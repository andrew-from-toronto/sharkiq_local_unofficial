"""Select platform for Shark IQ (Local): suction level."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from sharklocal import SharklocalError
from sharklocal.models import SuctionLevel

from .const import CONF_NAME, DOMAIN
from .coordinator import SharkCoordinator
from .entity import SharkBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the suction select (MQTT only)."""
    coordinator: SharkCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.use_mqtt:
        async_add_entities([SharkSuctionSelect(coordinator, entry.data[CONF_NAME])])


class SharkSuctionSelect(SharkBaseEntity, SelectEntity, RestoreEntity):
    """Suction level: the same setting as the vacuum's fan speed, as its own control."""

    _attr_translation_key = "suction"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [level.value for level in SuctionLevel]

    def __init__(self, coordinator: SharkCoordinator, entry_title: str) -> None:
        """Initialize."""
        super().__init__(coordinator, entry_title)
        self._attr_unique_id = f"{coordinator.unique_id}_suction"

    async def async_added_to_hass(self) -> None:
        """Restore the last level set, which the robot never reports."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self.coordinator.restore_suction(last.state)

    @property
    def current_option(self) -> str | None:
        """The last suction level set."""
        return self.coordinator.suction.value if self.coordinator.suction else None

    async def async_select_option(self, option: str) -> None:
        """Set the suction level."""
        try:
            await self.coordinator.async_set_suction(SuctionLevel(option))
        except SharklocalError as err:
            raise HomeAssistantError(
                f"Could not set suction on {self.coordinator.host}: {err}"
            ) from err
