"""Tests for the HA Toolkit config flow."""

from homeassistant import config_entries
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_toolkit.config_flow import (
    CONF_ENTITY_ID,
    CONF_INDIVIDUAL_SOURCES,
    CONF_KEEP_SEPARATE,
)
from custom_components.ha_toolkit.const import (
    CONF_ATTACH_ENTITY_IDS,
    CONF_CONFIGURATION_TYPE,
    CONF_GROUP_ENTITY_IDS,
    CONF_GROUP_NAME,
    CONF_GROUPS,
    CONF_MEMBER_ENTITY_IDS,
    CONF_NAME,
    CONF_PRICE_ENTITY_ID,
    CONF_SCENE_ENTITY_IDS,
    CONF_SOURCE_ENTITY_IDS,
    CONFIGURATION_TYPE_LIGHT_GROUP_PLUS,
    DOMAIN,
)
from custom_components.ha_toolkit.energy.models import ConfigurationType


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


async def test_user_flow_stores_all_settings_from_one_page(
    recorder_mock: object, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    """The second and final creation page stores sources, groups, and pricing."""
    del recorder_mock, enable_custom_integrations
    entity_id = _add_energy_source(hass)
    hass.states.async_set("input_number.electricity_price", "0.32")
    result = await _start_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "configuration"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_INDIVIDUAL_SOURCES: [
                {CONF_ENTITY_ID: entity_id, CONF_KEEP_SEPARATE: False}
            ],
            CONF_GROUPS: [
                {
                    CONF_GROUP_NAME: "Housekeeping",
                    CONF_GROUP_ENTITY_IDS: [entity_id],
                }
            ],
            CONF_PRICE_ENTITY_ID: "input_number.electricity_price",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SOURCE_ENTITY_IDS] == [entity_id]
    assert result["data"][CONF_ATTACH_ENTITY_IDS] == [entity_id]
    assert result["data"][CONF_GROUPS][0][CONF_GROUP_NAME] == "Housekeeping"
    assert result["data"][CONF_PRICE_ENTITY_ID] == "input_number.electricity_price"


async def test_flow_rejects_a_non_numeric_price(
    recorder_mock: object, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    """A price must currently expose a usable numeric value."""
    del recorder_mock, enable_custom_integrations
    entity_id = _add_energy_source(hass)
    hass.states.async_set("input_number.electricity_price", "unknown")
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_INDIVIDUAL_SOURCES: [
                {CONF_ENTITY_ID: entity_id, CONF_KEEP_SEPARATE: False}
            ],
            CONF_PRICE_ENTITY_ID: "input_number.electricity_price",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_price"}


async def test_options_flow_is_one_page_and_preserves_group_id(
    recorder_mock: object, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    """Editing has no nested menu and renaming keeps the virtual device identity."""
    del recorder_mock, enable_custom_integrations
    entity_id = _add_energy_source(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONFIGURATION_TYPE: ConfigurationType.ELECTRICITY_ENERGY.value,
            CONF_SOURCE_ENTITY_IDS: [],
            CONF_ATTACH_ENTITY_IDS: [],
            CONF_GROUPS: [
                {
                    "id": "stable-id",
                    CONF_GROUP_NAME: "Old name",
                    CONF_GROUP_ENTITY_IDS: [entity_id],
                }
            ],
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "configuration"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_INDIVIDUAL_SOURCES: [],
            CONF_GROUPS: [
                {
                    CONF_GROUP_NAME: "New name",
                    CONF_GROUP_ENTITY_IDS: [entity_id],
                }
            ],
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_GROUPS][0]["id"] == "stable-id"


async def test_options_flow_projects_existing_device_links_into_rows(
    recorder_mock: object, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    """Legacy storage is exposed as editable individual-source rows."""
    del recorder_mock, enable_custom_integrations
    entity_id = _add_energy_source(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONFIGURATION_TYPE: ConfigurationType.ELECTRICITY_ENERGY.value,
            CONF_SOURCE_ENTITY_IDS: [entity_id],
            CONF_ATTACH_ENTITY_IDS: [],
            CONF_GROUPS: [],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    schema = result["data_schema"].schema
    individual_field = next(
        field for field in schema if field.schema == CONF_INDIVIDUAL_SOURCES
    )
    assert individual_field.default() == [
        {CONF_ENTITY_ID: entity_id, CONF_KEEP_SEPARATE: True}
    ]


async def test_flow_rejects_duplicate_individual_sources(
    recorder_mock: object, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    """One source cannot create the same derived meter twice."""
    del recorder_mock, enable_custom_integrations
    entity_id = _add_energy_source(hass)
    result = await _start_flow(hass)
    row = {CONF_ENTITY_ID: entity_id, CONF_KEEP_SEPARATE: False}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_INDIVIDUAL_SOURCES: [row, row]}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_INDIVIDUAL_SOURCES: "duplicate_source"}


async def test_flow_requires_an_individual_sensor_or_group(
    recorder_mock: object, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    """An empty configuration cannot silently create no entities."""
    del recorder_mock, enable_custom_integrations
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_targets"}


async def test_light_group_plus_flow_stores_members_and_ordered_scenes(
    recorder_mock: object, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    """Light Group+ uses a separate form and stores native entity IDs."""
    del recorder_mock, enable_custom_integrations
    hass.states.async_set("light.salon", "off")
    hass.states.async_set("light.canape", "off")
    hass.states.async_set(
        "scene.salon_default", "scening", {"friendly_name": "Default"}
    )
    hass.states.async_set("scene.salon_tv", "scening", {"friendly_name": "TV"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CONFIGURATION_TYPE: CONFIGURATION_TYPE_LIGHT_GROUP_PLUS},
    )

    assert result["step_id"] == "light_group_plus"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Salon",
            CONF_MEMBER_ENTITY_IDS: ["light.salon", "light.canape"],
            CONF_SCENE_ENTITY_IDS: ["scene.salon_default", "scene.salon_tv"],
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Salon"
    assert result["data"][CONF_MEMBER_ENTITY_IDS] == [
        "light.salon",
        "light.canape",
    ]
    assert result["data"][CONF_SCENE_ENTITY_IDS] == [
        "scene.salon_default",
        "scene.salon_tv",
    ]


async def test_light_group_plus_rejects_duplicate_scene_names(
    recorder_mock: object, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    """Effect labels must be unambiguous in the native light control."""
    del recorder_mock, enable_custom_integrations
    hass.states.async_set("light.salon", "off")
    hass.states.async_set("scene.one", "scening", {"friendly_name": "Cozy"})
    hass.states.async_set("scene.two", "scening", {"friendly_name": "cozy"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CONFIGURATION_TYPE: CONFIGURATION_TYPE_LIGHT_GROUP_PLUS},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Salon",
            CONF_MEMBER_ENTITY_IDS: ["light.salon"],
            CONF_SCENE_ENTITY_IDS: ["scene.one", "scene.two"],
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "duplicate_scene_name"}
