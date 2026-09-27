"""End-to-end tests: entities, commands, the stored map."""
from __future__ import annotations

from http import HTTPStatus

import pytest

from homeassistant.components.vacuum import DOMAIN as VACUUM_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sharkiq_local_unofficial.const import DOMAIN

from .conftest import ENTRY_DATA, decode_frame, docked_status, fixture_bytes, push

VACUUM = "vacuum.basement_shark"
ROOMS = ["Bathroom. ", "Room", "Laundry Room", "Hallway"]


async def setup(hass: HomeAssistant, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def push_persisted_map(hass, entry) -> None:
    await push(hass, entry, docked_status(map=decode_frame("sharkiq_persisted_map_frame.b64")))


async def test_entities_come_up_from_mqtt_alone(hass, entry, robot):
    await setup(hass, entry)

    assert hass.states.get(VACUUM).state == "docked"
    assert hass.states.get("sensor.basement_shark_battery").state == "100"
    assert hass.states.get("switch.basement_shark_recharge_resume").state == "on"
    assert hass.states.get("switch.basement_shark_evac_resume").state == "off"
    assert hass.states.get("image.basement_shark_map") is not None
    # No map has been published yet, so nothing claims to know the last job.
    assert hass.states.get("sensor.basement_shark_last_clean_area").state == "unknown"
    assert "rooms" not in hass.states.get(VACUUM).attributes


async def test_persisted_map_fills_in_the_last_job(hass, entry, robot):
    await setup(hass, entry)
    await push_persisted_map(hass, entry)

    attrs = hass.states.get(VACUUM).attributes
    assert attrs["rooms"] == ["Bathroom.", "Room", "Laundry Room", "Hallway"]
    # 880 cleaned cells of 0.06 m.
    area = float(hass.states.get("sensor.basement_shark_last_clean_area").state)
    assert area == pytest.approx(3.17, abs=0.01)
    # The log's 28 s + 73 s of cleaning, shown in minutes.
    minutes = float(hass.states.get("sensor.basement_shark_last_clean_duration").state)
    assert minutes == pytest.approx(1.68, abs=0.01)
    warning = hass.states.get("sensor.basement_shark_last_warning")
    assert warning.state == "Low light for the camera"
    assert warning.attributes["code"] == "WARN_MM_LOWLIGHT"
    assert warning.attributes["warnings"] == ["Low light for the camera"]
    assert warning.attributes["warning_codes"] == ["WARN_MM_LOWLIGHT"]
    dock = hass.states.get("sensor.basement_shark_last_dock_reason")
    assert dock.state == "Sent to dock by user"
    assert dock.attributes["code"] == "DE_USR_CTR_DOCK"
    end = hass.states.get("sensor.basement_shark_last_job_end_reason")
    assert (end.state, end.attributes["code"]) == ("Finished normally", "NORMAL")


async def test_a_job_that_logged_no_code_reads_none(hass, entry, robot):
    # A job sent home by the user logs a dock code but no termination code.
    await setup(hass, entry)
    vacuum_map = decode_frame("sharkiq_persisted_map_frame.b64")
    vacuum_map.log = [e for e in vacuum_map.log if e.key != "DT_WFF_TERM_CODE"]
    await push(hass, entry, docked_status(map=vacuum_map))

    end = hass.states.get("sensor.basement_shark_last_job_end_reason")
    assert (end.state, end.attributes["code"]) == ("None", "none")


async def test_clean_rooms_sends_what_the_app_sends(hass, entry, robot):
    await setup(hass, entry)
    await push_persisted_map(hass, entry)

    # Typed as a person would, not as the app stored it ("Bathroom. ").
    await hass.services.async_call(
        DOMAIN,
        "clean_rooms",
        {"entity_id": VACUUM, "rooms": ["bathroom"], "matrix": True},
        blocking=True,
    )

    assert robot.payloads == [fixture_bytes("sharkiq_cmd_room_clean_matrix.b64")]
    assert robot.actions == ["start_cleaning"]


async def test_unknown_room_is_refused_before_anything_is_sent(hass, entry, robot):
    await setup(hass, entry)
    await push_persisted_map(hass, entry)

    with pytest.raises(ServiceValidationError, match="Garage"):
        await hass.services.async_call(
            DOMAIN, "clean_rooms", {"entity_id": VACUUM, "rooms": ["Garage"]}, blocking=True
        )
    assert robot.payloads == []
    assert robot.actions == []


async def test_clean_rooms_without_a_map_says_why(hass, entry, robot):
    await setup(hass, entry)

    with pytest.raises(ServiceValidationError, match="next docks"):
        await hass.services.async_call(
            DOMAIN, "clean_rooms", {"entity_id": VACUUM, "rooms": ["Hallway"]}, blocking=True
        )


async def test_clean_area_uses_the_area_mapping(hass, entry, robot):
    await setup(hass, entry)
    await push_persisted_map(hass, entry)

    area = ar.async_get(hass).async_create("Basement bathroom")
    er.async_get(hass).async_update_entity_options(
        VACUUM,
        VACUUM_DOMAIN,
        {
            "area_mapping": {area.id: ["Bathroom. "]},
            "last_seen_segments": [
                {"id": name, "name": name.strip(), "group": None} for name in ROOMS
            ],
        },
    )

    await hass.services.async_call(
        VACUUM_DOMAIN,
        "clean_area",
        {"entity_id": VACUUM, "cleaning_area_id": [area.id]},
        blocking=True,
    )

    assert robot.payloads == [fixture_bytes("sharkiq_cmd_room_clean.b64")]
    assert robot.actions == ["start_cleaning"]


async def test_segments_are_the_map_rooms(hass, entry, robot, hass_ws_client):
    await setup(hass, entry)
    await push_persisted_map(hass, entry)

    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "vacuum/get_segments", "entity_id": VACUUM})
    reply = await ws.receive_json()

    assert reply["success"], reply
    assert [s["id"] for s in reply["result"]["segments"]] == ROOMS


async def test_map_survives_a_restart(hass, entry, robot):
    await setup(hass, entry)
    await push_persisted_map(hass, entry)
    # Unloading flushes the delayed save.
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    await setup(hass, entry)

    # No frame since the reload, yet the rooms are known and cleanable.
    assert hass.states.get(VACUUM).attributes["rooms"][0] == "Bathroom."
    await hass.services.async_call(
        DOMAIN,
        "clean_rooms",
        {"entity_id": VACUUM, "rooms": ["Bathroom"], "matrix": True},
        blocking=True,
    )
    assert robot.payloads == [fixture_bytes("sharkiq_cmd_room_clean_matrix.b64")]


async def test_fan_speed_is_held_because_the_robot_never_reports_it(hass, entry, robot):
    await setup(hass, entry)

    await hass.services.async_call(
        VACUUM_DOMAIN, "set_fan_speed", {"entity_id": VACUUM, "fan_speed": "max"}, blocking=True
    )

    assert robot.actions == ["set_suction_max"]
    assert hass.states.get(VACUUM).attributes["fan_speed"] == "max"
    # A later status frame (which has no suction field) must not clear it.
    await push(hass, entry, docked_status())
    assert hass.states.get(VACUUM).attributes["fan_speed"] == "max"


async def test_locate_and_switches(hass, entry, robot):
    await setup(hass, entry)

    await hass.services.async_call(VACUUM_DOMAIN, "locate", {"entity_id": VACUUM}, blocking=True)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.basement_shark_evac_resume"}, blocking=True
    )

    assert robot.actions == ["find_robot", "evac_resume_on"]
    # Not optimistic: the switch waits for the robot to report the change.
    assert hass.states.get("switch.basement_shark_evac_resume").state == "off"
    await push(hass, entry, docked_status(evac_resume=True))
    assert hass.states.get("switch.basement_shark_evac_resume").state == "on"


async def test_pause_is_not_offered(hass, entry, robot):
    # "stop" is the robot's return-to-dock; a pause button that sends it home lies.
    await setup(hass, entry)
    features = hass.states.get(VACUUM).attributes["supported_features"]
    assert not features & 4  # VacuumEntityFeature.PAUSE


async def test_map_image_follows_the_live_frames(hass, entry, robot, hass_client):
    await setup(hass, entry)
    client = await hass_client()
    await push_persisted_map(hass, entry)
    first = hass.states.get("image.basement_shark_map")

    token = first.attributes["access_token"]
    response = await client.get(f"/api/image_proxy/image.basement_shark_map?token={token}")
    assert response.status == HTTPStatus.OK
    assert (await response.read()).startswith(b"\x89PNG")

    await push(hass, entry, docked_status(map=decode_frame("sharkiq_live_map_frame.b64")))
    assert hass.states.get("image.basement_shark_map").state != first.state


async def test_map_publishes_calibration_for_map_cards(hass, entry, robot):
    await setup(hass, entry)
    assert "calibration_points" not in hass.states.get("image.basement_shark_map").attributes

    await push_persisted_map(hass, entry)

    points = hass.states.get("image.basement_shark_map").attributes["calibration_points"]
    assert len(points) == 3
    assert set(points[0]) == {"vacuum", "map"}


async def test_without_mqtt_only_the_rest_entities_exist(hass, robot):
    entry = MockConfigEntry(
        domain=DOMAIN, data={**ENTRY_DATA, "use_mqtt": False}, title="Basement Shark"
    )
    entry.add_to_hass(hass)
    await setup(hass, entry)

    assert hass.states.get(VACUUM) is not None
    assert hass.states.get("switch.basement_shark_recharge_resume") is None
    assert hass.states.get("image.basement_shark_map") is None
    assert hass.states.get("sensor.basement_shark_last_clean_area") is None


async def test_spot_clean_draws_its_zone_until_the_job_ends(hass, entry, robot):
    import dataclasses

    from sharklocal.models import MapRoom
    from sharklocal.vacuum_map import SPOT_ROOM_NAME, spot_polygon

    await setup(hass, entry)
    await push_persisted_map(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    before = hass.states.get("image.basement_shark_map").state

    await hass.services.async_call(
        DOMAIN, "clean_spot", {"entity_id": VACUUM, "x": 2.74, "y": 0.02}, blocking=True
    )

    assert coordinator.job_target.zone == tuple(spot_polygon(2.74, 0.02))
    assert hass.states.get("image.basement_shark_map").state != before

    # The robot's end-of-job map records the zone as a room named PinDrop.
    saved = decode_frame("sharkiq_persisted_map_frame.b64")
    spot = MapRoom(SPOT_ROOM_NAME, spot_polygon(2.74, 0.02), selected=True, coverage=1.0)
    saved = dataclasses.replace(saved, rooms=[*saved.rooms, spot])
    await push(hass, entry, docked_status(map=saved))

    assert coordinator.job_target is None
    # A zone is not a room: not listed, not a segment, not cleanable by name.
    assert hass.states.get(VACUUM).attributes["rooms"] == ["Bathroom.", "Room", "Laundry Room", "Hallway"]
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "clean_rooms", {"entity_id": VACUUM, "rooms": ["PinDrop"]}, blocking=True
        )


async def test_room_clean_records_its_rooms_and_a_full_clean_clears_them(hass, entry, robot):
    await setup(hass, entry)
    await push_persisted_map(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    await hass.services.async_call(
        DOMAIN, "clean_rooms", {"entity_id": VACUUM, "rooms": ["hallway"]}, blocking=True
    )
    assert coordinator.job_target.rooms == ("Hallway",)

    await hass.services.async_call(VACUUM_DOMAIN, "start", {"entity_id": VACUUM}, blocking=True)
    assert coordinator.job_target is None
