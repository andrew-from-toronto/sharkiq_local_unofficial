"""Sensor platform for Shark IQ (Local)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfArea,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from sharklocal import codes as robot_codes
from sharklocal.models import VacuumMap

from .codes import advice, describe, end_reason
from .const import CONF_NAME, DOMAIN
from .coordinator import SharkCoordinator, SharkData
from .entity import SharkBaseEntity


@dataclass(frozen=True, kw_only=True)
class SharkSensorDescription(SensorEntityDescription):
    """Describes a Shark sensor."""

    value_fn: Callable[[SharkData], Any]
    attrs_fn: Callable[[SharkData], dict[str, Any] | None] | None = None
    mqtt_only: bool = False


def _last_job(data: SharkData) -> VacuumMap | None:
    return data.persisted_map


def _job_started(data: SharkData) -> datetime | None:
    job = _last_job(data)
    if job is None or not job.job_started:
        return None
    return datetime.fromtimestamp(job.job_started, UTC)


def _log_codes(data: SharkData, key: str) -> list[str] | None:
    """Codes logged under *key* in the last job's event log, in log order.

    The robot's clock can jump mid-job, so log order is the only reliable
    order. None when no persisted map has been seen yet.
    """
    job = _last_job(data)
    if job is None:
        return None
    return [entry.code for entry in job.log if entry.key == key]


def _last_code(data: SharkData, key: str) -> str | None:
    """The last code logged under *key*; "none" if the job logged none."""
    codes = _log_codes(data, key)
    if codes is None:
        return None
    if not codes:
        return "none"
    return end_reason(codes[-1]) if key == "DT_WFF_TERM_CODE" else codes[-1]


def _clean_mode(data: SharkData) -> str | None:
    """The last job's cleaning mode code: "Cleaning mode: ROOM_SELECTION" -> ROOM_SELECTION."""
    codes = _log_codes(data, "DT_CLEANING_MODE")
    if codes is None:
        return None
    if not codes:
        return "none"
    return codes[-1].split(":", 1)[-1].strip()


def _log_numbers(job: VacuumMap, predicate: Callable[[str], bool]) -> list[int]:
    return [int(e.code) for e in job.log if predicate(e.key) and e.code.lstrip("-").isdigit()]


def _job_details(data: SharkData) -> dict[str, Any] | None:
    """What the robot's log says about the last job, beyond area and time."""
    if (job := _last_job(data)) is None:
        return None
    battery = _log_numbers(job, lambda key: key == "DT_BATTERY_VALUE_AT_STATE_TRANS")
    return {
        "code": _clean_mode(data),
        "battery_start": battery[0] if battery else None,
        "battery_end": battery[-1] if battery else None,
        "bumper_hits": sum(_log_numbers(job, lambda k: k.startswith("DT_BUMPER_") and k.endswith("_COUNTER"))),
        "cliff_events": sum(_log_numbers(job, lambda k: k.startswith("DT_CLIFF_") and k.endswith("_COUNTER"))),
        "obstacles_avoided": sum(_log_numbers(job, lambda k: k.startswith("DT_IR_AVOID_") and k.endswith("_COUNTER"))),
        "docking_time": sum(_log_numbers(job, lambda k: k.endswith("_DOCK_TIME") and k.startswith("DT_")
                                         and k not in ("DT_TIME_BASE_DOCK", "DT_TIME_PP_DOCK"))),
    }


def _code_attrs(data: SharkData, key: str) -> dict[str, Any]:
    # The raw code is the stable thing for automations to match on.
    return {"code": _last_code(data, key)}


def _warning_attrs(data: SharkData) -> dict[str, Any]:
    codes = _log_codes(data, "DT_WARNING_CODE")
    return {
        **_code_attrs(data, "DT_WARNING_CODE"),
        "warnings": None if codes is None else [describe(code) for code in codes],
        "warning_codes": codes,
    }


def _live(codes: list[int] | None, table: dict[int, str]) -> list[str] | None:
    """Names of the codes active now, zero ("none") dropped, each once."""
    if codes is None:
        return None
    return list(dict.fromkeys(robot_codes.names(table, [c for c in codes if c])))


def _live_errors(data: SharkData) -> list[str] | None:
    return _live(data.status.errors, robot_codes.ERROR_CODES)


def _live_warnings(data: SharkData) -> list[str] | None:
    return _live(data.status.warnings, robot_codes.WARNING_CODES)


def _first_described(names: list[str] | None) -> str | None:
    if names is None:
        return None
    return describe(names[0]) if names else "None"


def _error_attrs(data: SharkData) -> dict[str, Any]:
    names = _live_errors(data) or []
    return {
        "code": names[0] if names else "ERROR_NONE",
        "codes": names,
        "errors": [describe(name) for name in names],
        "advice": advice(names[0]) if names else None,
    }


def _live_warning_attrs(data: SharkData) -> dict[str, Any]:
    names = _live_warnings(data) or []
    return {
        "code": names[0] if names else "WARN_NONE",
        "codes": names,
        "warnings": [describe(name) for name in names],
    }


def _named(value: int | None, table: dict[int, str]) -> str | None:
    if value is None:
        return None
    return table.get(value, f"UNKNOWN_{value}")


SENSORS: tuple[SharkSensorDescription, ...] = (
    SharkSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda data: data.status.battery_level,
    ),
    SharkSensorDescription(
        key="last_clean_area",
        translation_key="last_clean_area",
        device_class=SensorDeviceClass.AREA,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        suggested_display_precision=1,
        mqtt_only=True,
        value_fn=lambda data: (job := _last_job(data)) and job.cleaned_area,
    ),
    SharkSensorDescription(
        key="last_clean_duration",
        translation_key="last_clean_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        mqtt_only=True,
        value_fn=lambda data: (job := _last_job(data)) and job.clean_time,
    ),
    SharkSensorDescription(
        key="last_clean_start",
        translation_key="last_clean_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        mqtt_only=True,
        value_fn=_job_started,
    ),
    SharkSensorDescription(
        key="last_clean_mode",
        translation_key="last_clean_mode",
        mqtt_only=True,
        value_fn=lambda data: describe(_clean_mode(data)),
        attrs_fn=_job_details,
    ),
    # The robot's own event log arrives with the persisted map at the end of
    # every job; these are its fault and job-outcome codes.
    SharkSensorDescription(
        key="last_warning",
        translation_key="last_warning",
        entity_category=EntityCategory.DIAGNOSTIC,
        mqtt_only=True,
        value_fn=lambda data: describe(_last_code(data, "DT_WARNING_CODE")),
        attrs_fn=_warning_attrs,
    ),
    SharkSensorDescription(
        key="last_end_reason",
        translation_key="last_end_reason",
        entity_category=EntityCategory.DIAGNOSTIC,
        mqtt_only=True,
        value_fn=lambda data: describe(_last_code(data, "DT_WFF_TERM_CODE")),
        attrs_fn=lambda data: _code_attrs(data, "DT_WFF_TERM_CODE"),
    ),
    SharkSensorDescription(
        key="last_dock_reason",
        translation_key="last_dock_reason",
        entity_category=EntityCategory.DIAGNOSTIC,
        mqtt_only=True,
        value_fn=lambda data: describe(_last_code(data, "DT_DOCK_CODE")),
        attrs_fn=lambda data: _code_attrs(data, "DT_DOCK_CODE"),
    ),
    # Live, from every status frame.
    SharkSensorDescription(
        key="error",
        translation_key="error",
        mqtt_only=True,
        value_fn=lambda data: _first_described(_live_errors(data)),
        attrs_fn=_error_attrs,
    ),
    SharkSensorDescription(
        key="warning",
        translation_key="warning",
        entity_category=EntityCategory.DIAGNOSTIC,
        mqtt_only=True,
        value_fn=lambda data: _first_described(_live_warnings(data)),
        attrs_fn=_live_warning_attrs,
    ),
    SharkSensorDescription(
        key="robot_state",
        translation_key="robot_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        mqtt_only=True,
        value_fn=lambda data: describe(_named(data.status.state, robot_codes.SYSTEM_STATES)),
        attrs_fn=lambda data: {"code": _named(data.status.state, robot_codes.SYSTEM_STATES)},
    ),
    SharkSensorDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        mqtt_only=True,
        value_fn=lambda data: data.status.temperature,
    ),
    SharkSensorDescription(
        key="relocation",
        translation_key="relocation",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        mqtt_only=True,
        value_fn=lambda data: describe(_named(data.status.relocation, robot_codes.RELOCATION_STATES)),
        attrs_fn=lambda data: {"code": _named(data.status.relocation, robot_codes.RELOCATION_STATES)},
    ),
    *(
        SharkSensorDescription(
            key=key,
            translation_key=key,
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            mqtt_only=True,
            value_fn=lambda data, attr=attr: getattr(data.status, attr),
        )
        for key, attr in (
            ("suction_motor_speed", "fan_speed"),
            ("brushroll_speed", "brushroll_speed"),
            ("side_brush_speed", "side_brush_speed"),
        )
    ),
    SharkSensorDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.wifi.rssi if data.wifi else None,
    ),
    SharkSensorDescription(
        key="ssid",
        translation_key="ssid",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.wifi.ssid if data.wifi else None,
    ),
    SharkSensorDescription(
        key="ip_address",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.wifi.ip_address if data.wifi else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator: SharkCoordinator = hass.data[DOMAIN][entry.entry_id]
    name = entry.data[CONF_NAME]
    async_add_entities(
        SharkSensor(coordinator, name, desc)
        for desc in SENSORS
        if coordinator.use_mqtt or not desc.mqtt_only
    )


class SharkSensor(SharkBaseEntity, SensorEntity):
    """A sensor reading derived from the coordinator data."""

    entity_description: SharkSensorDescription

    def __init__(
        self,
        coordinator: SharkCoordinator,
        entry_title: str,
        description: SharkSensorDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry_title)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.unique_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes, if the description defines any."""
        if self.coordinator.data is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
