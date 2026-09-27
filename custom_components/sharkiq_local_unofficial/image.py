"""Image platform for Shark IQ (Local): the live map."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from sharklocal.models import VacuumMode

from sharklocal.models import VacuumMap

from .const import CONF_NAME, DOMAIN
from .coordinator import JobTarget, SharkCoordinator
from .entity import SharkBaseEntity
from .map_render import Canvas, render_map


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the map image (MQTT only: maps are never served over REST)."""
    coordinator: SharkCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.use_mqtt and coordinator.capabilities.has_map:
        async_add_entities([SharkMapImage(coordinator, entry.data[CONF_NAME])])


class SharkMapImage(SharkBaseEntity, ImageEntity):
    """The map as the robot last published it, with the path cleaned so far.

    During a job the robot publishes a live frame every few seconds, so the
    picture follows the robot; between jobs it shows the persisted map.
    """

    _attr_content_type = "image/png"
    _attr_translation_key = "map"

    def __init__(self, coordinator: SharkCoordinator, entry_title: str) -> None:
        """Initialize."""
        SharkBaseEntity.__init__(self, coordinator, entry_title)
        ImageEntity.__init__(self, coordinator.hass)
        self._attr_unique_id = f"{coordinator.unique_id}_map"
        self._drawn: _Picture | None = None
        self._png: bytes | None = None
        self._track_map()

    def _current(self) -> _Picture | None:
        data = self.coordinator.data
        if data is None or data.map is None:
            return None
        status = data.status
        target = self.coordinator.job_target
        return _Picture(
            data.map,
            data.persisted_map,
            target,
            # The app hides the robot on its dock, and shows a whole-home job
            # (one with no rooms or zone) in purple.
            docked=status.is_docked,
            whole_home=status.mode == VacuumMode.CLEANING and target is None,
        )

    @callback
    def _track_map(self) -> None:
        # Status frames arrive every few seconds; only a new map object (or a
        # new job target) is a new picture, and rendering waits until something
        # asks for it.
        current = self._current()
        if current is not None and (self._drawn is None or not current.same_as(self._drawn)):
            self._drawn = current
            self._png = None
            self._attr_image_last_updated = dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._track_map()
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Calibration for vacuum map cards: map metres to picture pixels.

        With it, a card such as the Xiaomi Vacuum Map Card can turn a tap on the
        picture into map coordinates for the clean_spot service (set the card's
        coordinates_rounding to false: the map is in metres).
        """
        if self._drawn is None:
            return None
        canvas = Canvas.fit(self._drawn.vacuum_map, self._drawn.rooms_from)
        return {"calibration_points": canvas.calibration_points()}

    async def async_image(self) -> bytes | None:
        """Return the rendered map, drawing it on first request."""
        if self._drawn is None:
            return None
        if self._png is None:
            drawn = self._drawn
            png = await self.hass.async_add_executor_job(drawn.render)
            if drawn is self._drawn:
                self._png = png
            return png
        return self._png


@dataclass(frozen=True)
class _Picture:
    """Everything the picture depends on."""

    vacuum_map: VacuumMap
    rooms_from: VacuumMap | None
    target: JobTarget | None
    docked: bool
    whole_home: bool

    def same_as(self, other: _Picture) -> bool:
        # Maps compare by identity: a new frame is a new object, and comparing
        # the rasters themselves every few seconds would be wasted work.
        return (
            self.vacuum_map is other.vacuum_map
            and self.rooms_from is other.rooms_from
            and (self.target, self.docked, self.whole_home)
            == (other.target, other.docked, other.whole_home)
        )

    def render(self) -> bytes:
        return render_map(
            self.vacuum_map,
            self.rooms_from,
            self.target,
            docked=self.docked,
            whole_home=self.whole_home,
        )
