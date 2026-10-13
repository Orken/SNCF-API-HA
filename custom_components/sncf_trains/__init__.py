"""The SNCF Train integration."""

from pathlib import Path
from types import MappingProxyType
from logging import getLogger
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.entity_registry import Platform
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.helpers import config_validation as cv
from homeassistant.loader import async_get_integration

from .const import (
    CONF_API_KEY,
    CONF_ARRIVAL_NAME,
    CONF_DEPARTURE_NAME,
    CONF_FROM,
    CONF_TIME_END,
    CONF_TIME_START,
    CONF_TO,
    CONF_TRAIN_COUNT,
    DOMAIN,
)
from .coordinator import SncfUpdateCoordinator

# Pré-chargement de la plateforme diagnostics : sans cela Home Assistant
# l'importe à la demande dans la boucle d'événements et signale un appel
# bloquant (« Detected blocking call to import_module ») au démarrage.
from . import diagnostics  # noqa: F401

type SncfDataConfigEntry = ConfigEntry[SncfUpdateCoordinator]

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.CALENDAR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CARD_URL = "/sncf_trains/sncf-train-card.js"
CARD_FILE = Path(__file__).parent / "www" / "sncf-train-card.js"
LOGGER = getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up SNCF Trains component — register the Lovelace card.

    The card file is served right away and declared two ways:

    * as a Lovelace resource (storage mode), like HACS cards: the frontend
      fetches the resource list on every dashboard load, so the card is
      always there even when the browser serves a cached copy of the page;
    * through ``add_extra_js_url`` as a fallback (YAML resource mode).

    The URL carries the integration version so a new release is never
    served from the browser cache.
    """
    integration = await async_get_integration(hass, DOMAIN)
    card_url = f"{CARD_URL}?v={integration.version}"

    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_FILE), cache_headers=False)]
    )
    add_extra_js_url(hass, card_url)

    async def _register_resource(_event: Any = None) -> None:
        try:
            await _async_register_lovelace_resource(hass, card_url)
        except Exception:  # noqa: BLE001 - never block the integration
            LOGGER.warning(
                "Impossible d'enregistrer la carte comme ressource Lovelace ; "
                "ajoutez %s (module) dans les ressources du tableau de bord",
                card_url,
                exc_info=True,
            )

    if hass.state == CoreState.running:
        await _register_resource()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_resource)

    return True


async def _async_register_lovelace_resource(hass: HomeAssistant, card_url: str) -> None:
    """Create or update the card's Lovelace resource (storage mode only)."""
    lovelace = hass.data.get("lovelace")
    if lovelace is None or getattr(lovelace, "resource_mode", None) != "storage":
        return
    resources = lovelace.resources
    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True

    existing = [
        item
        for item in resources.async_items()
        if str(item.get("url", "")).split("?", 1)[0] == CARD_URL
    ]
    if not existing:
        await resources.async_create_item({"res_type": "module", "url": card_url})
        LOGGER.debug("Ressource Lovelace ajoutée : %s", card_url)
        return

    first, *duplicates = existing
    if first.get("url") != card_url:
        await resources.async_update_item(
            first["id"], {"res_type": "module", "url": card_url}
        )
        LOGGER.debug("Ressource Lovelace mise à jour : %s", card_url)
    for item in duplicates:
        await resources.async_delete_item(item["id"])


async def async_setup_entry(hass: HomeAssistant, entry: SncfDataConfigEntry) -> bool:
    """Set up SNCF Train as config entry."""
    coordinator = SncfUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: SncfDataConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: SncfDataConfigEntry) -> None:
    """Reload entry if change option."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: SncfDataConfigEntry) -> bool:
    """Migrate old config entries to move API key to options."""
    data = dict(entry.data)
    options = dict(entry.options)
    updated = False

    if CONF_API_KEY in options:
        data.setdefault(CONF_API_KEY, options.pop(CONF_API_KEY))
        updated = True

    if updated:
        time_start = options.pop(CONF_TIME_START)
        time_end = options.pop(CONF_TIME_END)
        train_count = options.pop(CONF_TRAIN_COUNT)
        from_ = data.pop(CONF_FROM)
        to_ = data.pop(CONF_TO)
        dep_name = data.pop(CONF_DEPARTURE_NAME)
        arr_name = data.pop(CONF_ARRIVAL_NAME)
        subentry_data = MappingProxyType(
            {
                CONF_FROM: from_,
                CONF_TO: to_,
                CONF_DEPARTURE_NAME: dep_name,
                CONF_ARRIVAL_NAME: arr_name,
                CONF_TIME_START: time_start,
                CONF_TIME_END: time_end,
                CONF_TRAIN_COUNT: train_count,
            }
        )

        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            unique_id="sncf_trains",
            title="Trains SNCF",
        )
        subentry = ConfigSubentry(
            title=f"Trajet: {dep_name} → {arr_name} ({time_start} - {time_end})",
            data=subentry_data,
            subentry_id=f"{entry.entry_id}_MIGRATE",
            subentry_type="train",
            unique_id=f"{from_}_{to_}_{time_start}_{time_end}",
        )
        hass.config_entries.async_add_subentry(entry, subentry)

    return True
