"""Base entity for a uRADMonitor unit."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import URadCoordinator
from .protocol import UnitSnapshot, base_url


class URadEntity(CoordinatorEntity[URadCoordinator]):
    """An entity on one unit's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: URadCoordinator, unit_id: str, key: str) -> None:
        """Attach to the unit's device."""
        super().__init__(coordinator)
        self.unit_id = unit_id
        self._attr_unique_id = f"{unit_id}_{key}"
        unit = coordinator.data[unit_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unit_id)},
            manufacturer=MANUFACTURER,
            model="uRADMonitor",
            name=f"uRADMonitor {unit_id}",
            serial_number=unit_id,
            hw_version=_version(unit.version_hw),
            sw_version=_version(unit.version_sw),
            configuration_url=self._local_url(),
        )

    @property
    def unit(self) -> UnitSnapshot | None:
        """The unit's latest merged snapshot, if any source still reports it."""
        return self.coordinator.data.get(self.unit_id)

    @property
    def available(self) -> bool:
        """Available while a source reports the unit."""
        return super().available and self.unit is not None

    def _local_url(self) -> str | None:
        for status in self.coordinator.local_statuses.values():
            if status.unit_id == self.unit_id:
                return base_url(status.endpoint.host, status.endpoint.port)
        return None


def _version(value: int | None) -> str | None:
    return str(value) if value is not None else None
