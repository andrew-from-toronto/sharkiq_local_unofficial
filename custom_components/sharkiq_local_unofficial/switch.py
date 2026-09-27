"""Switch platform for Shark IQ (Local): the robot's settings."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from sharklocal import SharklocalError
from sharklocal.compat import Capabilities
from sharklocal.models import VacuumStatus

from .const import CONF_NAME, DOMAIN
from .coordinator import SharkCoordinator
from .entity import SharkBaseEntity


@dataclass(frozen=True, kw_only=True)
class SharkSwitchDescription(SwitchEntityDescription):
    """Describes a Shark setting switch.

    ``key`` is also the setting's name in sharklocal's CONFIG_TOGGLES.
    ``value_fn`` reads the setting back from a status frame, where the robot
    reports it; settings it does not report hold the last value set here.
    """

    supported_fn: Callable[[Capabilities], bool]
    value_fn: Callable[[VacuumStatus], bool | None] | None = None


# The settings the SharkClean app offers, each only on robots that have it
# (sharklocal.compat). The schema has more - child lock, silent mode,
# continuous crosshatch - that the app offers on no robot, so neither do we.
SWITCHES: tuple[SharkSwitchDescription, ...] = (
    SharkSwitchDescription(
        key="recharge_resume",
        translation_key="recharge_resume",
        entity_category=EntityCategory.CONFIG,
        supported_fn=lambda caps: caps.has_recharge_resume,
        value_fn=lambda status: status.recharge_resume,
    ),
    SharkSwitchDescription(
        key="evac_resume",
        translation_key="evac_resume",
        entity_category=EntityCategory.CONFIG,
        supported_fn=lambda caps: caps.has_auto_empty,
        value_fn=lambda status: status.evac_resume,
    ),
    SharkSwitchDescription(
        key="clean_edge",
        translation_key="clean_edge",
        entity_category=EntityCategory.CONFIG,
        supported_fn=lambda caps: caps.has_fan_jet,
        value_fn=lambda status: status.clean_edge,
    ),
    SharkSwitchDescription(
        key="do_not_disturb",
        translation_key="do_not_disturb",
        entity_category=EntityCategory.CONFIG,
        supported_fn=lambda caps: caps.do_not_disturb,
    ),
    SharkSwitchDescription(
        key="carpet_boost",
        translation_key="carpet_boost",
        entity_category=EntityCategory.CONFIG,
        supported_fn=lambda caps: caps.has_carpet_boost,
    ),
    SharkSwitchDescription(
        key="button_sounds",
        translation_key="button_sounds",
        entity_category=EntityCategory.CONFIG,
        supported_fn=lambda caps: caps.has_button_sounds,
    ),
    SharkSwitchDescription(
        key="underglow_lights",
        translation_key="underglow_lights",
        entity_category=EntityCategory.CONFIG,
        supported_fn=lambda caps: caps.has_underglow_lights,
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
    async_add_entities(
        SharkSwitch(coordinator, name, desc)
        for desc in SWITCHES
        if desc.supported_fn(coordinator.capabilities)
    )


class SharkSwitch(SharkBaseEntity, SwitchEntity, RestoreEntity):
    """A robot setting."""

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
        self._held: bool | None = None

    async def async_added_to_hass(self) -> None:
        """Restore a held setting the robot does not report."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._held = {STATE_ON: True, STATE_OFF: False}.get(last.state)

    @property
    def is_on(self) -> bool | None:
        """The setting as the robot reports it, else as last set here."""
        value_fn = self.entity_description.value_fn
        if value_fn is not None and self.coordinator.data is not None:
            reported = value_fn(self.coordinator.data.status)
            if reported is not None:
                return reported
        return self._held

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the setting."""
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the setting."""
        await self._set(False)

    async def _set(self, on: bool) -> None:
        try:
            await self.coordinator.client.set_toggle(self.entity_description.key, on)
        except SharklocalError as err:
            raise HomeAssistantError(
                f"Could not change {self.entity_description.key} on "
                f"{self.coordinator.host}: {err}"
            ) from err
        self._held = on
        if self.entity_description.value_fn is None:
            self.async_write_ha_state()
        # Reported settings wait for the robot's answer in the next frame.
        await self.coordinator.async_request_refresh()
