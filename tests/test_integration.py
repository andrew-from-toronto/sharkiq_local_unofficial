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
    area = float(hass.states.get("sensor.basement_shark_last_clean_area").state)
    assert area == pytest.approx(14.2, abs=0.1)
    # 335 s, shown in minutes.
    minutes = float(hass.states.get("sensor.basement_shark_last_clean_duration").state)
    assert minutes == pytest.approx(5.58, abs=0.01)
    warning = hass.states.get("sensor.basement_shark_last_warning")
    assert warning.state == "WARN_MM_LOWLIGHT"
    assert warning.attributes["warnings"] == ["WARN_MM_LOWLIGHT"]
    assert hass.states.get("sensor.basement_shark_last_dock_reason").state == "DE_USR_CTR_DOCK"


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
