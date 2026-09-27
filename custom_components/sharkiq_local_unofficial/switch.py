"""Switch platform for Shark IQ (Local): the robot's resume settings."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from sharklocal import SharklocalError, VacuumClient
from sharklocal.models import VacuumStatus

from .const import CONF_NAME, DOMAIN
from .coordinator import SharkCoordinator
from .entity import SharkBaseEntity


@dataclass(frozen=True, kw_only=True)
class SharkSwitchDescription(SwitchEntityDescription):
    """Describes a Shark setting switch."""

    value_fn: Callable[[VacuumStatus], bool | None]
    set_fn: Callable[[VacuumClient, bool], Awaitable[bool]]


SWITCHES: tuple[SharkSwitchDescription, ...] = (
    SharkSwitchDescription(
        key="recharge_resume",
        translation_key="recharge_resume",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda status: status.recharge_resume,
        set_fn=lambda client, on: client.set_recharge_resume(on),
    ),
    SharkSwitchDescription(
        key="evac_resume",
        translation_key="evac_resume",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda status: status.evac_resume,
        set_fn=lambda client, on: client.set_evac_resume(on),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the setting switches (MQTT only)."""
    coordinator: SharkCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.use_mqtt:
        return
    name = entry.data[CONF_NAME]
    async_add_entities(SharkSwitch(coordinator, name, desc) for desc in SWITCHES)


class SharkSwitch(SharkBaseEntity, SwitchEntity):
    """A robot setting that the status frame reports back."""

    entity_description: SharkSwitchDescription

    def __init__(
        self,
        coordinator: SharkCoordinator,
        entry_title: str,
        description: SharkSwitchDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry_title)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.unique_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the setting as the robot last reported it."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data.status)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the setting."""
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the setting."""
        await self._set(False)

    async def _set(self, on: bool) -> None:
        try:
            await self.entity_description.set_fn(self.coordinator.client, on)
        except SharklocalError as err:
            raise HomeAssistantError(
                f"Could not change {self.entity_description.key} on "
                f"{self.coordinator.host}: {err}"
            ) from err
        # No optimistic state: the next status frame carries the robot's answer.
        await self.coordinator.async_request_refresh()
