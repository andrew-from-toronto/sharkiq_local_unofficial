"""Settings, offered by robot type; carpet detection; volume; job buttons."""
from __future__ import annotations

import pytest

from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
    mock_restore_cache_with_extra_data,
)

from sharklocal import ConnectError, protobuf

from custom_components.sharkiq_local_unofficial.codes import advice, describe
from custom_components.sharkiq_local_unofficial.const import DOMAIN

from .conftest import ENTRY_DATA, decode_frame, docked_status, push

PREFIX = "basement_shark"
VACUUM = f"vacuum.{PREFIX}"
DND = f"switch.{PREFIX}_do_not_disturb"
EDGE = f"switch.{PREFIX}_cleanedge"
CARPET = f"select.{PREFIX}_carpet_detection"
VOLUME = f"number.{PREFIX}_volume"
EXPLORE = f"button.{PREFIX}_explore_run"
CLEAN_AREA = 16384


def setting(field: int, value: int) -> bytes:
    return protobuf.encode_bytes_field(7, protobuf.encode_varint_field(field, value))


async def setup(hass, **options) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options=options, title="Basement Shark")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def entity_ids(hass, entry) -> set[str]:
    return {e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}


def settings(hass, entry) -> set[str]:
    return {
        i.removeprefix(f"switch.{PREFIX}_")
        for i in entity_ids(hass, entry)
        if i.startswith("switch.")
    }


async def test_a_lidar_robot_gets_the_lidar_settings(hass, robot):
    # The defaults: a laser robot with a self-emptying dock, no CleanEdge.
    entry = await setup(hass)
    assert settings(hass, entry) == {"recharge_resume", "evac_resume", "do_not_disturb"}
    ids = entity_ids(hass, entry)
    assert {VOLUME, EXPLORE, f"image.{PREFIX}_map"} <= ids
    assert CARPET not in ids
    assert hass.states.get(VACUUM).attributes["supported_features"] & CLEAN_AREA


async def test_hardware_extras_are_asked(hass, robot):
    entry = await setup(hass, self_empty_dock=False, clean_edge=True)
    assert settings(hass, entry) == {"recharge_resume", "cleanedge", "do_not_disturb"}


async def test_rv3000_gets_its_rows(hass, robot):
    entry = await setup(hass, robot_type="rv3000")
    assert {"underglow_lights", "button_sounds"} <= settings(hass, entry)
    assert "carpet_boost" not in settings(hass, entry)


async def test_a_carpet_robot_gets_carpet_controls_but_no_matrix(hass, robot):
    entry = await setup(hass, robot_type="lidar_carpet")
    assert "carpet_boost" in settings(hass, entry)
    assert CARPET in entity_ids(hass, entry)
    await push(hass, entry, docked_status(map=decode_frame("sharkiq_persisted_map_frame.b64")))
    with pytest.raises(ServiceValidationError, match="Matrix"):
        await hass.services.async_call(
            DOMAIN, "clean_rooms", {"entity_id": VACUUM, "rooms": ["Hallway"], "matrix": True}, blocking=True
        )


async def test_a_spot_lidar_robot_has_no_spot_clean(hass, robot):
    await setup(hass, robot_type="spot_lidar")
    with pytest.raises(ServiceValidationError, match="spot"):
        await hass.services.async_call(DOMAIN, "clean_spot", {"entity_id": VACUUM, "x": 0, "y": 0}, blocking=True)


async def test_a_basic_robot_gets_no_map_rooms_or_settings(hass, robot):
    entry = await setup(hass, robot_type="basic", self_empty_dock=False)
    ids = entity_ids(hass, entry)
    assert settings(hass, entry) == set()
    assert not ({f"image.{PREFIX}_map", VOLUME, EXPLORE} & ids)
    assert not hass.states.get(VACUUM).attributes["supported_features"] & CLEAN_AREA


async def test_changing_the_robot_type_changes_the_entities(hass, robot):
    entry = await setup(hass)
    assert CARPET not in entity_ids(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"robot_type": "lidar_carpet", "self_empty_dock": True, "clean_edge": False, "scan_interval": 30},
    )
    await hass.async_block_till_done()

    assert hass.states.get(CARPET) is not None


async def test_a_held_setting_is_written_and_remembered(hass, robot):
    entry = await setup(hass)
    assert hass.states.get(DND).state == "unknown"

    await hass.services.async_call("switch", "turn_on", {"entity_id": DND}, blocking=True)

    assert robot.payloads == [setting(18, 1)]
    assert hass.states.get(DND).state == "on"
    await push(hass, entry, docked_status())
    assert hass.states.get(DND).state == "on"


async def test_a_held_setting_is_restored(hass, robot):
    mock_restore_cache(hass, [State(DND, "off")])
    await setup(hass)
    assert hass.states.get(DND).state == "off"


async def test_a_reported_setting_follows_the_robot(hass, robot):
    entry = await setup(hass, clean_edge=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": EDGE}, blocking=True)
    assert robot.payloads == [setting(14, 1)]

    await push(hass, entry, docked_status(clean_edge=False))
    assert hass.states.get(EDGE).state == "off"


async def test_carpet_detection(hass, robot):
    mock_restore_cache(hass, [State(CARPET, "off")])
    entry = await setup(hass, robot_type="lidar_carpet")
    assert hass.states.get(CARPET).state == "off"

    await hass.services.async_call(
        "select", "select_option", {"entity_id": CARPET, "option": "auto"}, blocking=True
    )
    assert robot.payloads == [setting(15, 2)]
    assert hass.states.get(CARPET).state == "auto"

    await push(hass, entry, docked_status(carpet_detect=1))
    assert hass.states.get(CARPET).state == "off"


async def test_carpet_detection_failure(hass, robot):
    await setup(hass, robot_type="lidar_carpet")
    robot.answers["send"] = ConnectError("broker gone")
    with pytest.raises(HomeAssistantError, match="carpet"):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": CARPET, "option": "auto"}, blocking=True
        )


async def test_volume(hass, robot):
    await setup(hass)
    await hass.services.async_call("number", "set_value", {"entity_id": VOLUME, "value": 40}, blocking=True)
    assert robot.payloads == [setting(2, 40)]
    assert hass.states.get(VOLUME).state == "40"


async def test_volume_is_restored(hass, robot):
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(VOLUME, "65"),
                {
                    "native_value": 65,
                    "native_min_value": 0,
                    "native_max_value": 100,
                    "native_step": 1,
                    "native_unit_of_measurement": "%",
                },
            )
        ],
    )
    await setup(hass)
    assert float(hass.states.get(VOLUME).state) == 65


async def test_volume_failure(hass, robot):
    await setup(hass)
    robot.answers["send"] = ConnectError("broker gone")
    with pytest.raises(HomeAssistantError, match="volume"):
        await hass.services.async_call("number", "set_value", {"entity_id": VOLUME, "value": 40}, blocking=True)


async def test_explore_starts_disabled_and_runs(hass, robot):
    entry = await setup(hass)
    registry = er.async_get(hass)
    assert registry.async_get(EXPLORE).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    registry.async_update_entity(EXPLORE, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call("button", "press", {"entity_id": EXPLORE}, blocking=True)
    assert robot.actions == ["explore"]

    robot.answers["explore"] = ConnectError("broker gone")
    with pytest.raises(HomeAssistantError, match="explore"):
        await hass.services.async_call("button", "press", {"entity_id": EXPLORE}, blocking=True)


def test_fault_words():
    assert describe("ERROR_SFRONT_WHEEL_STUCK_L") == "Side brush is stuck"
    assert advice("ERROR_SFRONT_WHEEL_STUCK_L") == "Please remove any hair and debris."
    assert advice("ERROR_CANNOT_FINISH_CLEAN") is None  # no advice given
    assert advice("WARN_DUST_FULL") is None  # not a fault
    assert advice(None) is None
    assert describe("DE_CLEAN_FINISH") == "Clean finished"
    assert describe("SYS_ST_CHARGING") == "Charging"
    assert describe("WARN_DUST_FULL") == "Dust bin full"
    assert describe("RS_SUCCESS") == "Knows where it is"
    from custom_components.sharkiq_local_unofficial import codes

    assert not any("%1" in title + text for title, text in codes.FAULTS.values())
