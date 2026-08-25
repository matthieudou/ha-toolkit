"""Tests for explicit meter-source resolution."""

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mattsassistant.discovery import (
    is_supported_sensor,
    resolve_meter_targets,
)
from custom_components.mattsassistant.models import (
    ConfigurationType,
    MeterGroup,
    TargetKind,
)


def _energy_attributes() -> dict[str, str]:
    return {
        "device_class": SensorDeviceClass.ENERGY,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
    }


async def test_resolves_multiple_explicit_sensors_on_the_same_device(
    hass: HomeAssistant,
) -> None:
    """A multi-channel device may expose more than one selected meter."""
    MockConfigEntry(domain="test", entry_id="source").add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="source", identifiers={("test", "strip")}, name="Power strip"
    )
    registry = er.async_get(hass)
    entity_ids = []
    for channel in (1, 2):
        entry = registry.async_get_or_create(
            "sensor",
            "test",
            f"channel_{channel}",
            suggested_object_id=f"strip_channel_{channel}_energy",
            device_id=device.id,
        )
        hass.states.async_set(entry.entity_id, str(channel), _energy_attributes())
        entity_ids.append(entry.entity_id)

    targets = resolve_meter_targets(
        hass,
        ConfigurationType.ELECTRICITY_ENERGY,
        entity_ids,
        entity_ids,
        [],
    )

    assert len(targets) == 2
    assert {target.source_entity_id for target in targets} == set(entity_ids)
    assert all(target.attach_to_device for target in targets)


async def test_meter_does_not_need_a_switch(hass: HomeAssistant) -> None:
    """A measurement-only device remains eligible."""
    MockConfigEntry(domain="test", entry_id="source").add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="source", identifiers={("test", "meter")}, name="House meter"
    )
    entry = er.async_get(hass).async_get_or_create(
        "sensor",
        "test",
        "house_energy",
        suggested_object_id="house_energy",
        device_id=device.id,
    )
    hass.states.async_set(entry.entity_id, "1234", _energy_attributes())

    targets = resolve_meter_targets(
        hass,
        ConfigurationType.ELECTRICITY_ENERGY,
        [entry.entity_id],
        [entry.entity_id],
        [],
    )

    assert len(targets) == 1
    assert targets[0].device_id == device.id


async def test_explicit_group_can_reuse_an_individual_source(
    hass: HomeAssistant,
) -> None:
    """One source may feed its own sensors and several named groups."""
    entry = er.async_get(hass).async_get_or_create(
        "sensor", "test", "dishwasher", suggested_object_id="dishwasher_energy"
    )
    hass.states.async_set(entry.entity_id, "12", _energy_attributes())
    groups = [MeterGroup("housekeeping", "Housekeeping", [entry.entity_id])]

    targets = resolve_meter_targets(
        hass,
        ConfigurationType.ELECTRICITY_ENERGY,
        [entry.entity_id],
        [],
        groups,
    )

    assert {target.kind for target in targets} == {
        TargetKind.SOURCE,
        TargetKind.GROUP,
    }
    aggregate = next(target for target in targets if target.kind is TargetKind.GROUP)
    assert aggregate.name == "Housekeeping"
    assert aggregate.sources[0].entity_id == entry.entity_id


async def test_group_is_omitted_when_one_configured_member_is_missing(
    hass: HomeAssistant,
) -> None:
    """An unresolved member never produces a partial aggregate."""
    entry = er.async_get(hass).async_get_or_create(
        "sensor", "test", "dishwasher", suggested_object_id="dishwasher_energy"
    )
    hass.states.async_set(entry.entity_id, "12", _energy_attributes())
    groups = [
        MeterGroup(
            "housekeeping",
            "Housekeeping",
            [entry.entity_id, "sensor.missing_energy"],
        )
    ]

    targets = resolve_meter_targets(
        hass, ConfigurationType.ELECTRICITY_ENERGY, [], [], groups
    )

    assert targets == ()


def test_configuration_type_rejects_another_quantity() -> None:
    """The first flow step controls accepted sensor metadata."""
    assert not is_supported_sensor(_energy_attributes(), ConfigurationType.WATER_VOLUME)
