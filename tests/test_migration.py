"""Tests for migrations from unpublished MattsAssistant prototypes."""

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mattsassistant import async_migrate_entry
from custom_components.mattsassistant.const import (
    CONF_CONFIGURATION_TYPE,
    CONF_DEVICE_IDS,
    CONF_SOURCE_ENTITY_IDS,
    DOMAIN,
)
from custom_components.mattsassistant.models import ConfigurationType


async def test_migration_preserves_existing_entity_id_and_unique_id_history(
    hass: HomeAssistant,
) -> None:
    """Version-two entities are namespaced without being recreated."""
    MockConfigEntry(domain="test", entry_id="source-config").add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="source-config",
        identifiers={("test", "plug")},
        name="Test plug",
    )
    registry = er.async_get(hass)
    source = registry.async_get_or_create(
        "sensor",
        "test",
        "plug_energy",
        suggested_object_id="plug_energy",
        device_id=device.id,
    )
    hass.states.async_set(
        source.entity_id,
        "12",
        {
            "device_class": SensorDeviceClass.ENERGY,
            "state_class": SensorStateClass.TOTAL_INCREASING,
            "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
        },
    )
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="mattsassistant-config",
        unique_id=DOMAIN,
        version=2,
        data={CONF_DEVICE_IDS: [device.id]},
    )
    config_entry.add_to_hass(hass)
    generated = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{source.id}_total",
        config_entry=config_entry,
        suggested_object_id="test_plug_total",
    )

    assert await async_migrate_entry(hass, config_entry)

    migrated = registry.async_get(generated.entity_id)
    assert migrated is not None
    assert migrated.unique_id == f"{config_entry.entry_id}_{source.id}_total"
    assert config_entry.data[CONF_CONFIGURATION_TYPE] == (
        ConfigurationType.ELECTRICITY_ENERGY.value
    )
    assert config_entry.data[CONF_SOURCE_ENTITY_IDS] == [source.entity_id]
