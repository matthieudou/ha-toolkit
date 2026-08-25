"""Tests for Recorder history and resumable statistics backfill."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.components.recorder import DOMAIN as RECORDER_DOMAIN
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_recorder_block_till_done,
)

from custom_components.mattsassistant.models import ConfigurationType, MeterSource
from custom_components.mattsassistant.periods import (
    CumulativeSample,
    CumulativeSeries,
    Metric,
)
from custom_components.mattsassistant.recorder import (
    LastStatistic,
    async_backfill_statistics,
    async_get_source_samples,
)


async def test_backfill_resumes_after_latest_closed_hour(hass: HomeAssistant) -> None:
    """Backfill appends missing closed hours and preserves existing rows."""
    now = datetime(2026, 8, 24, 12, 34, tzinfo=UTC)
    current_hour = now.replace(minute=0)
    first = current_hour - timedelta(hours=27)
    series = CumulativeSeries(
        [
            CumulativeSample(first + timedelta(hours=hour), float(hour))
            for hour in range(28)
        ]
    )
    with (
        patch(
            "custom_components.mattsassistant.recorder.async_get_last_statistic",
            AsyncMock(
                return_value=LastStatistic(
                    current_hour - timedelta(hours=2), 24.0, 42.0
                )
            ),
        ),
        patch(
            "custom_components.mattsassistant.recorder.dt_util.utcnow", return_value=now
        ),
        patch.dict(StatisticMetaData.__annotations__, {"unit_class": str}),
        patch(
            "custom_components.mattsassistant.recorder.async_import_statistics"
        ) as import_statistics,
    ):
        count = await async_backfill_statistics(
            hass,
            "sensor.plug_last_24_hours",
            Metric.LAST_24_HOURS,
            series,
            statistic_unit=("kWh", "energy"),
        )
    assert count == 1
    metadata, statistics = import_statistics.call_args.args[1:]
    assert metadata["unit_class"] == "energy"
    assert statistics[0]["start"] == current_hour - timedelta(hours=1)
    assert statistics[0]["sum"] == 42.0


async def test_cost_backfill_uses_monetary_metadata(hass: HomeAssistant) -> None:
    """Cost statistics use the configured currency and monetary unit class."""
    now = datetime(2026, 8, 24, 12, 34, tzinfo=UTC)
    first = now.replace(minute=0) - timedelta(hours=2)
    series = CumulativeSeries(
        [
            CumulativeSample(first, 0.0),
            CumulativeSample(first + timedelta(hours=1), 1.0),
        ]
    )
    with (
        patch(
            "custom_components.mattsassistant.recorder.async_get_last_statistic",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.mattsassistant.recorder.dt_util.utcnow", return_value=now
        ),
        patch.dict(StatisticMetaData.__annotations__, {"unit_class": str}),
        patch(
            "custom_components.mattsassistant.recorder.async_import_statistics"
        ) as import_statistics,
    ):
        await async_backfill_statistics(
            hass,
            "sensor.plug_cost_total",
            Metric.TOTAL,
            series,
            statistic_unit=("EUR", "monetary"),
        )
    metadata = import_statistics.call_args.args[1]
    assert metadata["unit_class"] == "monetary"
    assert metadata["unit_of_measurement"] == "EUR"


async def test_backfill_round_trip_with_recorder(
    recorder_mock: object, hass: HomeAssistant
) -> None:
    """Imported derived statistics are committed and readable."""
    del recorder_mock
    current_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    first = current_hour - timedelta(hours=26)
    series = CumulativeSeries(
        [
            CumulativeSample(first + timedelta(hours=hour), float(hour))
            for hour in range(27)
        ]
    )
    count = await async_backfill_statistics(
        hass,
        "sensor.plug_last_24_hours",
        Metric.LAST_24_HOURS,
        series,
        statistic_unit=("kWh", "energy"),
    )
    await async_recorder_block_till_done(hass)
    result = await get_instance(hass).async_add_executor_job(
        get_last_statistics,
        hass,
        10,
        "sensor.plug_last_24_hours",
        False,
        {"state", "sum"},
    )
    assert count == 3
    assert len(result["sensor.plug_last_24_hours"]) == 3


async def test_reads_closed_source_statistics(
    recorder_mock: object, hass: HomeAssistant
) -> None:
    """Source statistics are normalized and exposed at bucket end."""
    del recorder_mock
    current_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    source = MeterSource(
        "source-entry",
        "sensor.plug_energy",
        "device",
        "Plug",
        "kWh",
        ConfigurationType.ELECTRICITY_ENERGY,
    )
    metadata = {
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": None,
        "source": RECORDER_DOMAIN,
        "statistic_id": source.entity_id,
        "unit_of_measurement": "kWh",
    }
    if "unit_class" in StatisticMetaData.__annotations__:
        metadata["unit_class"] = "energy"
    async_import_statistics(
        hass,
        metadata,
        [
            {"start": current_hour - timedelta(hours=3), "state": 100.0, "sum": 0.0},
            {"start": current_hour - timedelta(hours=2), "state": 102.0, "sum": 2.0},
        ],
    )
    await async_recorder_block_till_done(hass)
    history = await async_get_source_samples(hass, source)
    assert [sample.value for sample in history.samples] == [0.0, 2.0]
    assert history.last_source_state == 102.0


async def test_cumulative_history_keeps_only_latest_continuous_suffix(
    recorder_mock: object, hass: HomeAssistant
) -> None:
    """A missing Recorder hour must not be bridged by interpolation."""
    del recorder_mock
    current_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    source = MeterSource(
        "source-entry",
        "sensor.plug_energy_with_gap",
        "device",
        "Plug",
        "kWh",
        ConfigurationType.ELECTRICITY_ENERGY,
    )
    metadata = {
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": None,
        "source": RECORDER_DOMAIN,
        "statistic_id": source.entity_id,
        "unit_of_measurement": "kWh",
    }
    if "unit_class" in StatisticMetaData.__annotations__:
        metadata["unit_class"] = "energy"
    async_import_statistics(
        hass,
        metadata,
        [
            {"start": current_hour - timedelta(hours=5), "state": 100.0, "sum": 0.0},
            {"start": current_hour - timedelta(hours=4), "state": 101.0, "sum": 1.0},
            {"start": current_hour - timedelta(hours=2), "state": 103.0, "sum": 3.0},
            {"start": current_hour - timedelta(hours=1), "state": 104.0, "sum": 4.0},
        ],
    )
    await async_recorder_block_till_done(hass)

    history = await async_get_source_samples(hass, source)

    assert [sample.value for sample in history.samples] == [3.0, 4.0]
    assert history.samples[0].at == current_hour - timedelta(hours=1)
    assert history.last_source_state == 104.0


async def test_integrates_hourly_power_means_into_energy(
    recorder_mock: object, hass: HomeAssistant
) -> None:
    """Power configurations reconstruct kWh from Recorder's hourly means."""
    del recorder_mock
    current_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    source = MeterSource(
        "source-entry",
        "sensor.plug_power",
        "device",
        "Plug power",
        "W",
        ConfigurationType.ELECTRICITY_POWER,
    )
    metadata = {
        "mean_type": StatisticMeanType.ARITHMETIC,
        "has_sum": False,
        "name": None,
        "source": RECORDER_DOMAIN,
        "statistic_id": source.entity_id,
        "unit_of_measurement": "W",
    }
    if "unit_class" in StatisticMetaData.__annotations__:
        metadata["unit_class"] = "power"
    async_import_statistics(
        hass,
        metadata,
        [
            {"start": current_hour - timedelta(hours=3), "mean": 1000.0},
            {"start": current_hour - timedelta(hours=2), "mean": 500.0},
        ],
    )
    await async_recorder_block_till_done(hass)

    history = await async_get_source_samples(hass, source)

    assert [sample.value for sample in history.samples] == [0.0, 1.0, 1.5]
