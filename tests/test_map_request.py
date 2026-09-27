"""Recovering a job whose end-of-job map was missed, by asking for the saved map."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sharklocal import ConnectError
from sharklocal.models import VacuumMode

from custom_components.sharkiq_local_unofficial.const import DOMAIN
from custom_components.sharkiq_local_unofficial.coordinator import MAP_REQUEST_ATTEMPTS

from .conftest import decode_frame, docked_status, push

REQUESTED = "sharkiq_requested_map_frame.b64"
REPORT = "sharkiq_persisted_map_frame.b64"


async def setup(hass, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def coordinator(hass, entry):
    return hass.data[DOMAIN][entry.entry_id]


async def test_setup_asks_a_docked_robot_for_its_saved_map(hass, entry, robot):
    await setup(hass, entry)
    assert robot.map_requests == 1


async def test_requested_map_fills_in_the_missed_job(hass, entry, robot):
    await setup(hass, entry)
    await push(hass, entry, docked_status(map=decode_frame(REQUESTED)))

    start = hass.states.get("sensor.basement_shark_last_clean_start").state
    assert datetime.fromisoformat(start) == datetime.fromtimestamp(1790503168, UTC)
    area = float(hass.states.get("sensor.basement_shark_last_clean_area").state)
    assert area == pytest.approx(25.61, abs=0.01)
    # No event log on a requested map: the summary's 33 whole minutes.
    assert float(hass.states.get("sensor.basement_shark_last_clean_duration").state) == 33
    # No event log, so nothing claims to know what the job logged.
    for sensor in ("last_dock_reason", "last_job_end_reason", "last_clean_mode", "last_warning"):
        assert hass.states.get(f"sensor.basement_shark_{sensor}").state == "unknown", sensor

    # It arrived, so no more requests.
    robot.map_requests = 0
    await coordinator(hass, entry).async_refresh()
    assert robot.map_requests == 0


async def test_requested_map_is_stored(hass, entry, robot, hass_storage):
    await setup(hass, entry)
    await push(hass, entry, docked_status(map=decode_frame(REQUESTED)))
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}.map"]["data"]
    assert (stored["job_started"], stored["report"]) == (1790503168, False)


async def test_requested_map_of_the_same_job_keeps_the_report(hass, entry, robot):
    await setup(hass, entry)
    report = decode_frame(REPORT)
    await push(hass, entry, docked_status(map=report))
    requested = decode_frame(REQUESTED)
    requested.job_started = report.job_started

    await push(hass, entry, docked_status(map=requested))

    assert coordinator(hass, entry).client.last_map is report
    dock = hass.states.get("sensor.basement_shark_last_dock_reason")
    assert dock.state == "Sent to dock by user"


async def test_no_request_while_the_robot_is_out(hass, entry, robot):
    robot.status = docked_status(mode=VacuumMode.CLEANING)
    await setup(hass, entry)
    assert robot.map_requests == 0

    robot.status = docked_status()
    await coordinator(hass, entry).async_refresh()
    assert robot.map_requests == 1


async def test_requests_stop_after_a_few_unanswered_polls(hass, entry, robot):
    await setup(hass, entry)
    for _ in range(MAP_REQUEST_ATTEMPTS + 2):
        await coordinator(hass, entry).async_refresh()
    assert robot.map_requests == MAP_REQUEST_ATTEMPTS


async def test_an_outage_asks_again(hass, entry, robot):
    await setup(hass, entry)
    await push(hass, entry, docked_status(map=decode_frame(REQUESTED)))
    robot.map_requests = 0

    robot.answers["get_status"] = ConnectError("gone")
    await coordinator(hass, entry).async_refresh()
    del robot.answers["get_status"]
    await coordinator(hass, entry).async_refresh()

    assert robot.map_requests == 1


async def test_a_failed_request_does_not_fail_the_poll(hass, entry, robot):
    robot.answers["request_map"] = ConnectError("broker gone")
    await setup(hass, entry)
    assert coordinator(hass, entry).last_update_success
