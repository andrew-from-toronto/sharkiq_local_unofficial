"""Readable names for the codes in the robot's event log.

The log reports warnings, dock reasons and job endings as codes such as
``WARN_MM_LOWLIGHT`` or ``DE_USR_CTR_DOCK``. Codes seen from a real robot get
a plain description here; any other code is tidied mechanically (prefix
dropped, words in sentence case) rather than guessed at. Sensors keep the raw
code in a ``code`` attribute, which is what automations should match on.
"""
from __future__ import annotations

import re

# Codes observed in real event logs (RV2610BFCA), with what they meant.
KNOWN: dict[str, str] = {
    # Logged on every job in a dark room: the camera has too little light.
    "WARN_MM_LOWLIGHT": "Low light for the camera",
    # Sent home from the app, or by a dock / stop command.
    "DE_USR_CTR_DOCK": "Sent to dock by user",
    # DT_WFF_TERM_CODE's reason once "Zone: n, " is stripped.
    "NORMAL": "Finished normally",
    "none": "None",
}

# Category prefixes that say nothing a reader needs.
_PREFIXES = ("WARN_", "ERR_", "ERROR_", "DE_", "DT_", "SYS_ST_", "RS_")

_CODE = re.compile(r"^[A-Z0-9_]+$")
_ZONE_REASON = re.compile(r"^Zone:\s*\d+,\s*(?P<reason>.+)$")


def describe(code: str | None) -> str | None:
    """Return a readable description of a logged code."""
    if code is None:
        return None
    if code in KNOWN:
        return KNOWN[code]
    if not _CODE.match(code):
        return code
    words = code
    for prefix in _PREFIXES:
        if words.startswith(prefix) and len(words) > len(prefix):
            words = words[len(prefix):]
            break
    text = words.replace("_", " ").strip().lower()
    return text[:1].upper() + text[1:]


def end_reason(entry: str | None) -> str | None:
    """Reduce a ``DT_WFF_TERM_CODE`` entry ("Zone: 1, NORMAL") to its reason code."""
    if entry is None:
        return None
    match = _ZONE_REASON.match(entry)
    return match.group("reason").strip() if match else entry
