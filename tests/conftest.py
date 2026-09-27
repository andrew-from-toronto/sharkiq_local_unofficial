"""Fixtures for Shark IQ (Local) tests.

The real VacuumClient is used throughout, with only its transport stubbed:
``_execute`` (fixed mapping actions) and ``_send`` (runtime-built payloads)
record what would go to the robot, and frames are fed in through the same
callback the MQTT monitor drives. Map frames are real captures from an
RV2610BFCA.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from pytest_homeassistant_custom_component.common import MockConfigEntry

from sharklocal import ConnectError, protobuf
from sharklocal.client import VacuumClient
from sharklocal.models import VacuumMap, VacuumMode, VacuumStatus
from sharklocal.vacuum_map import decode_map

from custom_components.sharkiq_local_unofficial.const import DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"

ENTRY_DATA = {
    "host": "192.0.2.10",
    "name": "Basement Shark",
    "mapping": "sharkiq_v1",
    "use_mqtt": True,
}


def fixture_bytes(name: str) -> bytes:
    """Decoded bytes of a base64 capture."""
    return base64.b64decode((FIXTURES / name).read_text().strip())


def decode_frame(name: str) -> VacuumMap:
    """Decode a captured map frame."""
    return decode_map(protobuf.decode_fields(fixture_bytes(name)))


def docked_status(**changes: Any) -> VacuumStatus:
    """A status like the robot reports sitting on its dock."""
    values: dict[str, Any] = dict(
        mode=VacuumMode.DOCKED,
        battery_level=100,
        charging=True,
        job_active=False,
        deep_clean=None,
        recharge_resume=True,
        evac_resume=False,
    )
    values.update(changes)
    return VacuumStatus(**values)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components from this repo."""
    yield


class Robot:
    """Records what the integration sends and plays frames back."""

    def __init__(self) -> None:
        self.status = docked_status()
        self.actions: list[str] = []
        self.payloads: list[bytes] = []
        self.start_monitoring = AsyncMock()
        # Per-action answers: a value to return or an exception to raise.
        # This firmware's REST endpoints are dead, so by default the REST-only
        # identity calls fail and only MQTT answers.
        self.answers: dict[str, Any] = {
            "get_robot_id": ConnectError("REST refused"),
            "get_wifi_status": ConnectError("REST refused"),
        }

    async def execute(self, client: VacuumClient, action: str) -> Any:
        answer = self.answers.get(action)
        if isinstance(answer, Exception):
            raise answer
        if action == "get_status":
            return self.status
        if answer is not None:
            return answer
        self.actions.append(action)
        return True

    async def send(self, client: VacuumClient, payload: bytes) -> bool:
        if isinstance(error := self.answers.get("send"), Exception):
            raise error
        self.payloads.append(payload)
        return True


@pytest.fixture
def robot():
    """Stub the transport under the real VacuumClient."""
    robot = Robot()

    async def execute(client, action):
        return await robot.execute(client, action)

    async def send(client, payload):
        return await robot.send(client, payload)

    with (
        patch.object(VacuumClient, "_execute", execute),
        patch.object(VacuumClient, "_send", send),
        patch.object(VacuumClient, "start_monitoring", robot.start_monitoring),
    ):
        yield robot


@pytest.fixture
def entry(hass):
    """The config entry for one vacuum."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, title="Basement Shark")
    entry.add_to_hass(hass)
    return entry


async def push(hass, entry, status: VacuumStatus) -> None:
    """Deliver a status frame as the MQTT monitor would."""
    client: VacuumClient = hass.data[DOMAIN][entry.entry_id].client
    await client._on_monitor_status(status)
    await hass.async_block_till_done()
