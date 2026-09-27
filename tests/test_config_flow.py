"""Config and options flows."""
from __future__ import annotations

from datetime import timedelta

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from sharklocal import ConnectError, SharklocalError
from sharklocal.models import DeviceInfo

from custom_components.sharkiq_local_unofficial.const import DOMAIN

from .conftest import ENTRY_DATA

USER_INPUT = {
    "host": " 192.0.2.10 ",
    "name": "Basement Shark",
    "mapping": "sharkiq_v1",
    "use_mqtt": True,
}


async def start(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}
    return result


async def submit(hass, result, user_input=USER_INPUT):
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    await hass.async_block_till_done()
    return result


async def test_creates_an_entry_keyed_by_host_when_no_mac_is_readable(hass, robot):
    result = await submit(hass, await start(hass))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Basement Shark"
    assert result["data"] == {**ENTRY_DATA}
    assert result["result"].unique_id == "192.0.2.10"


async def test_prefers_the_mac_as_unique_id(hass, robot):
    robot.answers["get_wifi_status"] = DeviceInfo(mac_address="AA:BB:CC:DD:EE:FF")

    result = await submit(hass, await start(hass))

    assert result["result"].unique_id == "AA:BB:CC:DD:EE:FF"


async def test_unreachable_vacuum(hass, robot):
    robot.answers["get_status"] = ConnectError("no route")

    result = await submit(hass, await start(hass))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_library_error(hass, robot):
    robot.answers["get_status"] = SharklocalError("bad frame")

    result = await submit(hass, await start(hass))

    assert result["errors"] == {"base": "unknown"}


async def test_unexpected_error(hass, robot):
    robot.answers["get_status"] = RuntimeError("boom")

    result = await submit(hass, await start(hass))

    assert result["errors"] == {"base": "unknown"}


async def test_same_vacuum_twice_updates_the_host(hass, robot, entry):
    hass.config_entries.async_update_entry(entry, unique_id="192.0.2.10")

    result = await submit(hass, await start(hass))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_change_the_poll_interval_live(hass, robot, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 120}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hass.data[DOMAIN][entry.entry_id].update_interval == timedelta(seconds=120)
