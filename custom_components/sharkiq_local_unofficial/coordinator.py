"""DataUpdateCoordinator for a single Shark IQ vacuum."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from sharklocal import (
    ConnectError,
    SharklocalError,
    VacuumClient,
)
from sharklocal.models import (
    DeviceInfo,
    MapPoint,
    SuctionLevel,
    VacuumMap,
    VacuumStatus,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


@dataclass
class SharkData:
    """Snapshot of all data we expose for one vacuum."""

    status: VacuumStatus
    device_info: DeviceInfo | None
    wifi: DeviceInfo | None
    # The map to draw: the latest live frame during a job, otherwise the latest
    # persisted one. None until the robot has published a map.
    map: VacuumMap | None = None
    # The latest persisted (end-of-job) map: rooms, dock, job summary, log.
    persisted_map: VacuumMap | None = None


@dataclass(frozen=True)
class JobTarget:
    """What the current job was sent to clean: chosen rooms, or a spot zone."""

    rooms: tuple[str, ...] = ()
    zone: tuple[MapPoint, ...] = ()


class SharkCoordinator(DataUpdateCoordinator[SharkData]):
    """Coordinate one Shark vacuum.

    Holds a long-lived VacuumClient. When MQTT is enabled the robot pushes a
    status frame every few seconds and those drive the entities; the poll
    every scan_interval seconds is then a fallback that also re-arms the
    MQTT monitor if its connection dropped. Device info is fetched once at
    startup and then refreshed at a cadence that doesn't scale with the poll
    rate.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: VacuumClient,
        entry_id: str,
        host: str,
        scan_interval: int,
        use_mqtt: bool,
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{host}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.entry_id = entry_id
        self.host = host
        self.use_mqtt = use_mqtt
        # Refresh Wi-Fi info roughly every 5 minutes regardless of poll rate.
        # Faster polling shouldn't mean we hammer wifi_status too — that endpoint
        # is heavier than /get/status and the data (RSSI, IP) rarely changes.
        self._wifi_refresh_every = max(1, 300 // max(1, scan_interval))
        # Cached once-per-session info (firmware, MAC, etc.)
        self._device_info: DeviceInfo | None = None
        self._wifi: DeviceInfo | None = None
        self._device_info_cycle = 0
        # The persisted map is published only when the robot docks, but room
        # and spot cleaning need its room definition at any time, so the
        # latest one is kept on disk across restarts.
        self._map_store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.map"
        )
        self._live_map: VacuumMap | None = None
        # The robot never reports its suction level — it only echoes a change
        # once — so the last level set is held here, shared by the vacuum's fan
        # speed and the suction select, and restored by them after a restart.
        self.suction: SuctionLevel | None = None
        # Live frames do not say what a job was sent to clean, so the target of
        # a job started here is held until the end-of-job map (which records
        # it) arrives. Not durable: a restart mid-job just stops drawing it.
        self.job_target: JobTarget | None = None

    async def async_setup(self) -> None:
        """Initial setup: fetch device info and restore the stored map."""
        try:
            self._device_info = await self.client.get_device_info()
        except SharklocalError as err:
            _LOGGER.debug("Could not fetch device info for %s: %s", self.host, err)
        try:
            self._wifi = await self.client.get_wifi_status()
        except SharklocalError as err:
            _LOGGER.debug("Could not fetch wifi status for %s: %s", self.host, err)

        if not self.use_mqtt:
            return
        stored = await self._map_store.async_load()
        if stored:
            try:
                self.client.last_map = VacuumMap.from_dict(stored)
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.warning("Ignoring unreadable stored map for %s: %s", self.host, err)
        self.client.on_status_update(self._on_status)

    async def async_start_monitoring(self) -> None:
        """Start (or re-arm) the MQTT monitor. A no-op while it is running."""
        if not self.use_mqtt:
            return
        try:
            await self.client.start_monitoring()
        except SharklocalError as err:
            _LOGGER.debug("Could not start MQTT monitoring for %s: %s", self.host, err)

    async def async_set_suction(self, level: SuctionLevel) -> None:
        """Set the suction level and remember it."""
        await self.client.set_suction(level)
        self.suction = level
        self.async_update_listeners()

    @callback
    def set_job_target(self, target: JobTarget | None) -> None:
        """Record what a job started from here was sent to clean."""
        self.job_target = target
        self.async_update_listeners()

    @callback
    def restore_suction(self, value: Any) -> None:
        """Adopt a restored suction level, unless one is already known."""
        if self.suction is None and value in {level.value for level in SuctionLevel}:
            self.suction = SuctionLevel(value)
            # Whichever entity restores first, the other may already be showing
            # "unknown".
            self.async_update_listeners()

    @callback
    def _on_status(self, status: VacuumStatus) -> None:
        """Handle a status frame pushed by the robot."""
        if status.map is not None:
            if status.map.persisted:
                # The job is over; the persisted frame supersedes the live one
                # and records what the job cleaned.
                self._live_map = None
                self.job_target = None
                # Once per job, so save now: a delayed save is only flushed at
                # shutdown and an entry reload in between would lose it.
                self.hass.async_create_task(
                    self._map_store.async_save(status.map.to_dict()),
                    f"{DOMAIN} save map",
                )
            else:
                self._live_map = status.map
        self.async_set_updated_data(self._snapshot(status))

    def _snapshot(self, status: VacuumStatus) -> SharkData:
        persisted = self.client.last_map
        return SharkData(
            status=status,
            device_info=self._device_info,
            wifi=self._wifi,
            map=self._live_map or persisted,
            persisted_map=persisted,
        )

    async def _async_update_data(self) -> SharkData:
        """Poll the vacuum for current status."""
        # A dropped MQTT connection ends the monitor; the poll brings it back.
        await self.async_start_monitoring()

        try:
            status = await self.client.get_status()
        except ConnectError as err:
            raise UpdateFailed(f"Vacuum {self.host} unreachable: {err}") from err
        except SharklocalError as err:
            raise UpdateFailed(f"Vacuum {self.host} error: {err}") from err

        # Refresh wifi info on a time-based cadence (~5 min), not a fixed
        # poll-count, so faster polling doesn't proportionally hammer the
        # heavier wifi_status endpoint.
        self._device_info_cycle += 1
        if self._device_info_cycle >= self._wifi_refresh_every:
            self._device_info_cycle = 0
            try:
                self._wifi = await self.client.get_wifi_status()
            except SharklocalError as err:
                _LOGGER.debug("WiFi refresh failed for %s: %s", self.host, err)

        return self._snapshot(status)

    @property
    def unique_id(self) -> str:
        """Stable unique ID for this vacuum.

        Prefers MAC from wifi_status (recommended by the upstream lib),
        falls back to host if MAC isn't available.
        """
        if self._wifi and self._wifi.mac_address:
            return self._wifi.mac_address
        if self._device_info and self._device_info.mac_address:
            return self._device_info.mac_address
        return self.host

    def get_device_metadata(self) -> dict[str, Any]:
        """Return metadata for HA device registry."""
        firmware = None
        mac = None
        if self._device_info:
            firmware = self._device_info.firmware
            mac = self._device_info.mac_address
        if self._wifi and not mac:
            mac = self._wifi.mac_address
        return {"firmware": firmware, "mac": mac}
