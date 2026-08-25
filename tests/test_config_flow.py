"""Tests for the MattsAssistant config flow."""

from homeassistant import config_entries
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mattsassistant.const import (
    CONF_ATTACH_ENTITY_IDS,
    CONF_CONFIGURATION_TYPE,
    CONF_GROUP_ENTITY_IDS,
    CONF_GROUP_NAME,
    CONF_GROUPS,
    CONF_PRICE_ENTITY_ID,
    CONF_SOURCE_ENTITY_IDS,
    DOMAIN,
)
from custom_components.mattsassistant.models import ConfigurationType


def _add_energy_source(hass: HomeAssistant) -> str:
    MockConfigEntry(domain="test", entry_id="source").add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="source", identifiers={("test", "plug")}, name="Test plug"
    )
    entry = er.async_get(hass).async_get_or_create(
        "sensor",
        "test",
        "plug_energy",
        suggested_object_id="kitchen_dishwasher_energy",
        device_id=device.id,
    )
    hass.states.async_set(
        entry.entity_id,
        "12",
        {
            "device_class": SensorDeviceClass.ENERGY,
            "state_class": SensorStateClass.TOTAL_INCREASING,
            "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
        },
    )
    return entry.entity_id


async def _start_flow(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CONFIGURATION_TYPE: ConfigurationType.ELECTRICITY_ENERGY.value},
    )


async def test_user_flow_stores_sources_device_links_and_price(
    recorder_mock: object, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    """A typed configuration stores explicit source entities and pricing."""
    del recorder_mock, enable_custom_integrations
    entity_id = _add_energy_source(hass)
    hass.states.async_set("input_number.electricity_price", "0.32")
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SOURCE_ENTITY_IDS: [entity_id],
            CONF_PRICE_ENTITY_ID: "input_number.electricity_price",
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ATTACH_ENTITY_IDS: [entity_id]}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SOURCE_ENTITY_IDS] == [entity_id]
    assert result["data"][CONF_ATTACH_ENTITY_IDS] == [entity_id]
    assert result["data"][CONF_PRICE_ENTITY_ID] == ("input_number.electricity_price")


async def test_flow_rejects_a_non_numeric_price(
    recorder_mock: object, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    """Cost sensors cannot be configured from an unusable current price."""
    del recorder_mock, enable_custom_integrations
    entity_id = _add_energy_source(hass)
    hass.states.async_set("input_number.electricity_price", "unknown")
    result = await _start_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SOURCE_ENTITY_IDS: [entity_id],
            CONF_PRICE_ENTITY_ID: "input_number.electricity_price",
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_price"}


async def test_flow_can_create_a_group_without_individual_sensors(
    recorder_mock: object, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    """Group members do not need duplicate individual sensor generation."""
    del recorder_mock, enable_custom_integrations
    entity_id = _add_energy_source(hass)
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOURCE_ENTITY_IDS: []}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "add_group"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_GROUP_NAME: "Housekeeping",
            CONF_GROUP_ENTITY_IDS: [entity_id],
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SOURCE_ENTITY_IDS] == []
    assert result["data"][CONF_GROUPS][0][CONF_GROUP_NAME] == "Housekeeping"


async def test_flow_requires_an_individual_sensor_or_group(
    recorder_mock: object, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    """An empty configuration cannot silently create no entities."""
    del recorder_mock, enable_custom_integrations
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOURCE_ENTITY_IDS: []}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_targets"}
