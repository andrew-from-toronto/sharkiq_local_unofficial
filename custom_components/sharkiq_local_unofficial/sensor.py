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
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from sharklocal.models import VacuumMap

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
    codes = _log_codes(data, key)
    if codes is None:
        return None
    return codes[-1] if codes else "none"


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
        value_fn=lambda data: (job := _last_job(data)) and job.job_duration,
    ),
    SharkSensorDescription(
        key="last_clean_start",
        translation_key="last_clean_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        mqtt_only=True,
        value_fn=_job_started,
    ),
    # The robot's own event log arrives with the persisted map at the end of
    # every job; these are its fault and job-outcome codes.
    SharkSensorDescription(
        key="last_warning",
        translation_key="last_warning",
        entity_category=EntityCategory.DIAGNOSTIC,
        mqtt_only=True,
        value_fn=lambda data: _last_code(data, "DT_WARNING_CODE"),
        attrs_fn=lambda data: {"warnings": _log_codes(data, "DT_WARNING_CODE")},
    ),
    SharkSensorDescription(
        key="last_end_reason",
        translation_key="last_end_reason",
        entity_category=EntityCategory.DIAGNOSTIC,
        mqtt_only=True,
        value_fn=lambda data: _last_code(data, "DT_WFF_TERM_CODE"),
    ),
    SharkSensorDescription(
        key="last_dock_reason",
        translation_key="last_dock_reason",
        entity_category=EntityCategory.DIAGNOSTIC,
        mqtt_only=True,
        value_fn=lambda data: _last_code(data, "DT_DOCK_CODE"),
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
