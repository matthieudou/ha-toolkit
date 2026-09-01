"""Domain models for HA Toolkit meter entries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    UnitOfEnergy,
    UnitOfPower,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.util.unit_conversion import (
    BaseUnitConverter,
    EnergyConverter,
    PowerConverter,
    VolumeConverter,
    VolumeFlowRateConverter,
)

from custom_components.ha_toolkit.const import (
    CONF_GROUP_ENTITY_IDS,
    CONF_GROUP_ID,
    CONF_GROUP_NAME,
)


class SourceMode(StrEnum):
    """How source readings become a cumulative series."""

    CUMULATIVE = "cumulative"
    RATE = "rate"


class ConfigurationType(StrEnum):
    """Supported meter configurations."""

    ELECTRICITY_ENERGY = "electricity_energy"
    ELECTRICITY_POWER = "electricity_power"
    WATER_VOLUME = "water_volume"
    WATER_FLOW = "water_flow"
    GAS_VOLUME = "gas_volume"
    GAS_FLOW = "gas_flow"


@dataclass(frozen=True, slots=True)
class MeasurementSpec:
    """Home Assistant metadata and normalization rules for one configuration."""

    source_device_class: SensorDeviceClass
    source_state_classes: frozenset[SensorStateClass]
    source_unit_class: str
    normalized_source_unit: str
    result_device_class: SensorDeviceClass
    result_unit_class: str
    result_unit: str
    quantity: str
    mode: SourceMode
    converter: type[BaseUnitConverter]

    def normalize(self, value: float, unit: str) -> float:
        """Convert a source value to the configuration's canonical unit."""
        return self.converter.convert(value, unit, self.normalized_source_unit)


MEASUREMENT_SPECS: dict[ConfigurationType, MeasurementSpec] = {
    ConfigurationType.ELECTRICITY_ENERGY: MeasurementSpec(
        source_device_class=SensorDeviceClass.ENERGY,
        source_state_classes=frozenset(
            {SensorStateClass.TOTAL, SensorStateClass.TOTAL_INCREASING}
        ),
        source_unit_class="energy",
        normalized_source_unit=UnitOfEnergy.KILO_WATT_HOUR,
        result_device_class=SensorDeviceClass.ENERGY,
        result_unit_class="energy",
        result_unit=UnitOfEnergy.KILO_WATT_HOUR,
        quantity="energy",
        mode=SourceMode.CUMULATIVE,
        converter=EnergyConverter,
    ),
    ConfigurationType.ELECTRICITY_POWER: MeasurementSpec(
        source_device_class=SensorDeviceClass.POWER,
        source_state_classes=frozenset({SensorStateClass.MEASUREMENT}),
        source_unit_class="power",
        normalized_source_unit=UnitOfPower.KILO_WATT,
        result_device_class=SensorDeviceClass.ENERGY,
        result_unit_class="energy",
        result_unit=UnitOfEnergy.KILO_WATT_HOUR,
        quantity="energy",
        mode=SourceMode.RATE,
        converter=PowerConverter,
    ),
    ConfigurationType.WATER_VOLUME: MeasurementSpec(
        source_device_class=SensorDeviceClass.WATER,
        source_state_classes=frozenset(
            {SensorStateClass.TOTAL, SensorStateClass.TOTAL_INCREASING}
        ),
        source_unit_class="volume",
        normalized_source_unit=UnitOfVolume.CUBIC_METERS,
        result_device_class=SensorDeviceClass.WATER,
        result_unit_class="volume",
        result_unit=UnitOfVolume.CUBIC_METERS,
        quantity="water",
        mode=SourceMode.CUMULATIVE,
        converter=VolumeConverter,
    ),
    ConfigurationType.WATER_FLOW: MeasurementSpec(
        source_device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        source_state_classes=frozenset({SensorStateClass.MEASUREMENT}),
        source_unit_class="volume_flow_rate",
        normalized_source_unit=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        result_device_class=SensorDeviceClass.WATER,
        result_unit_class="volume",
        result_unit=UnitOfVolume.CUBIC_METERS,
        quantity="water",
        mode=SourceMode.RATE,
        converter=VolumeFlowRateConverter,
    ),
    ConfigurationType.GAS_VOLUME: MeasurementSpec(
        source_device_class=SensorDeviceClass.GAS,
        source_state_classes=frozenset(
            {SensorStateClass.TOTAL, SensorStateClass.TOTAL_INCREASING}
        ),
        source_unit_class="volume",
        normalized_source_unit=UnitOfVolume.CUBIC_METERS,
        result_device_class=SensorDeviceClass.GAS,
        result_unit_class="volume",
        result_unit=UnitOfVolume.CUBIC_METERS,
        quantity="gas",
        mode=SourceMode.CUMULATIVE,
        converter=VolumeConverter,
    ),
    ConfigurationType.GAS_FLOW: MeasurementSpec(
        source_device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        source_state_classes=frozenset({SensorStateClass.MEASUREMENT}),
        source_unit_class="volume_flow_rate",
        normalized_source_unit=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        result_device_class=SensorDeviceClass.GAS,
        result_unit_class="volume",
        result_unit=UnitOfVolume.CUBIC_METERS,
        quantity="gas",
        mode=SourceMode.RATE,
        converter=VolumeFlowRateConverter,
    ),
}


@dataclass(frozen=True, slots=True)
class MeterSource:
    """One configured cumulative meter or instantaneous rate sensor."""

    registry_entry_id: str
    entity_id: str
    device_id: str | None
    name: str
    unit: str
    configuration_type: ConfigurationType

    @property
    def key(self) -> str:
        """Return an identifier that survives an entity-id rename when possible."""
        return self.registry_entry_id or self.entity_id

    @property
    def spec(self) -> MeasurementSpec:
        """Return normalization and output metadata."""
        return MEASUREMENT_SPECS[self.configuration_type]


class TargetKind(StrEnum):
    """Kinds of configured targets exposed by the integration."""

    SOURCE = "source"
    GROUP = "group"


@dataclass(slots=True)
class MeterGroup:
    """One named group in a stored meter configuration."""

    group_id: str
    name: str
    entity_ids: list[str]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MeterGroup:
        """Parse the serializable config-entry representation."""
        return cls(
            group_id=str(value[CONF_GROUP_ID]),
            name=str(value[CONF_GROUP_NAME]),
            entity_ids=list(value.get(CONF_GROUP_ENTITY_IDS, [])),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the serializable config-entry representation."""
        return {
            CONF_GROUP_ID: self.group_id,
            CONF_GROUP_NAME: self.name,
            CONF_GROUP_ENTITY_IDS: self.entity_ids,
        }


@dataclass(frozen=True, slots=True)
class MeterTarget:
    """An individual source or named aggregate exposed as sensors."""

    key: str
    kind: TargetKind
    name: str
    sources: tuple[MeterSource, ...]
    attach_to_device: bool = False
    device_id: str | None = None
    source_entity_id: str | None = None
