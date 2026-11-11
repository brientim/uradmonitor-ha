"""Radiation and air quality from uRADMonitor units, via the cloud API and/or each unit's local /j."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import URadConfigEntry, URadCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: URadConfigEntry) -> bool:
    """Read the units once, then set up their entities."""
    coordinator = URadCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: URadConfigEntry) -> bool:
    """Unload the entities."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: URadConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting a unit that no source reports any more."""
    return not any(
        identifier[0] == DOMAIN and identifier[1] in entry.runtime_data.data
        for identifier in device.identifiers
    )
