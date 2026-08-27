"""Integration-level setup tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, UnitOfEnergy
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mattsassistant import async_setup_entry, async_unload_entry
from custom_components.mattsassistant.const import (
    CONF_ATTACH_ENTITY_IDS,
    CONF_CONFIGURATION_TYPE,
    CONF_GROUP_ENTITY_IDS,
    CONF_GROUP_ID,
    CONF_GROUP_NAME,
    CONF_GROUPS,
    CONF_PRICE_ENTITY_ID,
    CONF_REBUILD_STATISTICS,
    CONF_SOURCE_ENTITY_IDS,
    DOMAIN,
)
from custom_components.mattsassistant.models import ConfigurationType
from custom_components.mattsassistant.periods import (
    CumulativeSample,
    CumulativeSeries,
    PriceSample,
)


async def _start_source(runtime: object) -> None:
    now = datetime.now(UTC)
    runtime.series = CumulativeSeries(
        [
            CumulativeSample(now - timedelta(days=365), 0.0),
            CumulativeSample(now, 100.0),
        ]
    )
    runtime.available = True


def _add_source(hass: HomeAssistant) -> tuple[str, str]:
    MockConfigEntry(domain="test", entry_id="source-config").add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="source-config",
        identifiers={("test", "plug")},
        name="Test plug",
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
        "100",
        {
            "device_class": SensorDeviceClass.ENERGY,
            "state_class": SensorStateClass.TOTAL_INCREASING,
            "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
        },
    )
    return entry.entity_id, device.id


async def test_statistics_rebuild_waits_until_home_assistant_started(
    hass: HomeAssistant,
) -> None:
    """A one-time repair must not wait on Recorder during startup."""
    hass.set_state(CoreState.starting)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="configuration",
        version=4,
        data={
            CONF_CONFIGURATION_TYPE: ConfigurationType.ELECTRICITY_ENERGY.value,
            CONF_SOURCE_ENTITY_IDS: [],
            CONF_ATTACH_ENTITY_IDS: [],
            CONF_GROUPS: [],
            CONF_REBUILD_STATISTICS: True,
        },
    )
    entry.add_to_hass(hass)
    clear_started = asyncio.Event()
    allow_clear = asyncio.Event()
    repair_finished = asyncio.Event()

    async def clear_statistics(_hass: HomeAssistant, _entry_id: str) -> None:
        clear_started.set()
        await allow_clear.wait()

    async def reload_entry(_entry_id: str) -> None:
        assert await async_unload_entry(hass, entry)
        repair_finished.set()

    with (
        patch(
            "custom_components.mattsassistant.async_clear_derived_statistics",
            side_effect=clear_statistics,
        ) as clear_statistics_mock,
        patch.object(
            hass.config_entries, "async_reload", side_effect=reload_entry
        ) as reload_mock,
    ):
        async with asyncio.timeout(1):
            assert await async_setup_entry(hass, entry)
        clear_statistics_mock.assert_not_awaited()

        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await clear_started.wait()
        assert entry.data[CONF_REBUILD_STATISTICS] is True

        allow_clear.set()
        async with asyncio.timeout(1):
            await repair_finished.wait()

    clear_statistics_mock.assert_awaited_once_with(hass, entry.entry_id)
    assert CONF_REBUILD_STATISTICS not in entry.data
    reload_mock.assert_awaited_once_with(entry.entry_id)


async def test_failed_statistics_rebuild_keeps_marker_for_later_retry(
    hass: HomeAssistant,
) -> None:
    """A failed one-time repair must remain pending without reloading."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="configuration",
        version=4,
        data={
            CONF_CONFIGURATION_TYPE: ConfigurationType.ELECTRICITY_ENERGY.value,
            CONF_SOURCE_ENTITY_IDS: [],
            CONF_ATTACH_ENTITY_IDS: [],
            CONF_GROUPS: [],
            CONF_REBUILD_STATISTICS: True,
        },
    )
    entry.add_to_hass(hass)
    failure_logged = asyncio.Event()

    async def clear_statistics(_hass: HomeAssistant, _entry_id: str) -> None:
        raise RuntimeError("Recorder unavailable")

    def record_failure(*_args: object, **_kwargs: object) -> None:
        failure_logged.set()

    with (
        patch(
            "custom_components.mattsassistant.async_clear_derived_statistics",
            side_effect=clear_statistics,
        ),
        patch(
            "custom_components.mattsassistant._LOGGER.exception",
            side_effect=record_failure,
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload_mock,
    ):
        assert await async_setup_entry(hass, entry)
        async with asyncio.timeout(1):
            await failure_logged.wait()

    assert entry.data[CONF_REBUILD_STATISTICS] is True
    reload_mock.assert_not_awaited()


async def test_setup_creates_ten_sensors_with_source_based_entity_ids(
    recorder_mock: object,
    enable_custom_integrations: None,
    hass: HomeAssistant,
) -> None:
    """One selected meter receives all calendar and rolling metrics."""
    del recorder_mock, enable_custom_integrations
    entity_id, device_id = _add_source(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="configuration",
        version=3,
        data={
            CONF_CONFIGURATION_TYPE: ConfigurationType.ELECTRICITY_ENERGY.value,
            CONF_SOURCE_ENTITY_IDS: [entity_id],
            CONF_ATTACH_ENTITY_IDS: [entity_id],
            CONF_GROUPS: [],
        },
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.mattsassistant.runtime.MeterSourceRuntime.async_start",
            _start_source,
        ),
        patch(
            "custom_components.mattsassistant.sensor.async_backfill_statistics",
            AsyncMock(return_value=0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    generated = [
        item for item in er.async_get(hass).entities.values() if item.platform == DOMAIN
    ]
    assert len(generated) == 10
    virtual_device_ids = {item.device_id for item in generated}
    assert None not in virtual_device_ids
    assert len(virtual_device_ids) == 1
    virtual_device_id = next(iter(virtual_device_ids))
    virtual_device = dr.async_get(hass).async_get(virtual_device_id)
    assert virtual_device is not None
    assert virtual_device.name == "kitchen dishwasher energy"
    assert virtual_device.via_device_id == device_id
    assert entry.entry_id in virtual_device.config_entries
    assert "sensor.kitchen_dishwasher_energy_this_year" in {
        item.entity_id for item in generated
    }
    state = hass.states.get("sensor.kitchen_dishwasher_energy_total")
    assert state is not None
    assert state.state != "unavailable"

    with (
        patch(
            "custom_components.mattsassistant.runtime.MeterSourceRuntime.async_start",
            _start_source,
        ),
        patch(
            "custom_components.mattsassistant.sensor.async_backfill_statistics",
            AsyncMock(return_value=0),
        ),
    ):
        assert await hass.config_entries.async_unload(entry.entry_id)
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_ATTACH_ENTITY_IDS: []},
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    generated = [
        item for item in er.async_get(hass).entities.values() if item.platform == DOMAIN
    ]
    assert {item.device_id for item in generated} == {virtual_device_id}
    virtual_device = dr.async_get(hass).async_get(virtual_device_id)
    assert virtual_device is not None
    assert virtual_device.via_device_id is None

    with patch(
        "custom_components.mattsassistant.sensor.async_backfill_statistics",
        AsyncMock(return_value=0),
    ):
        assert await hass.config_entries.async_unload(entry.entry_id)
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_SOURCE_ENTITY_IDS: [],
                CONF_ATTACH_ENTITY_IDS: [],
            },
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert dr.async_get(hass).async_get(virtual_device_id) is None


async def test_group_with_price_creates_virtual_device_and_cost_sensors(
    recorder_mock: object,
    enable_custom_integrations: None,
    hass: HomeAssistant,
) -> None:
    """A named group gets a virtual device with measurement and cost metrics."""
    del recorder_mock, enable_custom_integrations
    entity_id, _device_id = _add_source(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="configuration",
        version=3,
        data={
            CONF_CONFIGURATION_TYPE: ConfigurationType.ELECTRICITY_ENERGY.value,
            CONF_SOURCE_ENTITY_IDS: [],
            CONF_ATTACH_ENTITY_IDS: [],
            CONF_GROUPS: [
                {
                    CONF_GROUP_ID: "housekeeping",
                    CONF_GROUP_NAME: "Housekeeping",
                    CONF_GROUP_ENTITY_IDS: [entity_id],
                }
            ],
            CONF_PRICE_ENTITY_ID: "input_number.electricity_price",
        },
    )
    entry.add_to_hass(hass)

    async def start_price(runtime: object) -> None:
        runtime.samples = [PriceSample(datetime.now(UTC) - timedelta(days=365), 0.3)]
        runtime.available = True

    with (
        patch(
            "custom_components.mattsassistant.runtime.MeterSourceRuntime.async_start",
            _start_source,
        ),
        patch(
            "custom_components.mattsassistant.runtime.PriceRuntime.async_start",
            start_price,
        ),
        patch(
            "custom_components.mattsassistant.sensor.async_backfill_statistics",
            AsyncMock(return_value=0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    generated = [
        item for item in er.async_get(hass).entities.values() if item.platform == DOMAIN
    ]
    assert len(generated) == 20
    virtual_device_ids = {item.device_id for item in generated}
    assert len(virtual_device_ids) == 1
    virtual_device = dr.async_get(hass).async_get(next(iter(virtual_device_ids)))
    assert virtual_device is not None
    assert virtual_device.name == "Housekeeping"
