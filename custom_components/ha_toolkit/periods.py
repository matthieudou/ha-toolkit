"""Pure cumulative-series calculations for HA Toolkit."""

from __future__ import annotations

import calendar
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo
from enum import StrEnum


class Metric(StrEnum):
    """Supported lifetime, calendar and rolling metrics."""

    TOTAL = "total"
    TODAY = "today"
    THIS_WEEK = "this_week"
    THIS_MONTH = "this_month"
    THIS_QUARTER = "this_quarter"
    THIS_YEAR = "this_year"
    LAST_24_HOURS = "last_24_hours"
    LAST_7_DAYS = "last_7_days"
    LAST_3_MONTHS = "last_3_months"
    LAST_365_DAYS = "last_365_days"


CALENDAR_METRICS = frozenset(
    {
        Metric.TODAY,
        Metric.THIS_WEEK,
        Metric.THIS_MONTH,
        Metric.THIS_QUARTER,
        Metric.THIS_YEAR,
    }
)
ROLLING_DURATIONS = {
    Metric.LAST_24_HOURS: timedelta(hours=24),
    Metric.LAST_7_DAYS: timedelta(days=7),
    Metric.LAST_365_DAYS: timedelta(days=365),
}
METRICS = tuple(Metric)


@dataclass(frozen=True, slots=True)
class CumulativeSample:
    """A point on a cumulative timeline."""

    at: datetime
    value: float


@dataclass(frozen=True, slots=True)
class PriceSample:
    """A price that applies from its timestamp onward."""

    at: datetime
    price_per_unit: float | None


@dataclass(frozen=True, slots=True)
class Consumption:
    """A cumulative difference over one metric window."""

    value: float
    complete: bool
    estimated: bool
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class HistoricalValue:
    """A derived value for one closed Recorder hour."""

    start: datetime
    value: float


def metric_start(metric: Metric, end: datetime, timezone: tzinfo) -> datetime:
    """Return the inclusive start of a metric at an aware timestamp."""
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be timezone-aware")
    if metric is Metric.TOTAL:
        raise ValueError("The total metric has no fixed window start")
    if duration := ROLLING_DURATIONS.get(metric):
        return end - duration

    local_end = end.astimezone(timezone)
    local_date = local_end.date()
    if metric is Metric.TODAY:
        start_date = local_date
    elif metric is Metric.THIS_WEEK:
        start_date = local_date - timedelta(days=local_date.weekday())
    elif metric is Metric.THIS_MONTH:
        start_date = local_date.replace(day=1)
    elif metric is Metric.THIS_QUARTER:
        first_month = ((local_date.month - 1) // 3) * 3 + 1
        start_date = local_date.replace(month=first_month, day=1)
    elif metric is Metric.THIS_YEAR:
        start_date = local_date.replace(month=1, day=1)
    elif metric is Metric.LAST_3_MONTHS:
        return _subtract_months(local_end, 3).astimezone(end.tzinfo)
    else:
        raise ValueError(f"Unsupported metric: {metric}")
    return datetime.combine(start_date, time.min, timezone).astimezone(end.tzinfo)


def _subtract_months(value: datetime, months: int) -> datetime:
    """Subtract calendar months and clamp the day to the target month."""
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


class CumulativeSeries:
    """Interpolate a cumulative timeline and derive metric values."""

    def __init__(
        self,
        samples: list[CumulativeSample] | tuple[CumulativeSample, ...],
        *,
        estimated: bool = False,
    ) -> None:
        """Initialize an ordered, non-empty collection of aware samples."""
        if not samples:
            raise ValueError("At least one cumulative sample is required")
        deduplicated: dict[datetime, float] = {}
        for sample in samples:
            if sample.at.tzinfo is None or sample.at.utcoffset() is None:
                raise ValueError("Cumulative samples must be timezone-aware")
            deduplicated[sample.at] = sample.value
        self.samples = tuple(
            CumulativeSample(at, value) for at, value in sorted(deduplicated.items())
        )
        self._times = tuple(sample.at for sample in self.samples)
        self.estimated = estimated

    @property
    def first_sample(self) -> CumulativeSample:
        """Return the oldest sample."""
        return self.samples[0]

    @property
    def last_sample(self) -> CumulativeSample:
        """Return the newest sample."""
        return self.samples[-1]

    def value_at(self, target: datetime) -> tuple[float, bool]:
        """Return a value at a timestamp, interpolating inside known history."""
        index = bisect_right(self._times, target)
        if index == 0:
            return self.samples[0].value, self.samples[0].at == target
        before = self.samples[index - 1]
        if before.at == target or index == len(self.samples):
            return before.value, before.at == target
        after = self.samples[index]
        elapsed = (target - before.at).total_seconds()
        duration = (after.at - before.at).total_seconds()
        fraction = elapsed / duration
        return before.value + fraction * (after.value - before.value), False

    def consumption(
        self, metric: Metric, end: datetime, timezone: tzinfo
    ) -> Consumption:
        """Calculate a lifetime, calendar or rolling cumulative difference."""
        if metric is Metric.TOTAL:
            start = self.first_sample.at
            start_value = 0.0
            start_exact = True
        else:
            start = metric_start(metric, end, timezone)
            start_value, start_exact = self.value_at(start)
        end_value, end_exact = self.value_at(end)
        complete = start >= self.first_sample.at and end <= self.last_sample.at
        return Consumption(
            value=round(max(0.0, end_value - start_value), 6),
            complete=complete,
            estimated=self.estimated or not (start_exact and end_exact),
            start=start,
            end=end,
        )

    def historical_values(
        self,
        metric: Metric,
        timezone: tzinfo,
        current_hour: datetime,
        after: datetime | None = None,
    ) -> list[HistoricalValue]:
        """Build complete metric values for closed Recorder hours."""
        values: list[HistoricalValue] = []
        for sample in self.samples:
            end = sample.at
            start = end - timedelta(hours=1)
            if (
                end > current_hour
                or end.minute
                or end.second
                or end.microsecond
                or (after is not None and start <= after)
            ):
                continue
            effective_end = (
                end - timedelta(microseconds=1) if metric in CALENDAR_METRICS else end
            )
            consumption = self.consumption(metric, effective_end, timezone)
            if consumption.complete:
                values.append(HistoricalValue(start=start, value=consumption.value))
        return values

    @classmethod
    def sum(cls, series: list[CumulativeSeries]) -> CumulativeSeries:
        """Sum series only across the period covered by every member."""
        if not series:
            raise ValueError("At least one series is required")
        common_start = max(item.first_sample.at for item in series)
        common_end = min(item.last_sample.at for item in series)
        if common_start > common_end:
            raise ValueError("Series do not share a common period")
        times = {common_start, common_end}
        for item in series:
            times.update(
                sample.at
                for sample in item.samples
                if common_start <= sample.at <= common_end
            )
        estimated = any(item.estimated for item in series)
        samples: list[CumulativeSample] = []
        for at in sorted(times):
            values = [item.value_at(at) for item in series]
            estimated = estimated or not all(exact for _, exact in values)
            samples.append(CumulativeSample(at, sum(value for value, _ in values)))
        return cls(samples, estimated=estimated)

    def priced(self, prices: list[PriceSample]) -> CumulativeSeries | None:
        """Value energy increments with the price active during each increment."""
        if not prices:
            return None
        ordered_prices = sorted(prices, key=lambda item: item.at)
        last_gap = next(
            (
                index
                for index in range(len(ordered_prices) - 1, -1, -1)
                if ordered_prices[index].price_per_unit is None
            ),
            -1,
        )
        ordered_prices = [
            sample
            for sample in ordered_prices[last_gap + 1 :]
            if sample.price_per_unit is not None
        ]
        if not ordered_prices:
            return None
        start = max(self.first_sample.at, ordered_prices[0].at)
        end = self.last_sample.at
        if start > end:
            return None
        times = {start, end}
        times.update(sample.at for sample in self.samples if start <= sample.at <= end)
        times.update(
            sample.at for sample in ordered_prices if start <= sample.at <= end
        )
        price_times = [sample.at for sample in ordered_prices]
        cumulative_cost = 0.0
        output = [CumulativeSample(start, 0.0)]
        previous = start
        for current in sorted(times):
            if current <= start:
                continue
            measurement_before = self.value_at(previous)[0]
            measurement_after = self.value_at(current)[0]
            price_index = bisect_right(price_times, previous) - 1
            if price_index >= 0:
                price = ordered_prices[price_index].price_per_unit
                if price is None:  # Narrowed above; retained for type safety.
                    continue
                cumulative_cost += max(0.0, measurement_after - measurement_before) * (
                    price
                )
            output.append(CumulativeSample(current, round(cumulative_cost, 8)))
            previous = current
        energy_times = {sample.at for sample in self.samples}
        interpolated_price_boundary = any(
            start < sample.at < end and sample.at not in energy_times
            for sample in ordered_prices
        )
        return CumulativeSeries(
            output, estimated=self.estimated or interpolated_price_boundary
        )
