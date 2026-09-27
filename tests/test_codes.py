"""Readable names for event-log codes."""
from __future__ import annotations

import pytest

from custom_components.sharkiq_local_unofficial.codes import describe, end_reason


@pytest.mark.parametrize(
    "code, text",
    [
        ("WARN_MM_LOWLIGHT", "Low light for the camera"),
        ("DE_USR_CTR_DOCK", "Sent to dock by user"),
        ("NORMAL", "Finished normally"),
        ("none", "None"),
        # Never seen: tidied, not guessed at.
        ("WARN_BRUSH_STUCK", "Brush stuck"),
        ("DE_LOW_BATTERY", "Low battery"),
        ("SOMETHING_NEW", "Something new"),
        ("WARN_", "Warn"),
        # Not a code at all: shown as the robot wrote it.
        ("Zone: 2, weird", "Zone: 2, weird"),
        (None, None),
    ],
)
def test_describe(code, text):
    assert describe(code) == text


@pytest.mark.parametrize(
    "entry, reason",
    [
        ("Zone: 1, NORMAL", "NORMAL"),
        ("Zone:3,  STUCK ", "STUCK"),
        ("NORMAL", "NORMAL"),
        (None, None),
    ],
)
def test_end_reason(entry, reason):
    assert end_reason(entry) == reason
