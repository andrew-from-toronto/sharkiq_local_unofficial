"""Button platform for Shark IQ (Local): one-shot jobs."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from sharklocal import SharklocalError, VacuumClient

from .const import CONF_NAME, DOMAIN
from .coordinator import SharkCoordinator
from .entity import SharkBaseEntity


@dataclass(frozen=True, kw_only=True)
class SharkButtonDescription(ButtonEntityDescription):
    """Describes a Shark job button."""

    press_fn: Callable[[VacuumClient], Awaitable[bool]]


BUTTONS: tuple[SharkButtonDescription, ...] = (
    SharkButtonDescription(
        key="edge_clean",
        translation_key="edge_clean",
        press_fn=lambda client: client.edge_clean(),
    ),
    # An explore run remaps the floor; disabled so nobody starts one by accident.
    SharkButtonDescription(
        key="explore",
        translation_key="explore",
        entity_registry_enabled_default=False,
        press_fn=lambda client: client.explore(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the job buttons (MQTT only)."""
    coordinator: SharkCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.use_mqtt:
        return
    name = entry.data[CONF_NAME]
    async_add_entities(SharkButton(coordinator, name, desc) for desc in BUTTONS)


class SharkButton(SharkBaseEntity, ButtonEntity):
    """Start a one-shot job."""

    entity_description: SharkButtonDescription

    def __init__(
        self,
        coordinator: SharkCoordinator,
        entry_title: str,
        description: SharkButtonDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry_title)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.unique_id}_{description.key}"

    async def async_press(self) -> None:
        """Start the job."""
        try:
            await self.entity_description.press_fn(self.coordinator.client)
        except SharklocalError as err:
            raise HomeAssistantError(
                f"{self.entity_description.key} failed for {self.coordinator.host}: {err}"
            ) from err
        self.coordinator.set_job_target(None)
        await self.coordinator.async_request_refresh()
