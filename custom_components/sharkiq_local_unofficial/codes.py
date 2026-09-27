"""Readable names for the codes in the robot's event log.

The log reports warnings, dock reasons and job endings as codes such as
``WARN_MM_LOWLIGHT`` or ``DE_USR_CTR_DOCK``. Codes seen from a real robot get
a plain description here; any other code is tidied mechanically (prefix
dropped, words in sentence case) rather than guessed at. Sensors keep the raw
code in a ``code`` attribute, which is what automations should match on.
"""
from __future__ import annotations

import re

# Faults (status field 5 / ErrorCodeT), with the SharkClean app's own text
# where it has one: a title and the advice it shows. Codes the app does not
# describe are paired by meaning with its older cloud codes; the rest fall back
# to a tidied name.
FAULTS: dict[str, tuple[str, str]] = {
    "ERROR_AED_NOT_FOUND": ("Cannot empty into the dock", "I am unable to empty debris into the dock dust bin. Please empty my dock dust bin and remove blockages."),
    "ERROR_AED_PROC_STUCK": ("Dust bin opening is blocked", "Please remove debris and blockages from my dust bin opening and from the debris intake opening on my dock."),
    "ERROR_BASE_DOCK_FAILED": ("Trouble returning to the base", "Please place the robot on the base."),
    "ERROR_BATTERY_ABNORMAL": ("Trouble with the battery", "The robot is having trouble with the battery. Please contact customer support so we can help resolve the issue. Error #8"),
    "ERROR_BATTERY_CRITICAL_LOW": ("Battery is too low", "Please place the robot on the base to recharge."),
    "ERROR_BATTERY_CRITICAL_LOW_IN_PAUSE": ("Battery is too low", "Please place the robot on the base to recharge."),
    "ERROR_BATTERY_LOW_TO_START": ("Battery too low to clean", "My battery level is too low for me to clean. Please charge my battery to at least 30% so I can clean."),
    "ERROR_BOUNDARY": ("Stuck on a BotBoundary", "Please move the robot to a new location."),
    "ERROR_BUMPER_START_FAIL": ("Front bumper is jammed", "Please remove debris or obstructions from the front bumper so the robot can move freely."),
    "ERROR_CANNOT_FINISH_CLEAN": ("Could not finish the clean", ""),
    "ERROR_CHARGE_VOL_ABNORMAL": ("Trouble charging", "Please check for anything blocking the metal charging contacts on the base or move it to a new outlet."),
    "ERROR_CLIFF_START_FAIL": ("Near a cliff", "Please move the robot to a level surface and clean cliff sensors."),
    "ERROR_DATA_BUMPER": ("Front bumper is jammed", "Please remove debris or obstructions from the front bumper so the robot can move freely."),
    "ERROR_DATA_CLIFF": ("Near a cliff", "Please move the robot to a level surface and clean cliff sensors."),
    "ERROR_DUST_UNINSTALLED": ("Dust cup not installed", "Please reinstall the dust cup until it clicks into place."),
    "ERROR_DUST_UNINSTALLED_IN_REDOCK": ("Dust cup not installed", "Please reinstall the dust cup until it clicks into place."),
    "ERROR_FANJET_BROKEN": ("Trouble with the CleanEdge vent", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_FAN_BROCKEN": ("Trouble with the suction fan", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_FAN_STUCK": ("Trouble with the suction motor", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_GNG_ZONE_CLEAN_FAILED": ("Zone clean failed", "Please place the robot on the base."),
    "ERROR_INIT_OPTIC": ("Trouble with a sensor", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_LIDAR_BROCKEN": ("Trouble with the lidar sensor", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_LIDAR_CRC_FAILED": ("Trouble with the lidar sensor", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_LIDAR_DATA_TIMEOUT": ("Trouble with the lidar sensor", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_LIDAR_INIT_FAILED": ("Trouble with the lidar sensor", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_LIDAR_MODEL_UNKNOWN": ("Trouble with the lidar sensor", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_LOW_LIGHT": ("Too dark to navigate", "Turn on a light in the room."),
    "ERROR_MAP_RELOC_FAILED": ("Lost its way", "I have lost my way and can't read my map. Please place me on the dock and initiate cleaning again."),
    "ERROR_MFD_BOILER_ABNORMAL": ("Trouble washing the mop pad", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_MFD_BOILER_NTC_ABNORMAL": ("Trouble washing the mop pad", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_MFD_DETACH_MOTOR_BROKEN": ("Trouble with mop pad", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_MFD_DRY_FAN_BROKEN": ("Trouble drying the mop pad", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_MFD_GREY_BROKEN": ("Trouble washing the mop pad", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_MFD_HEATER_ABNORMAL": ("Trouble drying the mop pad", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_MFD_HEATER_NTC_ABNORMAL": ("Trouble drying the mop pad", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_MFD_LIMIT_SWITCH_BROKEN": ("Trouble washing and drying the mop pad", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_MFD_PUMP_BROKEN": ("Trouble refilling", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_MFD_SCALING_NTC_ABNORMAL": ("Trouble with one of the sensors", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_MFD_SHUTTLE_BROKEN": ("Trouble washing the mop pad", ""),
    "ERROR_MFD_VALVE_BROKEN": ("Trouble refilling and washing the mop pad", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_MFD_VALVE_OVER_CURRENT": ("Trouble refilling and washing the mop pad", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_NOGO_ESCAPE_TIMEOUT": ("Stuck", "Please move the robot to a level surface. If error persists, restart the robot or contact customer support."),
    "ERROR_NO_DOCK_SIGNAL": ("Trouble returning to the base", "Please place the robot on the base."),
    "ERROR_PICK_UP": ("Picked up", "Put the robot back on the floor."),
    "ERROR_PP_ESCAPE_FAILED": ("Stuck", "Please move the robot to a level surface. If error persists, restart the robot or contact customer support."),
    "ERROR_PUMP_BROCKEN": ("Trouble with the water tank", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_RFRONT_WHEEL_BROCKEN": ("Brushroll is stuck", "Please remove it and clean off any hair and debris."),
    "ERROR_RFRONT_WHEEL_STUCK": ("Brushroll is stuck", "Please remove it and clean off any hair and debris."),
    "ERROR_ROBOT_STUCK": ("Stuck", "Please move the robot to a level surface. If error persists, restart the robot or contact customer support."),
    "ERROR_SFRONT_WHEEL_BROCKEN": ("Side brush is stuck", "Please remove any hair and debris."),
    "ERROR_SFRONT_WHEEL_STUCK_BOTH": ("Side brush is stuck", "Please remove any hair and debris."),
    "ERROR_SFRONT_WHEEL_STUCK_L": ("Side brush is stuck", "Please remove any hair and debris."),
    "ERROR_SFRONT_WHEEL_STUCK_R": ("Side brush is stuck", "Please remove any hair and debris."),
    "ERROR_TANK_UNINSTALLED": ("Tank is missing", "Please reinstall the water tank. Be sure to slide it on until it clicks into place."),
    "ERROR_UFD_INIT_FAILED": ("Trouble with the floor detect sensor", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_UFD_MODEL_UNKNOWN": ("Trouble with the floor detect sensor", "Please turn the robot off and then on again. If error persists, contact customer support."),
    "ERROR_WHEEL_DROP_BOTH": ("Wheel is on an uneven surface", "Please move the robot to a level surface and check the wheels for obstructions."),
    "ERROR_WHEEL_DROP_L": ("Wheel is on an uneven surface", "Please move the robot to a level surface and check the wheels for obstructions."),
    "ERROR_WHEEL_DROP_R": ("Wheel is on an uneven surface", "Please move the robot to a level surface and check the wheels for obstructions."),
    "ERROR_WHEEL_DROP_START_FAIL": ("Wheel is on an uneven surface", "Please move the robot to a level surface and check the wheels for obstructions."),
    "ERROR_WHEEL_STUCK_BOTH": ("Wheel is stuck", "Please clean the wheels and remove any debris."),
    "ERROR_WHEEL_STUCK_L": ("Wheel is stuck", "Please clean the wheels and remove any debris."),
    "ERROR_WHEEL_STUCK_R": ("Wheel is stuck", "Please clean the wheels and remove any debris."),
}

# Warnings (field 6 / WarningCodeT, and DT_WARNING_CODE in the log). The app
# has no text for these (it shows only WARN_CLEANSENSE_DIRT, as a dialog).
WARNINGS: dict[str, str] = {
    # WARN_MM_* repeat the map manager's MMStatus (DT_MM_STATUS): its verdict on
    # whether the run's map was used to update the saved one. Bookkeeping, not
    # faults; the meanings are read from the names and PbMapSummary's matching
    # flags, as nothing in the app explains them. WARN_MM_LOWLIGHT is logged by
    # a lidar robot too, with no camera to be short of light.
    "WARN_MM_ALLRIGHT": "Map updated",
    "WARN_MM_FIRSTCLEAN": "First map made",
    "WARN_MM_TRAINING": "Map still being learned",
    "WARN_MM_NOTTRAINING": "Map not being learned",
    "WARN_MM_AREASMALL": "Map not updated: area too small",
    "WARN_MM_RESETFLOOR1": "Map reset",
    "WARN_MM_RESETFLOOR2": "Map reset",
    "WARN_MM_SIMILARCHECK1": "Map compared with the saved one",
    "WARN_MM_SIMILARCHECK2": "Map compared with the saved one",
    "WARN_MM_SIMILARCHECK3": "Map compared with the saved one",
    "WARN_MM_REPLACESUCC": "Saved map replaced",
    "WARN_MM_NOTFROMSTATION": "Map not updated: did not start from the dock",
    "WARN_MM_LOWLIGHT": "Map not updated: low light",
    "WARN_MM_NOTFINISH": "Map not updated: job not finished",
    "WARN_MM_NOTEFFIENCY": "Map not updated: run too slow for its area",
    "WARN_MM_NEW_FLOOR": "New floor mapped",
    "WARN_MM_UPDATE_FLOOR": "Floor map updated",
    "WARN_MM_UPDATE_SLAM": "Navigation map updated",
    "WARNING_CHARGING_INTERRUPTED": "Charging interrupted",
    "WARN_BAD_RUN": "Run did not go well",
    "WARN_BATTERY_LOW": "Battery low",
    "WARN_DANGEROUS_ZONE": "In a hazardous area",
    "WARN_DUST_FULL": "Dust bin full",
    "WARN_ESCAPE_FAILED": "Could not get free",
    "WARN_EVAUCATION_RESUME_CLOSE": "Evac & Resume turned off",
    "WARN_EVAUCATION_RESUME_OPEN": "Evac & Resume turned on",
    "WARN_FIND_ME": "Locating",
    "WARN_INCLINE": "On an incline",
    "WARN_LENS_DIRTY": "Camera lens dirty",  # this and the two *_LIGHT: the camera models' visual tracker
    "WARN_LIDAR_BROKEN": "Lidar sensor problem",
    "WARN_LOW_LIGHT": "Low light",
    "WARN_MAP_SYNC_FAILED": "Map sync failed",
    "WARN_NO_LIGHT": "Too dark",
    "WARN_OVER_CURRENT": "Motor overloaded",
    "WARN_PICK_UP": "Picked up",
    "WARN_PP_NO_DOCK": "Cannot find the dock",
    "WARN_RBR_NOT_COVER_ALL": "Room-by-room clean missed some areas",
    "WARN_RECHARGE_RESUME_CLOSE": "Recharge & Resume turned off",
    "WARN_RECHARGE_RESUME_OPEN": "Recharge & Resume turned on",
    "WARN_RELOC_FAILED_WITH_NOGO": "Could not find its position near a no-go zone",
    "WARN_SAVE_MAP_BUSY": "Busy saving the map",
    "WARN_TANK_EMPTY": "Water tank empty",
    "WARN_TANK_LOW": "Water tank low",
    "WARN_VT_LOST_LOCALIZATION": "Lost its position",
    "WARN_VT_RELOC_FAILED": "Could not find its position",
    "WARN_WHEEL_FLY": "Wheel lifted",
    "WARN_WHEEL_OVER_CURRENT": "Wheel overloaded",
    "WARN_WHEEL_SLIP": "Wheel slipping",
}

# Why the robot docked (DT_DOCK_CODE).
DOCK_REASONS: dict[str, str] = {
    "DE_2R": "Recharge & Resume",
    "DE_BAT_LOW": "Battery low",
    "DE_BAT_LOW_DC": "Battery low",
    "DE_CLEAN_FINISH": "Clean finished",
    "DE_CLEAN_FINISH_DC": "Clean finished",
    "DE_ER": "After an error",
    "DE_ESC_FAILED": "Could not get free",
    "DE_NONE": "None",
    "DE_REDOCK": "Returning again",
    "DE_RELOC_FAILED": "Lost its position",
    "DE_USR_CTR_DOCK": "Sent to dock by user",
}

# The robot's system state (field 4).
STATES: dict[str, str] = {
    "SYS_ST_ALONG_WALL": "Edge cleaning",
    "SYS_ST_AUTO_EXPLORE": "Exploring",
    "SYS_ST_BASE_DOCK": "Docking",
    "SYS_ST_BATTERY_FULL": "Charged",
    "SYS_ST_CARPET_EXPLORE": "Exploring carpets",
    "SYS_ST_CHARGING": "Charging",
    "SYS_ST_CLEANING": "Cleaning",
    "SYS_ST_CLEAN_ZONE": "Zone cleaning",
    "SYS_ST_DOCKING": "Returning to dock",
    "SYS_ST_ERROR": "Error",
    "SYS_ST_EXPLORE_DOCKING": "Returning after exploring",
    "SYS_ST_GO_TARGET": "Going to a spot",
    "SYS_ST_PAUSE": "Paused",
    "SYS_ST_POWEROFF": "Powering off",
    "SYS_ST_REBOOT": "Restarting",
    "SYS_ST_REDOCKING": "Returning to dock again",
    "SYS_ST_RELOCAL": "Finding its position",
    "SYS_ST_REMOTE": "Remote control",
    "SYS_ST_SLEEPING": "Sleeping",
    "SYS_ST_SPOTING": "Spot cleaning",
    "SYS_ST_UPDATING": "Updating",
    "SYS_ST_WAITING": "Waiting",
    "SYS_ST_WIFI_CONFIG": "Wi-Fi setup",
}

# Codes seen in real event logs that belong to no family above.
KNOWN: dict[str, str] = {
    # DT_WFF_TERM_CODE's reason once "Zone: n, " is stripped.
    "NORMAL": "Finished normally",
    # Cleaning modes this integration derives from the report (sensor.py).
    "WHOLE_HOME": "Whole home",
    "SPOT": "Spot",
    "none": "None",
    "ERROR_NONE": "None",
    "WARN_NONE": "None",
    # Relocation (status field 41).
    "RS_NONE": "None",
    "RS_RELOC": "Finding its position",
    "RS_FAIL": "Could not find its position",
    "RS_SUCCESS": "Knows where it is",
    "RS_UNRELOC": "Position unknown",
}

# Category prefixes that say nothing a reader needs.
_PREFIXES = ("WARN_", "ERR_", "ERROR_", "DE_", "DT_", "SYS_ST_", "RS_")

_CODE = re.compile(r"^[A-Z0-9_]+$")
_ZONE_REASON = re.compile(r"^Zone:\s*\d+,\s*(?P<reason>.+)$")


def describe(code: str | None) -> str | None:
    """Return a readable description of a logged code."""
    if code is None:
        return None
    for table in (KNOWN, WARNINGS, DOCK_REASONS, STATES):
        if code in table:
            return table[code]
    if code in FAULTS:
        return FAULTS[code][0]
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


def advice(code: str | None) -> str | None:
    """What the app tells the owner to do about a fault, if it says."""
    if code is None or code not in FAULTS:
        return None
    return FAULTS[code][1] or None
