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
        name = entry.data[CONF_NAME]
        async_add_entities(
            [SharkSuctionSelect(coordinator, name), SharkCarpetDetectSelect(coordinator, name)]
        )


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


# sharklocal.codes.CARPET_DETECT_MODES values the app offers.
CARPET_DETECT = {"auto": 2, "off": 1}


class SharkCarpetDetectSelect(SharkBaseEntity, SelectEntity, RestoreEntity):
    """Carpet detection: automatic, or off.

    The robot reports this one back (status field 44); until it does, the last
    value set here is shown. Not yet seen on this model, so disabled by default.
    """

    _attr_translation_key = "carpet_detect"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_options = list(CARPET_DETECT)

    def __init__(self, coordinator: SharkCoordinator, entry_title: str) -> None:
        """Initialize."""
        super().__init__(coordinator, entry_title)
        self._attr_unique_id = f"{coordinator.unique_id}_carpet_detect"
        self._held: str | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last value set."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state in CARPET_DETECT:
            self._held = last.state

    @property
    def current_option(self) -> str | None:
        """As the robot reports it, else as last set here."""
        data = self.coordinator.data
        if data is not None and data.status.carpet_detect is not None:
            for option, value in CARPET_DETECT.items():
                if value == data.status.carpet_detect:
                    return option
        return self._held

    async def async_select_option(self, option: str) -> None:
        """Set carpet detection."""
        try:
            await self.coordinator.client.set_carpet_detect(CARPET_DETECT[option])
        except SharklocalError as err:
            raise HomeAssistantError(
                f"Could not set carpet detection on {self.coordinator.host}: {err}"
            ) from err
        self._held = option
        self.async_write_ha_state()
