"""DataUpdateCoordinator for a single Shark IQ vacuum."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from sharklocal import (
    ConnectError,
    SharklocalError,
    VacuumClient,
)
from sharklocal.compat import Capabilities
from sharklocal.models import (
    DeviceInfo,
    MapPoint,
    SuctionLevel,
    VacuumMap,
    VacuumStatus,
)

from .const import (
    CONF_CLEAN_EDGE,
    CONF_ROBOT_TYPE,
    CONF_SELF_EMPTY_DOCK,
    DEFAULT_CLEAN_EDGE,
    DEFAULT_ROBOT_TYPE,
    DEFAULT_SELF_EMPTY_DOCK,
    DOMAIN,
    EVENT_VACUUM_EVENT,
)
from .codes import describe

MAP_REQUEST_ATTEMPTS = 3

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
        self.capabilities: Capabilities = capabilities_from_options({})
        # The end-of-job map is published once, as the robot docks, so a job
        # that ended while this entry was down or the robot unreachable leaves
        # the last-job sensors on the one before. A docked robot re-sends its
        # saved map on request (that job's path and summary, no event log), so
        # ask for it after setup and after every outage until one arrives. Not
        # durable: a restart is exactly when it is due.
        self._map_requests_left = 0
        self._arm_map_request()

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
                self._map_requests_left = 0
                # The client skips a requested map of a job whose end-of-job
                # frame it already has, since that one carries the event log.
                if self.client.last_map is status.map:
                    # The job is over; the persisted frame supersedes the live
                    # one and records what the job cleaned.
                    self._live_map = None
                    self.job_target = None
                    # Once per job, so save now: a delayed save is only flushed
                    # at shutdown and an entry reload in between would lose it.
                    self.hass.async_create_task(
                        self._map_store.async_save(status.map.to_dict()),
                        f"{DOMAIN} save map",
                    )
                    self._update_firmware(status.map)
            else:
                self._live_map = status.map
        # Log entries the robot streams mid-job become events automations can
        # match on (by key and code).
        for entry in status.log_entries or ():
            self.hass.bus.async_fire(
                EVENT_VACUUM_EVENT,
                {
                    "host": self.host,
                    "key": entry.key,
                    "code": entry.code,
                    "description": describe(entry.code),
                    "time": entry.time,
                },
            )
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
            self._arm_map_request()
            raise UpdateFailed(f"Vacuum {self.host} unreachable: {err}") from err
        except SharklocalError as err:
            self._arm_map_request()
            raise UpdateFailed(f"Vacuum {self.host} error: {err}") from err

        await self._request_missed_map(status)

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

    @callback
    def _arm_map_request(self) -> None:
        """Ask for the saved map on the next polls that find the robot docked."""
        if self.use_mqtt:
            # The monitor may still be subscribing when the first request goes
            # out, and a robot that has never mapped will not answer, so a few
            # polls' worth rather than one or forever.
            self._map_requests_left = MAP_REQUEST_ATTEMPTS

    async def _request_missed_map(self, status: VacuumStatus) -> None:
        """Ask a docked robot for its saved map while a request is due."""
        # Only while docked: that is when the robot has a finished job to send,
        # and a command it was never sent mid-job is not tried then.
        if self._map_requests_left <= 0 or not status.is_docked:
            return
        self._map_requests_left -= 1
        try:
            await self.client.request_map()
        except SharklocalError as err:
            _LOGGER.debug("Could not request the saved map from %s: %s", self.host, err)

    @property
    def has_wifi_status(self) -> bool:
        """Whether the robot answered the REST wifi_status call at setup."""
        return self._wifi is not None

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
        hardware = None
        mac = None
        if self._device_info:
            firmware = self._device_info.firmware
            mac = self._device_info.mac_address
        if self._wifi and not mac:
            mac = self._wifi.mac_address
        # REST is dead on some firmware; the event log names the versions too.
        logged = firmware_from_log(self.client.last_map)
        return {
            "firmware": firmware or logged.get("DT_VERSION_L01"),
            "hardware": logged.get("DT_VERSION_MCU"),
            "mac": mac,
        }

    @callback
    def _update_firmware(self, vacuum_map: VacuumMap) -> None:
        """Keep the device's versions in step with what the robot last logged."""
        logged = firmware_from_log(vacuum_map)
        if not logged:
            return
        registry = dr.async_get(self.hass)
        device = registry.async_get_device_by_identifier((DOMAIN, self.unique_id), self.entry_id)
        if device is None:
            return
        registry.async_update_device(
            device.id,
            sw_version=logged.get("DT_VERSION_L01", device.sw_version),
            hw_version=logged.get("DT_VERSION_MCU", device.hw_version),
        )


def capabilities_from_options(options: Mapping[str, Any]) -> Capabilities:
    """What the robot has, as its owner configured it (sharklocal.compat)."""
    return Capabilities(
        options.get(CONF_ROBOT_TYPE, DEFAULT_ROBOT_TYPE),
        options.get(CONF_SELF_EMPTY_DOCK, DEFAULT_SELF_EMPTY_DOCK),
        options.get(CONF_CLEAN_EDGE, DEFAULT_CLEAN_EDGE),
    )


def firmware_from_log(vacuum_map: VacuumMap | None) -> dict[str, str]:
    """The firmware versions a persisted map's event log carries."""
    if vacuum_map is None:
        return {}
    return {
        entry.key: entry.code
        for entry in vacuum_map.log
        if entry.key in ("DT_VERSION_L01", "DT_VERSION_MCU") and entry.code
    }
