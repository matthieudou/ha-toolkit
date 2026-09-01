"""Tests for generated Home Assistant sensor metadata."""

from datetime import UTC, datetime, timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant

from custom_components.ha_toolkit.coordinator import (
    MeterSourceRuntime,
    PriceRuntime,
)
from custom_components.ha_toolkit.models import (
    ConfigurationType,
    MeterSource,
    MeterTarget,
    TargetKind,
)
from custom_components.ha_toolkit.periods import (
    CumulativeSample,
    CumulativeSeries,
    Metric,
    PriceSample,
)
from custom_components.ha_toolkit.runtime import MeterTargetRuntime
from custom_components.ha_toolkit.sensor import (
    CostMetricSensor,
    MeasurementMetricSensor,
)


def _target_runtime(hass: HomeAssistant, *, with_price: bool) -> MeterTargetRuntime:
    source = MeterSource(
        "entry",
        "sensor.kitchen_dishwasher_energy",
        "device",
        "Dishwasher energy",
        "kWh",
        ConfigurationType.ELECTRICITY_ENERGY,
    )
    source_runtime = MeterSourceRuntime(hass, source)
    now = datetime.now(UTC)
    source_runtime.series = CumulativeSeries(
        [
            CumulativeSample(now - timedelta(days=7), 10.0),
            CumulativeSample(now, 12.5),
        ]
    )
    source_runtime.available = True
    price = None
    if with_price:
        price = PriceRuntime(
            hass, "input_number.electricity_price", now - timedelta(days=7)
        )
        price.samples = [PriceSample(now - timedelta(days=7), 0.4)]
        price.available = True
    return MeterTargetRuntime(
        hass,
        MeterTarget(
            "entry",
            TargetKind.SOURCE,
            "Dishwasher energy",
            (source,),
            attach_to_device=True,
            device_id="device",
            source_entity_id=source.entity_id,
        ),
        (source_runtime,),
        price,
        "configuration",
    )


def test_energy_sensor_has_statistics_metadata_and_suggested_entity_id(
    hass: HomeAssistant,
) -> None:
    """Measurement metrics expose normalized output and a source-based id."""
    sensor = MeasurementMetricSensor(
        _target_runtime(hass, with_price=False), Metric.LAST_7_DAYS
    )
    sensor.hass = hass
    assert sensor.device_class is SensorDeviceClass.ENERGY
    assert sensor.state_class is SensorStateClass.TOTAL
    assert sensor.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
    assert sensor.native_value == 2.5
    assert sensor.unique_id == "configuration_entry_last_7_days"
    assert sensor.entity_id == "sensor.kitchen_dishwasher_energy_last_7_days"


def test_cost_sensor_uses_configured_currency(hass: HomeAssistant) -> None:
    """Cost metrics use Home Assistant's currency and monetary metadata."""
    hass.config.currency = "EUR"
    sensor = CostMetricSensor(_target_runtime(hass, with_price=True), Metric.TOTAL)
    sensor.hass = hass
    assert sensor.device_class is SensorDeviceClass.MONETARY
    assert sensor.state_class is SensorStateClass.TOTAL
    assert sensor.native_unit_of_measurement == "EUR"
    assert sensor.native_value == 1.0
    assert sensor.unique_id == "configuration_entry_cost_total"


def test_group_series_is_computed_once_and_cached(hass: HomeAssistant) -> None:
    """All derived sensors for a group share one aggregate series."""
    now = datetime.now(UTC)
    sources = tuple(
        MeterSource(
            f"entry-{index}",
            f"sensor.source_{index}",
            None,
            f"Source {index}",
            "kWh",
            ConfigurationType.ELECTRICITY_ENERGY,
        )
        for index in range(2)
    )
    source_runtimes = tuple(MeterSourceRuntime(hass, source) for source in sources)
    for index, source_runtime in enumerate(source_runtimes):
        source_runtime.series = CumulativeSeries(
            [
                CumulativeSample(now - timedelta(days=1), float(index)),
                CumulativeSample(now, float(index + 1)),
            ]
        )
        source_runtime.available = True
    runtime = MeterTargetRuntime(
        hass,
        MeterTarget("group", TargetKind.GROUP, "Group", sources),
        source_runtimes,
        None,
        "configuration",
    )

    first = runtime.measurement_series()

    assert first is not None
    assert runtime.measurement_series() is first
    assert first.last_sample.value == 3.0
