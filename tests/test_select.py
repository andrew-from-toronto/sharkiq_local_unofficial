"""The suction select and its link to the vacuum's fan speed."""
from __future__ import annotations

import pytest

from homeassistant.components.vacuum import DOMAIN as VACUUM_DOMAIN
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import mock_restore_cache

from sharklocal import ConnectError

from .conftest import docked_status, push

VACUUM = "vacuum.basement_shark"
SUCTION = "select.basement_shark_suction"


async def setup(hass, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def select(hass, option: str) -> None:
    await hass.services.async_call(
        "select", "select_option", {"entity_id": SUCTION, "option": option}, blocking=True
    )


async def test_unknown_until_set(hass, entry, robot):
    await setup(hass, entry)
    assert hass.states.get(SUCTION).state == "unknown"
    assert hass.states.get(SUCTION).attributes["options"] == ["eco", "normal", "max"]


async def test_select_sets_suction_and_the_fan_speed_follows(hass, entry, robot):
    await setup(hass, entry)

    await select(hass, "eco")

    assert robot.actions == ["set_suction_eco"]
    assert hass.states.get(SUCTION).state == "eco"
    assert hass.states.get(VACUUM).attributes["fan_speed"] == "eco"
    # Status frames carry no suction field and must not clear it.
    await push(hass, entry, docked_status())
    assert hass.states.get(SUCTION).state == "eco"


async def test_fan_speed_sets_suction_and_the_select_follows(hass, entry, robot):
    await setup(hass, entry)

    await hass.services.async_call(
        VACUUM_DOMAIN, "set_fan_speed", {"entity_id": VACUUM, "fan_speed": "max"}, blocking=True
    )

    assert hass.states.get(SUCTION).state == "max"


async def test_restored_from_the_select(hass, entry, robot):
    mock_restore_cache(hass, [State(SUCTION, "normal")])
    await setup(hass, entry)

    assert hass.states.get(SUCTION).state == "normal"
    assert hass.states.get(VACUUM).attributes["fan_speed"] == "normal"


async def test_a_nonsense_restored_value_is_ignored(hass, entry, robot):
    mock_restore_cache(hass, [State(SUCTION, "turbo"), State(VACUUM, "docked", {"fan_speed": "turbo"})])
    await setup(hass, entry)

    assert hass.states.get(SUCTION).state == "unknown"


async def test_a_refused_change_is_an_error_and_changes_nothing(hass, entry, robot):
    await setup(hass, entry)
    await select(hass, "eco")
    robot.answers["set_suction_max"] = ConnectError("broker gone")

    with pytest.raises(HomeAssistantError, match="suction"):
        await select(hass, "max")

    assert hass.states.get(SUCTION).state == "eco"
