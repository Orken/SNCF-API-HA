"""Tests for the Lovelace resource registration of the card."""

from types import SimpleNamespace

from custom_components.sncf_trains import (
    CARD_URL,
    LEGACY_CARD_URLS,
    _async_register_lovelace_resource,
)


class FakeResources:
    """Minimal stand-in for ResourceStorageCollection."""

    def __init__(self, items=None):
        self.loaded = True
        self.items = {item["id"]: dict(item) for item in items or []}
        self._next = 100

    def async_items(self):
        return list(self.items.values())

    async def async_load(self):
        self.loaded = True

    async def async_create_item(self, data):
        self._next += 1
        item_id = str(self._next)
        self.items[item_id] = {"id": item_id, "type": data["res_type"], "url": data["url"]}

    async def async_update_item(self, item_id, data):
        self.items[item_id].update(type=data["res_type"], url=data["url"])

    async def async_delete_item(self, item_id):
        del self.items[item_id]


def _hass(resources, mode="storage"):
    return SimpleNamespace(
        data={"lovelace": SimpleNamespace(resource_mode=mode, resources=resources)}
    )


async def test_resource_created_when_missing():
    res = FakeResources([{"id": "1", "url": "/hacsfiles/x.js", "type": "module"}])
    await _async_register_lovelace_resource(_hass(res), f"{CARD_URL}?v=2")
    urls = sorted(i["url"] for i in res.async_items())
    assert urls == ["/hacsfiles/x.js", f"{CARD_URL}?v=2"]


async def test_resource_version_updated_and_duplicates_removed():
    res = FakeResources(
        [
            {"id": "1", "url": f"{CARD_URL}?v=1", "type": "module"},
            {"id": "2", "url": CARD_URL, "type": "module"},
        ]
    )
    await _async_register_lovelace_resource(_hass(res), f"{CARD_URL}?v=2")
    assert [i["url"] for i in res.async_items()] == [f"{CARD_URL}?v=2"]


async def test_resource_unchanged_when_up_to_date():
    res = FakeResources([{"id": "1", "url": f"{CARD_URL}?v=2", "type": "module"}])
    await _async_register_lovelace_resource(_hass(res), f"{CARD_URL}?v=2")
    assert len(res.async_items()) == 1


async def test_yaml_mode_is_left_alone():
    res = FakeResources()
    await _async_register_lovelace_resource(_hass(res, mode="yaml"), f"{CARD_URL}?v=2")
    assert res.async_items() == []


async def test_legacy_card_resource_is_replaced():
    res = FakeResources(
        [
            {"id": "1", "url": f"{LEGACY_CARD_URLS[0]}?v=1.5.0", "type": "module"},
            {"id": "2", "url": "/hacsfiles/x.js", "type": "module"},
        ]
    )
    await _async_register_lovelace_resource(_hass(res), f"{CARD_URL}?v=2")
    urls = sorted(i["url"] for i in res.async_items())
    assert urls == ["/hacsfiles/x.js", f"{CARD_URL}?v=2"]
