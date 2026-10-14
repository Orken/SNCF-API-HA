"""Translation files must cover the same keys, with plain text only."""

import json
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent.parent


def _load(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


def _keys(data: dict, prefix: str = "") -> dict[str, str]:
    out = {}
    for key, value in data.items():
        if isinstance(value, dict):
            out.update(_keys(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


@pytest.mark.parametrize("name", ["translations/en.json", "translations/fr.json"])
def test_translation_keys_match_strings(name):
    assert set(_keys(_load(name))) == set(_keys(_load("strings.json")))


@pytest.mark.parametrize("name", ["translations/en.json", "translations/fr.json"])
def test_translations_have_no_key_references(name):
    # « [%key:…] » n'est résolu que pour les intégrations du cœur.
    assert not [k for k, v in _keys(_load(name)).items() if "[%key:" in v]
