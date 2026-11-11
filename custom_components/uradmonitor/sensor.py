"""Sensors for each uRADMonitor reading, added as units report them."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfPressure,
    UnitOfSoundPressure,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import URadConfigEntry, URadCoordinator
from .entity import URadEntity
from .protocol import (
    AQI_BANDS,
    AQI_KEY,
    CPM_KEY,
    DOSE_KEY,
    UPTIME_KEY,
    Reading,
    Source,
    UnitSnapshot,
)

PARALLEL_UPDATES = 0

# Literal units: HA 2026.11 deprecates the CONCENTRATION_* constants, and their
# replacements (UnitOfDensity, UnitOfRatio) don't exist in older versions.
MICROGRAMS_PER_CUBIC_METER = "μg/m³"
PARTS_PER_BILLION = "ppb"
PARTS_PER_MILLION = "ppm"


@dataclass(frozen=True, kw_only=True)
class URadReadingDescription(SensorEntityDescription):
    """A reading sensor; ``value_fn`` turns the normalized value into the state."""

    value_fn: Callable[[float], StateType] = lambda value: value


@dataclass(frozen=True, kw_only=True)
class URadUnitDescription(SensorEntityDescription):
    """A sensor about the unit itself rather than one of its readings."""

    value_fn: Callable[[UnitSnapshot], StateType | datetime]


def _aqi(value: float) -> str | None:
    band = round(value)
    return AQI_BANDS[band - 1] if 1 <= band <= len(AQI_BANDS) else None


def _measurement(key: str, **kwargs) -> URadReadingDescription:
    return URadReadingDescription(key=key, state_class=SensorStateClass.MEASUREMENT, **kwargs)


READINGS: dict[str, URadReadingDescription] = {
    description.key: description
    for description in (
        _measurement(
            DOSE_KEY,
            translation_key="dose",
            native_unit_of_measurement="μSv/h",
            suggested_display_precision=3,
        ),
        _measurement(
            CPM_KEY,
            translation_key="cpm",
            native_unit_of_measurement="CPM",
            suggested_display_precision=0,
        ),
        URadReadingDescription(
            key=AQI_KEY,
            translation_key="aqi",
            device_class=SensorDeviceClass.ENUM,
            options=list(AQI_BANDS),
            value_fn=_aqi,
        ),
        _measurement(
            "temperature",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            suggested_display_precision=1,
        ),
        _measurement(
            "humidity",
            device_class=SensorDeviceClass.HUMIDITY,
            native_unit_of_measurement=PERCENTAGE,
            suggested_display_precision=1,
        ),
        _measurement(
            "pressure",
            device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
            native_unit_of_measurement=UnitOfPressure.HPA,
            suggested_display_precision=1,
        ),
        _measurement(
            "co2",
            device_class=SensorDeviceClass.CO2,
            native_unit_of_measurement=PARTS_PER_MILLION,
            suggested_display_precision=0,
        ),
        _measurement(
            "ch2o",
            translation_key="ch2o",
            native_unit_of_measurement=PARTS_PER_BILLION,
            suggested_display_precision=0,
        ),
        _measurement(
            "voc",
            translation_key="voc",
            native_unit_of_measurement="Ω",
            suggested_display_precision=0,
        ),
        _measurement("vocaqi", translation_key="vocaqi", suggested_display_precision=0),
        _measurement(
            "o3",
            translation_key="o3",
            native_unit_of_measurement=PARTS_PER_BILLION,
            suggested_display_precision=0,
        ),
        _measurement(
            "pm1",
            device_class=SensorDeviceClass.PM1,
            native_unit_of_measurement=MICROGRAMS_PER_CUBIC_METER,
            suggested_display_precision=0,
        ),
        _measurement(
            "pm25",
            device_class=SensorDeviceClass.PM25,
            native_unit_of_measurement=MICROGRAMS_PER_CUBIC_METER,
            suggested_display_precision=0,
        ),
        _measurement(
            "pm10",
            device_class=SensorDeviceClass.PM10,
            native_unit_of_measurement=MICROGRAMS_PER_CUBIC_METER,
            suggested_display_precision=0,
        ),
        _measurement(
            "noise",
            translation_key="noise",
            device_class=SensorDeviceClass.SOUND_PRESSURE,
            native_unit_of_measurement=UnitOfSoundPressure.DECIBEL,
            suggested_display_precision=1,
        ),
        _measurement(
            "radon",
            translation_key="radon",
            native_unit_of_measurement="Bq/m³",
            suggested_display_precision=0,
        ),
        _measurement(
            "voltage",
            translation_key="voltage",
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            suggested_display_precision=0,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        _measurement(
            "duty",
            translation_key="duty",
            native_unit_of_measurement="‰",
            suggested_display_precision=0,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        _measurement(
            "signal",
            device_class=SensorDeviceClass.SIGNAL_STRENGTH,
            native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
            suggested_display_precision=0,
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
        ),
        URadReadingDescription(
            key=UPTIME_KEY,
            translation_key="uptime",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            suggested_unit_of_measurement=UnitOfTime.DAYS,
            suggested_display_precision=1,
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
        ),
    )
}

UNIT_SENSORS: tuple[URadUnitDescription, ...] = (
    URadUnitDescription(
        key="source",
        translation_key="source",
        device_class=SensorDeviceClass.ENUM,
        options=[Source.LOCAL.value, Source.CLOUD.value],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda unit: unit.source.value if unit.source != Source.NONE else None,
    ),
    URadUnitDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda unit: unit.last_seen,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: URadConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors now, and again whenever a unit reports a new unit or sensor."""
    coordinator = entry.runtime_data
    known: set[tuple[str, str]] = set()

    @callback
    def add_new() -> None:
        entities: list[SensorEntity] = []
        for unit in coordinator.data.values():
            for description in UNIT_SENSORS:
                if (unit.id, description.key) not in known:
                    known.add((unit.id, description.key))
                    entities.append(URadUnitSensor(coordinator, unit.id, description))
            for reading in unit.readings.values():
                if (unit.id, reading.key) not in known:
                    known.add((unit.id, reading.key))
                    entities.append(URadReadingSensor(coordinator, unit.id, reading))
        if entities:
            async_add_entities(entities)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class URadReadingSensor(URadEntity, SensorEntity):
    """One reading of a unit; unavailable while the unit is offline."""

    entity_description: URadReadingDescription

    def __init__(self, coordinator: URadCoordinator, unit_id: str, reading: Reading) -> None:
        """Use the known description, or the unit's own name and unit for anything else."""
        super().__init__(coordinator, unit_id, reading.key)
        self.key = reading.key
        known = READINGS.get(reading.key)
        if known is not None:
            self.entity_description = known
        else:
            self.entity_description = URadReadingDescription(
                key=reading.key,
                name=reading.name,
                native_unit_of_measurement=reading.unit,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=2,
            )

    @property
    def reading(self) -> Reading | None:
        """The latest value, while the unit is online and reports it."""
        unit = self.unit
        return unit.readings.get(self.key) if unit is not None and unit.online else None

    @property
    def available(self) -> bool:
        """Available while the unit is online and reports this sensor."""
        return super().available and self.reading is not None

    @property
    def native_value(self) -> StateType:
        """The reading in the description's unit."""
        reading = self.reading
        return self.entity_description.value_fn(reading.value) if reading else None


class URadUnitSensor(URadEntity, SensorEntity):
    """Where the unit's data came from and when it was last seen."""

    entity_description: URadUnitDescription

    def __init__(
        self, coordinator: URadCoordinator, unit_id: str, description: URadUnitDescription
    ) -> None:
        """Attach the description."""
        super().__init__(coordinator, unit_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        """The unit's value."""
        unit = self.unit
        return self.entity_description.value_fn(unit) if unit is not None else None
