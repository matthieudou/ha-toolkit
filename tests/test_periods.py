"""Tests for cumulative energy, aggregation and price calculations."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.ha_toolkit.periods import (
    CumulativeSample,
    CumulativeSeries,
    Metric,
    PriceSample,
    metric_start,
)

BRUSSELS = ZoneInfo("Europe/Brussels")


@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        (Metric.TODAY, datetime(2026, 8, 23, 22, tzinfo=UTC)),
        (Metric.THIS_WEEK, datetime(2026, 8, 23, 22, tzinfo=UTC)),
        (Metric.THIS_MONTH, datetime(2026, 7, 31, 22, tzinfo=UTC)),
        (Metric.THIS_QUARTER, datetime(2026, 6, 30, 22, tzinfo=UTC)),
        (Metric.THIS_YEAR, datetime(2025, 12, 31, 23, tzinfo=UTC)),
    ],
)
def test_calendar_metric_starts_in_home_timezone(
    metric: Metric, expected: datetime
) -> None:
    """Calendar periods start at local midnight and respect DST."""
    end = datetime(2026, 8, 24, 10, 30, tzinfo=UTC)
    assert metric_start(metric, end, BRUSSELS) == expected


def test_three_month_window_uses_calendar_months() -> None:
    """Three months is not approximated as 90 days."""
    end = datetime(2026, 5, 31, 10, 30, tzinfo=UTC)
    assert metric_start(Metric.LAST_3_MONTHS, end, UTC) == datetime(
        2026, 2, 28, 10, 30, tzinfo=UTC
    )


def test_lifetime_total_uses_normalized_cumulative_value() -> None:
    """The total includes retained history rather than only one window."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    series = CumulativeSeries(
        [
            CumulativeSample(start, 10.0),
            CumulativeSample(start + timedelta(days=1), 15.0),
        ]
    )
    result = series.consumption(Metric.TOTAL, start + timedelta(days=1), UTC)
    assert result.value == 15.0
    assert result.complete is True


def test_sum_uses_only_history_shared_by_every_source() -> None:
    """A label aggregate never presents a partial member sum."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    first = CumulativeSeries(
        [
            CumulativeSample(start, 5.0),
            CumulativeSample(start + timedelta(hours=2), 9.0),
        ]
    )
    second = CumulativeSeries(
        [
            CumulativeSample(start + timedelta(hours=1), 20.0),
            CumulativeSample(start + timedelta(hours=2), 23.0),
        ]
    )
    aggregate = CumulativeSeries.sum([first, second])
    assert aggregate.first_sample.at == start + timedelta(hours=1)
    assert aggregate.last_sample.value == 32.0


def test_sum_stops_when_the_shortest_member_history_ends() -> None:
    """An aggregate never freezes one member and continues with the others."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    long = CumulativeSeries(
        [
            CumulativeSample(start, 0.0),
            CumulativeSample(start + timedelta(hours=2), 2.0),
        ]
    )
    short = CumulativeSeries(
        [
            CumulativeSample(start, 10.0),
            CumulativeSample(start + timedelta(hours=1), 11.0),
        ]
    )

    aggregate = CumulativeSeries.sum([long, short])

    assert aggregate.last_sample.at == start + timedelta(hours=1)
    assert aggregate.last_sample.value == 12.0


def test_pricing_applies_each_price_to_later_energy() -> None:
    """A tariff change values only consumption after the change."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    energy = CumulativeSeries(
        [
            CumulativeSample(start, 0.0),
            CumulativeSample(start + timedelta(hours=1), 2.0),
            CumulativeSample(start + timedelta(hours=2), 6.0),
        ]
    )
    cost = energy.priced(
        [
            PriceSample(start, 0.2),
            PriceSample(start + timedelta(hours=1), 0.4),
        ]
    )
    assert cost is not None
    assert cost.last_sample.value == 2.0


def test_subhour_price_change_marks_cost_as_estimated() -> None:
    """Hourly energy interpolation at a tariff boundary is disclosed."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    energy = CumulativeSeries(
        [
            CumulativeSample(start, 0.0),
            CumulativeSample(start + timedelta(hours=1), 2.0),
        ]
    )
    cost = energy.priced(
        [
            PriceSample(start, 0.2),
            PriceSample(start + timedelta(minutes=30), 0.4),
        ]
    )
    assert cost is not None
    result = cost.consumption(Metric.TOTAL, start + timedelta(hours=1), UTC)
    assert result.value == 0.6
    assert result.estimated is True


def test_pricing_restarts_after_an_unknown_price_period() -> None:
    """A missing tariff must not silently reuse the previous price."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    energy = CumulativeSeries(
        [
            CumulativeSample(start, 0.0),
            CumulativeSample(start + timedelta(hours=1), 1.0),
            CumulativeSample(start + timedelta(hours=2), 2.0),
            CumulativeSample(start + timedelta(hours=3), 3.0),
        ]
    )

    cost = energy.priced(
        [
            PriceSample(start, 0.2),
            PriceSample(start + timedelta(hours=1), None),
            PriceSample(start + timedelta(hours=2), 0.4),
        ]
    )

    assert cost is not None
    assert cost.first_sample.at == start + timedelta(hours=2)
    assert cost.last_sample.value == 0.4


def test_historical_values_resume_after_last_imported_hour() -> None:
    """Backfill fills later closed hours without rewriting existing rows."""
    start = datetime(2026, 8, 1, tzinfo=UTC)
    series = CumulativeSeries(
        [
            CumulativeSample(start + timedelta(hours=hour), float(hour))
            for hour in range(27)
        ]
    )
    values = series.historical_values(
        Metric.LAST_24_HOURS,
        BRUSSELS,
        current_hour=start + timedelta(hours=26),
        after=start + timedelta(hours=24),
    )
    assert [value.start for value in values] == [start + timedelta(hours=25)]
    assert values[0].value == 24.0


def test_calendar_history_keeps_value_before_midnight_reset() -> None:
    """The final civil hour stores the old day's total rather than zero."""
    midnight = datetime(2026, 8, 24, tzinfo=BRUSSELS).astimezone(UTC)
    start = midnight - timedelta(days=1)
    series = CumulativeSeries(
        [
            CumulativeSample(start + timedelta(hours=hour), float(hour))
            for hour in range(25)
        ]
    )
    values = series.historical_values(Metric.TODAY, BRUSSELS, midnight)
    assert values[-1].start == midnight - timedelta(hours=1)
    assert values[-1].value == 24.0
