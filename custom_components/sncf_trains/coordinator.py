"""Data Update Coordinator."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SncfApiClient, SncfInvalidJourneyError, SncfQuotaError
from .const import (
    CONF_API_KEY,
    CONF_ARRIVAL_NAME,
    CONF_DEPARTURE_NAME,
    CONF_FROM,
    CONF_OUTSIDE_INTERVAL,
    CONF_TIME_END,
    CONF_TIME_START,
    CONF_TO,
    CONF_TRAIN_COUNT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_OUTSIDE_INTERVAL,
    DEFAULT_TRAIN_COUNT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_TRAIN_COUNT,
)
from .disruptions import (
    mark_destination_deleted,
    merge_with_schedule,
    planned_departure,
)
from .helpers import parse_datetime, short_station_name
from .tracking import EVENT_STATUS_CHANGED, detect_changes

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Nombre de mises à jour consécutives en échec avant de signaler une
# réparation « API injoignable » (évite les alertes sur un incident bref).
FAILURES_BEFORE_ISSUE = 3

# Nombre de trajets demandés à l'API par appel.
JOURNEY_COUNT = MAX_TRAIN_COUNT


class SncfUpdateCoordinator(DataUpdateCoordinator):
    """Coordonnateur pour récupérer les données des trajets SNCF."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        """Initialisation."""
        self.entry = entry
        self.api_client = None

        # Dernier statut connu de chaque train (clé : train_uid), persisté
        # pour ne pas ré-annoncer les mêmes changements après un redémarrage.
        self.train_states: dict[str, dict[str, Any]] = {}
        self._store: Store | None = None

        # Plage en cours ou à venir de chaque trajet : (début, fin). La fin
        # dépend des trains (départ du dernier train affiché).
        self._windows: dict[str, tuple[datetime, datetime]] = {}

        # Échecs réseau/quota consécutifs par trajet.
        self._failures: dict[str, int] = {}

        self.update_interval_minutes = entry.options.get(
            CONF_UPDATE_INTERVAL,
            DEFAULT_UPDATE_INTERVAL,
        )

        self.outside_interval_minutes = entry.options.get(
            CONF_OUTSIDE_INTERVAL,
            DEFAULT_OUTSIDE_INTERVAL,
        )

        super().__init__(
            hass,
            _LOGGER,
            name="SNCF Train Journeys",
            update_interval=timedelta(minutes=self.update_interval_minutes),
        )

    async def _async_setup(self) -> None:
        """Paramétrage du coordinateur."""
        api_key = self.entry.data[CONF_API_KEY]

        self._store = Store(
            self.hass,
            STORAGE_VERSION,
            f"sncf_trains.{self.entry.entry_id}.train_states",
        )
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self.train_states = stored

        try:
            session = async_get_clientsession(self.hass)
            self.api_client = SncfApiClient(session, api_key)

        except Exception as err:
            if "401" in str(err) or "403" in str(err):
                raise ConfigEntryAuthFailed("Clé API invalide ou expirée") from err

            _LOGGER.error(
                "Erreur lors de la récupération des trajets SNCF: %s",
                err,
            )
            raise UpdateFailed(err) from err

    def _window_end(self, time_start, time_end) -> datetime:
        """Fin de la plage horaire correspondant au paramètre datetime envoyé.

        Même logique de jour que `_build_datetime_param` : si la plage du
        jour est passée, on regarde celle du lendemain. Une plage qui
        traverse minuit (fin <= début) se termine le lendemain du début.
        """
        return self._fixed_end(
            self._parse_start(self._build_datetime_param(time_start, time_end)),
            time_end,
        )

    @staticmethod
    def _now() -> datetime:
        """Heure courante, toujours avec fuseau horaire."""
        now = dt_util.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=dt_util.get_default_time_zone())
        return now

    @staticmethod
    def _parse_start(datetime_str: str) -> datetime:
        """Paramètre datetime de l'API → datetime avec fuseau horaire."""
        tz = dt_util.now().tzinfo or dt_util.get_default_time_zone()
        return datetime.strptime(datetime_str, "%Y%m%dT%H%M%S").replace(tzinfo=tz)

    @staticmethod
    def _fixed_end(start: datetime, time_end: str) -> datetime:
        """Heure de fin de la plage qui commence à `start`.

        Une plage qui traverse minuit (fin <= début) se termine le
        lendemain de son début.
        """
        h_end, m_end = map(int, time_end.split(":"))
        end = start.replace(hour=h_end, minute=m_end)
        if end <= start:
            end += timedelta(days=1)
        return end

    @staticmethod
    def _next_start(time_start: str, after: datetime) -> datetime:
        """Premier début de plage strictement après `after`."""
        h_start, m_start = map(int, time_start.split(":"))
        start = after.replace(hour=h_start, minute=m_start, second=0, microsecond=0)
        if start <= after:
            start += timedelta(days=1)
        return start

    def _initial_start(self, time_start: str, time_end: str | None) -> datetime:
        """Début de plage à interroger quand aucune plage n'est connue.

        Sans heure de fin, la fin de plage dépend des trains : on part du
        dernier début passé (la plage est peut-être encore en cours).
        """
        if time_end:
            return self._parse_start(self._build_datetime_param(time_start, time_end))
        now = self._now().replace(second=0, microsecond=0)
        return self._next_start(time_start, now) - timedelta(days=1)

    @staticmethod
    def _window_end_from_trains(
        start: datetime,
        journeys: list[dict[str, Any]],
        train_count: int,
        fixed_end: datetime | None,
    ) -> datetime:
        """Fin de plage : la première limite atteinte.

        La plage se termine au départ du dernier des `train_count` trains
        affichés, sans dépasser l'heure de fin si elle est renseignée.
        Sans heure de fin et avec moins de trains que demandé, elle se
        termine au départ du dernier train (ou tout de suite s'il n'y en a
        aucun).
        """
        departures = [
            dep
            for journey in journeys[:train_count]
            if (
                dep := parse_datetime(journey.get("departure_date_time", ""))
                or planned_departure(journey)
            )
            is not None
        ]
        last = max(departures, default=None)

        if fixed_end is not None:
            if last is None or len(journeys) < train_count:
                return fixed_end
            return min(last, fixed_end)

        return last if last is not None else start

    @staticmethod
    def _filter_by_window(
        journeys: list[dict[str, Any]],
        window_end: datetime,
    ) -> list[dict[str, Any]]:
        """Garde les trains dont le départ prévu est dans la plage horaire.

        Le début de plage est déjà appliqué par l'API (paramètre datetime) ;
        on retire ici les trains qui partent après la fin de plage. Un
        trajet sans date exploitable est conservé.
        """
        kept = []
        for journey in journeys:
            dep = planned_departure(journey)
            if dep is not None and dep.tzinfo is None:
                dep = dep.replace(tzinfo=window_end.tzinfo)
            if dep is None or dep <= window_end:
                kept.append(journey)
        return kept

    def _build_datetime_param(self, time_start, time_end) -> str:
        """Construit le paramètre datetime pour l'API.

        Renvoie le début de la plage en cours ou à venir. Une plage qui
        traverse minuit (fin <= début) se termine le lendemain de son début.
        """
        now = dt_util.now()

        h_start, m_start = map(int, time_start.split(":"))
        h_end, m_end = map(int, time_end.split(":"))

        dt_start = now.replace(
            hour=h_start,
            minute=m_start,
            second=0,
            microsecond=0,
        )

        dt_end = dt_start.replace(hour=h_end, minute=m_end)

        if (h_end, m_end) <= (h_start, m_start):
            dt_end += timedelta(days=1)

            # Plage de la veille encore en cours (ex. 00:30 pour 22:00-01:00)
            if now < dt_end - timedelta(days=1):
                dt_start -= timedelta(days=1)
                dt_end -= timedelta(days=1)

        if now > dt_end:
            dt_start += timedelta(days=1)

        return dt_start.strftime("%Y%m%dT%H%M%S")

    def _adjust_update_interval(
        self,
        start: datetime,
        end: datetime,
    ) -> timedelta | None:
        """Ajuste la fréquence selon la plage horaire."""
        now = self._now()

        pre_start = start - timedelta(hours=1)

        in_fast_mode = pre_start <= now <= end

        interval_minutes = (
            self.update_interval_minutes
            if in_fast_mode
            else self.outside_interval_minutes
        )

        new_interval = timedelta(minutes=interval_minutes)

        if self.update_interval != new_interval:
            _LOGGER.debug(
                "Update interval: %s → %s minutes",
                (
                    None
                    if self.update_interval is None
                    else self.update_interval.total_seconds() / 60
                ),
                interval_minutes,
            )

        return new_interval

    async def _fetch_base_schedule(
        self,
        departure: str,
        arrival: str,
        datetime_str: str,
        title: str,
    ) -> list[dict[str, Any]] | None:
        """Fetch the planned timetable, without transfers. Never raises."""
        try:
            journeys = await self.api_client.fetch_journeys(
                departure,
                arrival,
                datetime_str,
                count=JOURNEY_COUNT,
                data_freshness="base_schedule",
            )
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:  # noqa: BLE001 - best effort, see caller
            _LOGGER.debug(
                "Horaires théoriques indisponibles pour '%s' : %s",
                title,
                err,
            )
            return None

        if not isinstance(journeys, list):
            return None

        return [
            journey
            for journey in journeys
            if isinstance(journey, dict) and journey.get("nb_transfers", 0) == 0
        ]

    async def _fetch_trip(
        self,
        subentry_id: str,
        entry: Any,
        start: datetime,
        window_end: datetime | None,
    ) -> list[dict[str, Any]] | None:
        """Trains directs d'un trajet à partir de `start`.

        Renvoie None si l'API n'a pas répondu (on garde alors les données
        précédentes), une liste vide si le trajet est refusé.
        """
        max_retries = 3
        retry_delay = 2

        departure = entry.data[CONF_FROM]
        arrival = entry.data[CONF_TO]
        datetime_str = start.strftime("%Y%m%dT%H%M%S")

        journeys = None
        last_error = ""
        invalid: SncfInvalidJourneyError | None = None

        # ---------------------------------------------------------
        # Appel API avec retry
        # ---------------------------------------------------------
        for attempt in range(1, max_retries + 1):
            try:
                journeys = await self.api_client.fetch_journeys(
                    departure,
                    arrival,
                    datetime_str,
                    count=JOURNEY_COUNT,
                )

                if journeys is not None:
                    break

            except SncfInvalidJourneyError as err:
                # Inutile de réessayer : le trajet lui-même est refusé.
                invalid = err
                break

            except (TimeoutError, ClientError, RuntimeError) as err:
                last_error = (
                    "quota" if isinstance(err, SncfQuotaError) else str(err)
                )
                _LOGGER.warning(
                    "Erreur réseau lors de la récupération des trajets "
                    "(tentative %d/%d) : %s",
                    attempt,
                    max_retries,
                    err,
                )

            if attempt < max_retries:
                await asyncio.sleep(retry_delay)

        # ---------------------------------------------------------
        # Vérification de la réponse API
        # ---------------------------------------------------------
        if invalid is not None:
            _LOGGER.warning(
                "Trajet '%s' refusé par l'API SNCF (%s) : %s",
                entry.title,
                invalid.error_id,
                invalid.message,
            )
            self._raise_issue(
                f"invalid_journey_{subentry_id}",
                "invalid_journey",
                ir.IssueSeverity.ERROR,
                {
                    "title": entry.title,
                    "reason": invalid.message or invalid.error_id,
                },
            )
            return []

        self._clear_issue(f"invalid_journey_{subentry_id}")

        if journeys is None or not isinstance(journeys, list):
            failures = self._failures.get(subentry_id, 0) + 1
            self._failures[subentry_id] = failures
            _LOGGER.warning(
                "Aucune donnée reçue de l'API SNCF pour le trajet '%s' "
                "(%d échec(s) consécutif(s))",
                entry.title,
                failures,
            )
            if failures >= FAILURES_BEFORE_ISSUE:
                self._raise_issue(
                    "api_unavailable",
                    "quota_exceeded"
                    if last_error == "quota"
                    else "api_unavailable",
                    ir.IssueSeverity.WARNING,
                    {"title": entry.title},
                )
            return None

        self._failures[subentry_id] = 0

        _LOGGER.debug(
            "Trajet '%s' : %d journey(s) reçu(s) depuis l'API",
            entry.title,
            len(journeys),
        )

        # ---------------------------------------------------------
        # Filtrage des trajets
        #
        # On ne teste plus le nombre de sections.
        #
        # Un trajet direct peut maintenant être représenté par :
        #
        #   crow_fly
        #   public_transport
        #   crow_fly
        #
        # tout en ayant nb_transfers = 0.
        #
        # nb_transfers est donc le critère approprié pour
        # éliminer les trajets avec correspondance.
        # ---------------------------------------------------------
        filtered_journeys = []

        for journey in journeys:
            if not isinstance(journey, dict):
                continue

            if journey.get("nb_transfers", 0) == 0:
                filtered_journeys.append(journey)

        _LOGGER.debug(
            "Trajet '%s' : %d journey(s) conservé(s) après filtrage "
            "(sans correspondance)",
            entry.title,
            len(filtered_journeys),
        )

        # ---------------------------------------------------------
        # Trains supprimés
        #
        # En temps réel, l'API retire les trains supprimés au lieu
        # de les signaler. On compare donc avec les horaires
        # théoriques : un train prévu mais absent du temps réel est
        # marqué comme supprimé. Appel « best effort » : en cas
        # d'échec on garde simplement les données temps réel.
        # ---------------------------------------------------------
        base_journeys = await self._fetch_base_schedule(
            departure,
            arrival,
            datetime_str,
            entry.title,
        )

        merged = merge_with_schedule(
            filtered_journeys,
            base_journeys,
            arrival,
            window_end=window_end,
            # Moins de résultats que demandé : rien n'a été tronqué.
            realtime_exhaustive=len(journeys) < JOURNEY_COUNT,
        )

        # Train qui ne dessert plus la gare d'arrivée : information
        # seulement (il peut s'arrêter avant), pas une suppression.
        mark_destination_deleted(merged, arrival)

        # ---------------------------------------------------------
        # Plage horaire : on ne garde que les trains qui partent
        # avant l'heure de fin configurée.
        # ---------------------------------------------------------
        if window_end is None:
            return merged
        return self._filter_by_window(merged, window_end)

    async def _async_update_data(self) -> dict[str, Any]:
        """Récupère les données de l'API SNCF."""

        if not self.entry.subentries:
            _LOGGER.warning("Pas de subentries configurés")
            return {}

        update_intervals = []
        trains = {}

        for subentry_id, entry in self.entry.subentries.items():
            _LOGGER.debug(
                "Traitement du trajet : %s",
                entry.title,
            )

            time_start = entry.data[CONF_TIME_START]
            # Heure de fin facultative : sans elle, la plage se termine au
            # départ du dernier des trains demandés.
            time_end = entry.data.get(CONF_TIME_END) or None
            train_count = entry.data.get(CONF_TRAIN_COUNT, DEFAULT_TRAIN_COUNT)

            now = self._now()
            known = self._windows.get(subentry_id)
            if known is None:
                start = self._initial_start(time_start, time_end)
            elif now <= known[1]:
                start = known[0]
            else:
                start = self._next_start(time_start, known[1])

            journeys = None
            # Deux essais au plus : si la plage interrogée est déjà finie
            # (dernier train parti), on passe à la suivante.
            for _ in range(2):
                fixed_end = self._fixed_end(start, time_end) if time_end else None
                journeys = await self._fetch_trip(subentry_id, entry, start, fixed_end)
                if journeys is None:
                    break
                journeys = journeys[:train_count]
                end = self._window_end_from_trains(
                    start, journeys, train_count, fixed_end
                )
                self._windows[subentry_id] = (start, end)
                if now <= end:
                    break
                start = self._next_start(time_start, end)

            if journeys is not None:
                trains[subentry_id] = journeys
            else:
                # On garde les dernières données connues plutôt que de vider
                # les capteurs pendant un incident passager.
                previous = (self.data or {}).get(subentry_id)
                if previous is not None:
                    trains[subentry_id] = previous

            window = self._windows.get(subentry_id)
            if window is not None:
                update_intervals.append(self._adjust_update_interval(*window))

        # -------------------------------------------------------------
        # Mise à jour de l'intervalle
        # -------------------------------------------------------------
        if update_intervals:
            new_interval = min(update_intervals)

            if self.update_interval != new_interval:
                self.update_interval = new_interval

                _LOGGER.debug(
                    "Coordinator update interval set to %s minutes",
                    self.update_interval.total_seconds() / 60,
                )

        if not any(self._failures.values()):
            self._clear_issue("api_unavailable")

        self._track_changes(trains)

        return trains

    def _raise_issue(
        self,
        issue_id: str,
        translation_key: str,
        severity: ir.IssueSeverity,
        placeholders: dict[str, str],
    ) -> None:
        """Signale un problème dans Paramètres → Réparations."""
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=severity,
            translation_key=translation_key,
            translation_placeholders=placeholders,
        )

    def _clear_issue(self, issue_id: str) -> None:
        """Retire un problème résolu de Paramètres → Réparations."""
        ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _track_changes(self, trains: dict[str, list[dict[str, Any]]]) -> None:
        """Émet un événement par train dont le statut a changé."""
        trip_info = {}
        for subentry_id in trains:
            subentry = self.entry.subentries.get(subentry_id)
            data = getattr(subentry, "data", {}) or {}
            trip_info[subentry_id] = {
                "trip_title": getattr(subentry, "title", ""),
                "departure_name": short_station_name(data.get(CONF_DEPARTURE_NAME, "")),
                "arrival_name": short_station_name(data.get(CONF_ARRIVAL_NAME, "")),
            }

        now = dt_util.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=dt_util.get_default_time_zone())

        events, self.train_states = detect_changes(
            self.train_states,
            trains,
            trip_info,
            now,
        )

        for event in events:
            _LOGGER.debug("Changement de statut : %s", event)
            self.hass.bus.async_fire(EVENT_STATUS_CHANGED, event)

        if self._store is not None:
            self._store.async_delay_save(lambda: self.train_states, 10)
