"""Vacuum platform for Shark IQ (Local)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.vacuum import (
    Segment,
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from sharklocal import SharklocalError
from sharklocal.models import SuctionLevel, VacuumMode
from sharklocal.vacuum_map import spot_polygon

from .const import CONF_NAME, DOMAIN
from .coordinator import JobTarget, SharkCoordinator
from .entity import SharkBaseEntity

_LOGGER = logging.getLogger(__name__)


# sharklocal.VacuumMode → HA VacuumActivity
MODE_TO_ACTIVITY: dict[VacuumMode, VacuumActivity] = {
    VacuumMode.CLEANING: VacuumActivity.CLEANING,
    VacuumMode.RETURNING_TO_DOCK: VacuumActivity.RETURNING,
    VacuumMode.DOCKING: VacuumActivity.RETURNING,
    VacuumMode.DOCKED: VacuumActivity.DOCKED,
    VacuumMode.IDLE: VacuumActivity.IDLE,
    VacuumMode.EXPLORING: VacuumActivity.CLEANING,
}

FAN_SPEEDS = [level.value for level in SuctionLevel]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Shark vacuum entity."""
    coordinator: SharkCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SharkVacuum(coordinator, entry.data[CONF_NAME])])


def _segments(room_names: list[str]) -> list[Segment]:
    # The robot identifies rooms only by name, so the name is the segment id.
    return [Segment(id=name, name=name.strip()) for name in room_names]


class SharkVacuum(SharkBaseEntity, StateVacuumEntity, RestoreEntity):
    """Representation of a Shark IQ vacuum."""

    _attr_name = None  # uses device name
    _attr_fan_speed_list = FAN_SPEEDS

    # "stop" is the robot's return-to-dock command and no pause command has
    # been captured, so PAUSE is deliberately not offered.
    _BASE_FEATURES = (
        VacuumEntityFeature.STATE
        | VacuumEntityFeature.START
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
    )
    # Commands that exist only on the MQTT transport.
    _MQTT_FEATURES = (
        VacuumEntityFeature.FAN_SPEED
        | VacuumEntityFeature.LOCATE
        | VacuumEntityFeature.CLEAN_AREA
    )

    def __init__(self, coordinator: SharkCoordinator, entry_title: str) -> None:
        """Initialize."""
        super().__init__(coordinator, entry_title)
        self._attr_unique_id = f"{coordinator.unique_id}_vacuum"
        self._attr_supported_features = self._BASE_FEATURES
        if coordinator.use_mqtt:
            self._attr_supported_features |= self._MQTT_FEATURES
        self._segments_checked: list[str] | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last suction level set."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self.coordinator.restore_suction(last.attributes.get("fan_speed"))
        self._check_segments()

    @property
    def fan_speed(self) -> str | None:
        """The last suction level set (the robot never reports it)."""
        return self.coordinator.suction.value if self.coordinator.suction else None

    @callback
    def _handle_coordinator_update(self) -> None:
        self._check_segments()
        super()._handle_coordinator_update()

    @property
    def activity(self) -> VacuumActivity | None:
        """Return the current activity per the VacuumActivity enum."""
        if self.coordinator.data is None:
            return None
        status = self.coordinator.data.status
        return MODE_TO_ACTIVITY.get(status.mode, VacuumActivity.IDLE)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose useful info that doesn't fit the standard properties."""
        if self.coordinator.data is None:
            return {}
        status = self.coordinator.data.status
        attrs: dict[str, Any] = {
            "shark_mode": status.mode.value if status.mode else None,
            "charging": status.charging,
            "battery_level": status.battery_level,
        }
        if status.job_active is not None:
            attrs["matrix_clean"] = bool(status.job_active and status.deep_clean)
        if rooms := self._room_names():
            attrs["rooms"] = [name.strip() for name in rooms]
        return attrs

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _command(self, name: str, call: Any) -> None:
        """Await a client command, surfacing failures to the caller."""
        try:
            await call
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except SharklocalError as err:
            raise HomeAssistantError(
                f"{name} failed for {self.coordinator.host}: {err}"
            ) from err
        await self.coordinator.async_request_refresh()

    async def async_start(self) -> None:
        """Start cleaning."""
        await self._command("start_cleaning", self.coordinator.client.start_cleaning())
        self.coordinator.set_job_target(None)

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop cleaning (the robot returns to its dock)."""
        await self._command("stop", self.coordinator.client.stop())

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Return to dock."""
        await self._command("go_home", self.coordinator.client.go_home())

    async def async_locate(self, **kwargs: Any) -> None:
        """Play the locate sound."""
        await self._command("find_robot", self.coordinator.client.find_robot())

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set the suction level."""
        if fan_speed not in FAN_SPEEDS:
            raise ServiceValidationError(
                f"Unknown fan speed {fan_speed!r}; choose from {FAN_SPEEDS}"
            )
        await self._command(
            "set_suction", self.coordinator.async_set_suction(SuctionLevel(fan_speed))
        )

    # ------------------------------------------------------------------
    # Rooms
    # ------------------------------------------------------------------

    def _room_names(self) -> list[str]:
        data = self.coordinator.data
        if data is None or data.persisted_map is None:
            return []
        return [room.name for room in data.persisted_map.named_rooms]

    def resolve_rooms(self, requested: list[str]) -> list[str]:
        """Map requested names to the map's exact room names.

        The app lets rooms carry stray whitespace and punctuation ("Bathroom. ")
        that nobody will type, so matching ignores case, surrounding spaces and
        trailing dots — but the robot must be sent the name exactly as stored.
        """
        known = self._room_names()
        if not known:
            raise ServiceValidationError(
                "No room map yet; the robot publishes one when it next docks"
            )

        def key(name: str) -> str:
            return name.strip().rstrip(".").strip().casefold()

        by_key = {key(name): name for name in known}
        resolved: list[str] = []
        for name in requested:
            exact = name if name in known else by_key.get(key(name))
            if exact is None:
                raise ServiceValidationError(
                    f"Unknown room {name!r}; the map has "
                    f"{[k.strip() for k in known]}"
                )
            resolved.append(exact)
        return resolved

    async def async_clean_rooms(self, rooms: list[str], matrix: bool = False) -> None:
        """Clean the named rooms, optionally with a Matrix (two-pass) clean."""
        names = self.resolve_rooms(rooms)
        await self._command(
            "clean_rooms", self.coordinator.client.clean_rooms(names, deep=matrix)
        )
        self.coordinator.set_job_target(JobTarget(rooms=tuple(names)))

    async def async_clean_spot_at(self, x: float, y: float) -> None:
        """Spot-clean a ~1.5 m square around a point on the map, in metres."""
        await self._command("clean_spot", self.coordinator.client.clean_spot(x, y))
        self.coordinator.set_job_target(JobTarget(zone=tuple(spot_polygon(x, y))))

    async def async_get_segments(self) -> list[Segment]:
        """Return the rooms on the latest map, for HA's area mapping."""
        return _segments(self._room_names())

    async def async_clean_segments(self, segment_ids: list[str], **kwargs: Any) -> None:
        """Clean the rooms HA mapped to the requested areas."""
        await self.async_clean_rooms(segment_ids)

    @callback
    def _check_segments(self) -> None:
        """Raise HA's repair issue when the rooms no longer match the area mapping.

        Renaming a room in the app changes its id here, which would otherwise
        silently break the area mapping.
        """
        if (
            self.registry_entry is None
            or VacuumEntityFeature.CLEAN_AREA not in self.supported_features
        ):
            return
        names = self._room_names()
        if not names or names == self._segments_checked:
            return
        self._segments_checked = names
        last_seen = self.last_seen_segments
        if last_seen is not None and {s.id for s in last_seen} != set(names):
            self.async_create_segments_issue()
