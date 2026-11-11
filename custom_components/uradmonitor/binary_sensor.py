"""Whether each uRADMonitor unit is online."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import URadConfigEntry, URadCoordinator
from .entity import URadEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: URadConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a status sensor for every unit, including units that appear later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new() -> None:
        new = [unit_id for unit_id in coordinator.data if unit_id not in known]
        known.update(new)
        if new:
            async_add_entities(URadStatusSensor(coordinator, unit_id) for unit_id in new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class URadStatusSensor(URadEntity, BinarySensorEntity):
    """On while the unit reports to the cloud or answers locally."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: URadCoordinator, unit_id: str) -> None:
        """Attach to the unit."""
        super().__init__(coordinator, unit_id, "status")

    @property
    def is_on(self) -> bool | None:
        """Whether the unit is online."""
        unit = self.unit
        return unit.online if unit is not None else None
