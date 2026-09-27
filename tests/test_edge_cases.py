"""Failure paths, restarts and the less travelled branches."""
from __future__ import annotations

import base64
import dataclasses
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.components.vacuum import DOMAIN as VACUUM_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.entity_component import DATA_INSTANCES
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from sharklocal import ConnectError, SharklocalError
from sharklocal.client import VacuumClient
from sharklocal.models import DeviceInfo, MapRoom

from custom_components.sharkiq_local_unofficial.const import DOMAIN
from custom_components.sharkiq_local_unofficial.map_render import render_map

from .conftest import ENTRY_DATA, decode_frame, docked_status, push

VACUUM = "vacuum.basement_shark"
ROOMS = ["Bathroom. ", "Room", "Laundry Room", "Hallway"]


async def setup(hass, entry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def push_persisted_map(hass, entry) -> None:
    await push(hass, entry, docked_status(map=decode_frame("sharkiq_persisted_map_frame.b64")))


def entity(hass, entity_id: str):
    domain = entity_id.split(".")[0]
    return hass.data[DATA_INSTANCES][domain].get_entity(entity_id)


def coordinator(hass, entry):
    return hass.data[DOMAIN][entry.entry_id]


# ---------------------------------------------------------------------------
# Setup and teardown
# ---------------------------------------------------------------------------


async def test_client_that_cannot_open_retries_later(hass, entry, robot):
    with patch.object(VacuumClient, "__aenter__", AsyncMock(side_effect=ConnectError("x"))):
        await setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_failed_first_refresh_closes_the_client(hass, entry, robot):
    robot.answers["get_status"] = ConnectError("no route")
    with patch.object(VacuumClient, "close", AsyncMock()) as close:
        await setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    close.assert_awaited()


async def test_monitor_that_will_not_start_does_not_block_setup(hass, entry, robot):
    robot.start_monitoring.side_effect = SharklocalError("no MQTT")
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED


async def test_poll_rearms_the_monitor(hass, entry, robot):
    await setup(hass, entry)
    robot.start_monitoring.reset_mock()

    await coordinator(hass, entry).async_refresh()

    robot.start_monitoring.assert_awaited_once()


@pytest.mark.parametrize("error", [ConnectError("gone"), SharklocalError("garbled")])
async def test_failed_poll_marks_entities_unavailable(hass, entry, robot, error):
    await setup(hass, entry)
    robot.answers["get_status"] = error

    await coordinator(hass, entry).async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(VACUUM).state == "unavailable"


async def test_unreadable_stored_map_is_ignored(hass, entry, robot, hass_storage):
    hass_storage[f"{DOMAIN}.{entry.entry_id}.map"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}.map",
        "data": {"not": "a map"},
    }
    await setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert "rooms" not in hass.states.get(VACUUM).attributes


async def test_removing_the_vacuum_deletes_its_stored_map(hass, entry, robot, hass_storage):
    await setup(hass, entry)
    await push_persisted_map(hass, entry)
    key = f"{DOMAIN}.{entry.entry_id}.map"
    assert key in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert key not in hass_storage


# ---------------------------------------------------------------------------
# Identity: MAC from wifi_status, else robot_id, else host
# ---------------------------------------------------------------------------


async def test_identity_from_wifi_status(hass, entry, robot):
    robot.answers["get_wifi_status"] = DeviceInfo(mac_address="AA:BB:CC:DD:EE:FF")
    await setup(hass, entry)

    device = dr.async_get(hass).async_get_device_by_connection(
        (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff"), entry.entry_id
    )
    assert device is not None
    assert er.async_get(hass).async_get(VACUUM).unique_id == "AA:BB:CC:DD:EE:FF_vacuum"


async def test_identity_and_firmware_from_robot_id(hass, entry, robot):
    robot.answers["get_robot_id"] = DeviceInfo(firmware="6.6.10", mac_address="11:22:33:44:55:66")
    await setup(hass, entry)

    assert er.async_get(hass).async_get(VACUUM).unique_id == "11:22:33:44:55:66_vacuum"
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "11:22:33:44:55:66"), entry.entry_id
    )
    assert device.sw_version == "6.6.10"


async def test_wifi_is_refreshed_on_its_own_cadence(hass, robot):
    # A 300 s poll refreshes Wi-Fi every poll; a failure there is not a failed poll.
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, options={"scan_interval": 300}, title="Basement Shark"
    )
    entry.add_to_hass(hass)
    await setup(hass, entry)
    robot.answers["get_wifi_status"] = DeviceInfo(rssi=-61, mac_address="AA:BB:CC:DD:EE:FF")

    await coordinator(hass, entry).async_refresh()
    assert coordinator(hass, entry).data.wifi.rssi == -61

    robot.answers["get_wifi_status"] = ConnectError("REST refused")
    await coordinator(hass, entry).async_refresh()
    assert coordinator(hass, entry).last_update_success
    assert coordinator(hass, entry).data.wifi.rssi == -61


# ---------------------------------------------------------------------------
# Vacuum commands
# ---------------------------------------------------------------------------


async def test_plain_commands(hass, entry, robot):
    await setup(hass, entry)

    for service in ("start", "stop", "return_to_base"):
        await hass.services.async_call(VACUUM_DOMAIN, service, {"entity_id": VACUUM}, blocking=True)

    assert robot.actions == ["start_cleaning", "stop", "go_home"]


async def test_a_refused_command_is_an_error_not_a_log_line(hass, entry, robot):
    await setup(hass, entry)
    robot.answers["start_cleaning"] = ConnectError("broker gone")

    with pytest.raises(HomeAssistantError, match="start_cleaning failed"):
        await hass.services.async_call(VACUUM_DOMAIN, "start", {"entity_id": VACUUM}, blocking=True)


async def test_a_rejected_argument_is_a_validation_error(hass, entry, robot):
    await setup(hass, entry)
    await push_persisted_map(hass, entry)
    robot.answers["send"] = ValueError("outside the map")

    with pytest.raises(ServiceValidationError, match="outside the map"):
        await hass.services.async_call(
            DOMAIN, "clean_spot", {"entity_id": VACUUM, "x": 90, "y": 90}, blocking=True
        )


async def test_clean_spot(hass, entry, robot):
    await setup(hass, entry)
    await push_persisted_map(hass, entry)

    await hass.services.async_call(
        DOMAIN, "clean_spot", {"entity_id": VACUUM, "x": 1.2, "y": -0.4}, blocking=True
    )

    assert len(robot.payloads) == 1
    assert robot.actions == ["start_cleaning"]


async def test_unknown_fan_speed(hass, entry, robot):
    await setup(hass, entry)

    with pytest.raises(ServiceValidationError, match="turbo"):
        await hass.services.async_call(
            VACUUM_DOMAIN, "set_fan_speed", {"entity_id": VACUUM, "fan_speed": "turbo"}, blocking=True
        )
    assert robot.actions == []


async def test_fan_speed_is_restored_across_restarts(hass, entry, robot):
    mock_restore_cache(hass, [State(VACUUM, "docked", {"fan_speed": "eco"})])
    await setup(hass, entry)

    assert hass.states.get(VACUUM).attributes["fan_speed"] == "eco"


async def test_matrix_clean_is_reported_during_a_job(hass, entry, robot):
    await setup(hass, entry)
    await push(hass, entry, docked_status(job_active=True, deep_clean=True))

    assert hass.states.get(VACUUM).attributes["matrix_clean"] is True


async def test_renamed_room_raises_the_area_mapping_repair(hass, entry, robot):
    await setup(hass, entry)
    er.async_get(hass).async_update_entity_options(
        VACUUM,
        VACUUM_DOMAIN,
        {
            "area_mapping": {},
            "last_seen_segments": [{"id": "Old name", "name": "Old name", "group": None}],
        },
    )

    await push_persisted_map(hass, entry)

    registry_id = er.async_get(hass).async_get(VACUUM).id
    assert ir.async_get(hass).async_get_issue(VACUUM_DOMAIN, f"segments_changed_{registry_id}")


async def test_unchanged_rooms_raise_no_repair(hass, entry, robot):
    await setup(hass, entry)
    er.async_get(hass).async_update_entity_options(
        VACUUM,
        VACUUM_DOMAIN,
        {
            "area_mapping": {},
            "last_seen_segments": [{"id": n, "name": n.strip(), "group": None} for n in ROOMS],
        },
    )

    await push_persisted_map(hass, entry)
    await push_persisted_map(hass, entry)

    assert not ir.async_get(hass).issues


# ---------------------------------------------------------------------------
# Switches
# ---------------------------------------------------------------------------


async def test_switch_off(hass, entry, robot):
    await setup(hass, entry)

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.basement_shark_recharge_resume"}, blocking=True
    )

    # Byte for byte the app's own Recharge & Resume off command.
    assert robot.payloads == [base64.b64decode("OgJAAg==")]


async def test_switch_failure_is_an_error(hass, entry, robot):
    await setup(hass, entry)
    robot.answers["send"] = ConnectError("broker gone")

    with pytest.raises(HomeAssistantError, match="evac_resume"):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": "switch.basement_shark_evac_resume"}, blocking=True
        )


# ---------------------------------------------------------------------------
# Map image
# ---------------------------------------------------------------------------


async def test_no_picture_before_the_first_map(hass, entry, robot):
    await setup(hass, entry)
    assert await entity(hass, "image.basement_shark_map").async_image() is None


async def test_picture_is_drawn_once_per_map(hass, entry, robot):
    await setup(hass, entry)
    await push_persisted_map(hass, entry)
    image = entity(hass, "image.basement_shark_map")

    with patch(
        "custom_components.sharkiq_local_unofficial.image.render_map", return_value=b"png"
    ) as render:
        assert await image.async_image() == b"png"
        assert await image.async_image() == b"png"
        # A status frame without a map is not a new picture.
        await push(hass, entry, docked_status())
        assert await image.async_image() == b"png"

    render.assert_called_once()


async def test_rooms_without_an_outline_are_not_labelled():
    vacuum_map = decode_frame("sharkiq_persisted_map_frame.b64")
    with_blank = dataclasses.replace(vacuum_map, rooms=[*vacuum_map.rooms, MapRoom("Nowhere", [])])
    assert render_map(with_blank) == render_map(vacuum_map)


# ---------------------------------------------------------------------------
# Entities read before any data (defensive branches)
# ---------------------------------------------------------------------------


async def test_entities_without_data(hass, entry, robot):
    await setup(hass, entry)
    await push_persisted_map(hass, entry)
    coordinator(hass, entry).data = None

    vacuum = entity(hass, VACUUM)
    assert vacuum.activity is None
    assert vacuum.extra_state_attributes == {}
    assert await vacuum.async_get_segments() == []
    assert entity(hass, "switch.basement_shark_evac_resume").is_on is None
    battery = entity(hass, "sensor.basement_shark_battery")
    assert battery.native_value is None
    assert battery.extra_state_attributes is None


@pytest.mark.parametrize("typed", ["Bathroom", "bathroom", "BATHROOM.", " bath room "])
async def test_room_names_match_without_punctuation(hass, entry, robot, typed):
    # A map card room id may only hold letters, digits, spaces and underscores.
    await setup(hass, entry)
    await push_persisted_map(hass, entry)

    await hass.services.async_call(
        DOMAIN, "clean_rooms", {"entity_id": VACUUM, "rooms": [typed]}, blocking=True
    )

    from .conftest import fixture_bytes

    assert robot.payloads == [fixture_bytes("sharkiq_cmd_room_clean.b64")]
