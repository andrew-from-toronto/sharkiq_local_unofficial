"""Number platform for Shark IQ (Local): voice volume."""
from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from sharklocal import SharklocalError

from .const import CONF_NAME, DOMAIN
from .coordinator import SharkCoordinator
from .entity import SharkBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the volume control (MQTT only)."""
    coordinator: SharkCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.use_mqtt and coordinator.capabilities.has_volume:
        async_add_entities([SharkVolume(coordinator, entry.data[CONF_NAME])])


class SharkVolume(SharkBaseEntity, RestoreNumber):
    """The robot's voice volume, 0-100 as the app's slider has it.

    The robot does not report it, so the last value set is held and restored.
    """

    _attr_translation_key = "volume"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: SharkCoordinator, entry_title: str) -> None:
        """Initialize."""
        super().__init__(coordinator, entry_title)
        self._attr_unique_id = f"{coordinator.unique_id}_volume"
        self._attr_native_value = None

    async def async_added_to_hass(self) -> None:
        """Restore the last volume set."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_number_data()) is not None:
            self._attr_native_value = last.native_value

    async def async_set_native_value(self, value: float) -> None:
        """Set the volume."""
        try:
            await self.coordinator.client.set_volume(round(value))
        except SharklocalError as err:
            raise HomeAssistantError(
                f"Could not set the volume on {self.coordinator.host}: {err}"
            ) from err
        self._attr_native_value = round(value)
        self.async_write_ha_state()
