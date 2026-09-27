"""Settings the robot does not report, carpet detection, and job buttons."""
from __future__ import annotations

import pytest

from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import mock_restore_cache

from sharklocal import ConnectError, protobuf

from custom_components.sharkiq_local_unofficial.codes import advice, describe

from .conftest import docked_status, push

DND = "switch.basement_shark_do_not_disturb"
EDGE = "switch.basement_shark_cleanedge"
CARPET = "select.basement_shark_carpet_detection"


def setting(field: int, value: int) -> bytes:
    return protobuf.encode_bytes_field(7, protobuf.encode_varint_field(field, value))


async def setup(hass, entry, enable: tuple[str, ...] = ()) -> None:
    # The unverified settings are disabled by default; enable those under test.
    registry = er.async_get(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for entity_id in enable:
        registry.async_update_entity(entity_id, disabled_by=None)
    if enable:
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()


async def test_unverified_settings_start_disabled(hass, entry, robot):
    await setup(hass, entry)
    registry = er.async_get(hass)
    for entity_id in (DND, EDGE, CARPET, "button.basement_shark_explore_run"):
        assert registry.async_get(entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get("button.basement_shark_edge_clean") is not None


async def test_a_held_setting_is_written_and_remembered(hass, entry, robot):
    await setup(hass, entry, enable=(DND,))
    assert hass.states.get(DND).state == "unknown"

    await hass.services.async_call("switch", "turn_on", {"entity_id": DND}, blocking=True)

    assert robot.payloads == [setting(18, 1)]
    assert hass.states.get(DND).state == "on"
    await push(hass, entry, docked_status())
    assert hass.states.get(DND).state == "on"


async def test_a_held_setting_is_restored(hass, entry, robot):
    mock_restore_cache(hass, [State(DND, "off")])
    await setup(hass, entry, enable=(DND,))
    assert hass.states.get(DND).state == "off"


async def test_a_reported_setting_follows_the_robot(hass, entry, robot):
    await setup(hass, entry, enable=(EDGE,))
    await hass.services.async_call("switch", "turn_on", {"entity_id": EDGE}, blocking=True)
    assert robot.payloads == [setting(14, 1)]

    await push(hass, entry, docked_status(clean_edge=False))
    assert hass.states.get(EDGE).state == "off"


async def test_carpet_detection(hass, entry, robot):
    mock_restore_cache(hass, [State(CARPET, "off")])
    await setup(hass, entry, enable=(CARPET,))
    assert hass.states.get(CARPET).state == "off"

    await hass.services.async_call(
        "select", "select_option", {"entity_id": CARPET, "option": "auto"}, blocking=True
    )
    assert robot.payloads == [setting(15, 2)]
    assert hass.states.get(CARPET).state == "auto"

    await push(hass, entry, docked_status(carpet_detect=1))
    assert hass.states.get(CARPET).state == "off"


async def test_carpet_detection_failure(hass, entry, robot):
    await setup(hass, entry, enable=(CARPET,))
    robot.answers["send"] = ConnectError("broker gone")
    with pytest.raises(HomeAssistantError, match="carpet"):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": CARPET, "option": "auto"}, blocking=True
        )


async def test_job_buttons(hass, entry, robot):
    await setup(hass, entry, enable=("button.basement_shark_explore_run",))

    await hass.services.async_call("button", "press", {"entity_id": "button.basement_shark_edge_clean"}, blocking=True)
    await hass.services.async_call("button", "press", {"entity_id": "button.basement_shark_explore_run"}, blocking=True)

    assert robot.actions == ["edge_clean", "explore"]


async def test_a_refused_button_is_an_error(hass, entry, robot):
    await setup(hass, entry)
    robot.answers["edge_clean"] = ConnectError("broker gone")
    with pytest.raises(HomeAssistantError, match="edge_clean"):
        await hass.services.async_call("button", "press", {"entity_id": "button.basement_shark_edge_clean"}, blocking=True)


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
    # None of the app's placeholders leak through.
    from custom_components.sharkiq_local_unofficial import codes

    assert not any("%1" in title + text for title, text in codes.FAULTS.values())
