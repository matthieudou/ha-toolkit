"""Tests for live cumulative-source tracking."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant, State

from custom_components.mattsassistant.coordinator import MeterSourceRuntime
from custom_components.mattsassistant.models import ConfigurationType, MeterSource
from custom_components.mattsassistant.periods import CumulativeSample


def test_live_updates_anchor_to_recorder_and_survive_source_reset(
    hass: HomeAssistant,
) -> None:
    """The source lifetime total is not counted twice after startup or reset."""
    source = MeterSource(
        "entry",
        "sensor.plug_energy",
        "device",
        "Prise",
        "kWh",
        ConfigurationType.ELECTRICITY_ENERGY,
    )
    runtime = MeterSourceRuntime(hass, source)
    runtime._closed_samples = [
        CumulativeSample(datetime.now(UTC) - timedelta(hours=1), 50.0)
    ]
    runtime._last_closed_source_state = 100.0

    runtime._apply_live_state(State(source.entity_id, "102"))
    assert runtime.series is not None
    assert runtime.series.last_sample.value == 52.0

    runtime._apply_live_state(State(source.entity_id, "103"))
    assert runtime.series.last_sample.value == 53.0

    runtime._apply_live_state(State(source.entity_id, "1"))
    assert runtime.series.last_sample.value == 54.0


def test_incremental_rate_refresh_preserves_older_cumulative_history(
    hass: HomeAssistant,
) -> None:
    """Refreshing the recent rate suffix keeps its cumulative offset."""
    start = datetime.now(UTC) - timedelta(hours=4)
    source = MeterSource(
        "entry",
        "sensor.plug_power",
        "device",
        "Power",
        "W",
        ConfigurationType.ELECTRICITY_POWER,
    )
    runtime = MeterSourceRuntime(hass, source)
    runtime._closed_samples = [
        CumulativeSample(start + timedelta(hours=hour), float(hour))
        for hour in range(4)
    ]

    runtime._merge_rate_history(
        [
            CumulativeSample(start + timedelta(hours=2), 0.0),
            CumulativeSample(start + timedelta(hours=3), 0.5),
            CumulativeSample(start + timedelta(hours=4), 1.0),
        ],
        full=False,
    )

    assert [sample.value for sample in runtime._closed_samples] == [
        0.0,
        1.0,
        2.0,
        2.5,
        3.0,
    ]


def test_live_rate_keeps_observed_changes_during_the_open_hour(
    hass: HomeAssistant,
) -> None:
    """A refresh never applies the latest rate to the whole open hour."""
    start = datetime(2026, 8, 25, 12, tzinfo=UTC)
    source = MeterSource(
        "entry",
        "sensor.plug_power",
        "device",
        "Power",
        "kW",
        ConfigurationType.ELECTRICITY_POWER,
    )
    runtime = MeterSourceRuntime(hass, source)
    runtime._closed_samples = [CumulativeSample(start, 0.0)]
    with patch(
        "custom_components.mattsassistant.coordinator.dt_util.utcnow",
        return_value=start,
    ):
        runtime._append_live_rate(0.0)
    with patch(
        "custom_components.mattsassistant.coordinator.dt_util.utcnow",
        return_value=start + timedelta(minutes=45),
    ):
        runtime._append_live_rate(4.0)
    runtime._rebase_live_rate_samples()
    with patch(
        "custom_components.mattsassistant.coordinator.dt_util.utcnow",
        return_value=start + timedelta(hours=1),
    ):
        runtime._append_live_rate(4.0)

    assert runtime._live_cumulative == 1.0
