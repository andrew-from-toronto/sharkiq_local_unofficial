"""Services for Shark IQ (Local)."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.components.vacuum import DOMAIN as VACUUM_DOMAIN, VacuumEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, service

from .const import DOMAIN

SERVICE_CLEAN_ROOMS = "clean_rooms"
SERVICE_CLEAN_SPOT = "clean_spot"


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the room and spot cleaning services."""
    # HA's own vacuum.clean_area covers a plain room clean by area; this one
    # takes room names directly and adds Matrix Clean, which clean_area cannot
    # express.
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_CLEAN_ROOMS,
        entity_domain=VACUUM_DOMAIN,
        schema=cv.make_entity_service_schema(
            {
                vol.Required("rooms"): vol.All(cv.ensure_list, [cv.string], vol.Length(min=1)),
                vol.Optional("matrix", default=False): cv.boolean,
            }
        ),
        func="async_clean_rooms",
        required_features=[VacuumEntityFeature.CLEAN_AREA],
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_CLEAN_SPOT,
        entity_domain=VACUUM_DOMAIN,
        schema=cv.make_entity_service_schema(
            {
                vol.Required("x"): vol.Coerce(float),
                vol.Required("y"): vol.Coerce(float),
            }
        ),
        func="async_clean_spot_at",
        required_features=[VacuumEntityFeature.CLEAN_AREA],
    )
