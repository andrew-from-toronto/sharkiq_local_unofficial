"""Every translation carries exactly the strings strings.json defines."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent / "custom_components" / "sharkiq_local_unofficial"


def _keys(data, prefix=""):
    for key, value in data.items():
        if isinstance(value, dict):
            yield from _keys(value, f"{prefix}{key}.")
        else:
            yield f"{prefix}{key}"


STRINGS = set(_keys(json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))))


@pytest.mark.parametrize("path", sorted((ROOT / "translations").glob("*.json")), ids=lambda p: p.stem)
def test_translation_matches_strings(path):
    assert set(_keys(json.loads(path.read_text(encoding="utf-8")))) == STRINGS


def test_english_is_strings_json():
    assert json.loads((ROOT / "translations" / "en.json").read_text(encoding="utf-8")) == json.loads(
        (ROOT / "strings.json").read_text(encoding="utf-8")
    )
