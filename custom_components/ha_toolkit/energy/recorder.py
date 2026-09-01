"""Recorder access and safe long-term statistics backfill."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import DOMAIN as RECORDER_DOMAIN
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.ha_toolkit.const import DOMAIN

from .models import MeterSource, SourceMode
from .periods import CumulativeSample, CumulativeSeries, Metric, PriceSample

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SourceHistory:
    """Closed cumulative samples and the latest normalized source state."""

    samples: list[CumulativeSample]
    last_source_state: float | None


@dataclass(frozen=True, slots=True)
class LastStatistic:
    """Last derived statistic needed to resume state and sum values."""

    start: datetime
    state: float
    total: float


async def async_clear_derived_statistics(
    hass: HomeAssistant, config_entry_id: str
) -> None:
    """Clear this entry's derived statistics before a corrected full backfill."""
    statistic_ids = [
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.platform == DOMAIN and entry.config_entry_id == config_entry_id
    ]
    if not statistic_ids:
        return
    cleared = hass.loop.create_future()
    get_instance(hass).async_clear_statistics(
        statistic_ids,
        on_done=lambda: hass.loop.call_soon_threadsafe(cleared.set_result, None),
    )
    await cleared


async def async_get_source_samples(
    hass: HomeAssistant,
    source: MeterSource,
    start: datetime | None = None,
) -> SourceHistory:
    """Read and normalize closed hourly statistics for one source."""
    current_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    query_start = start or datetime(1970, 1, 1, tzinfo=UTC)
    spec = source.spec
    fields = {"state", "sum"} if spec.mode is SourceMode.CUMULATIVE else {"mean"}
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        query_start,
        current_hour,
        {source.entity_id},
        "hour",
        {spec.source_unit_class: spec.normalized_source_unit},
        fields,
    )
    rows = result.get(source.entity_id, [])
    if spec.mode is SourceMode.RATE:
        return SourceHistory(_integrate_rate_rows(rows, current_hour), None)

    samples: list[CumulativeSample] = []
    last_source_state: float | None = None
    previous_end: datetime | None = None
    ordered_rows = sorted(
        (row for row in rows if row.get("start") is not None),
        key=lambda item: _as_datetime(item["start"]),
    )
    for row in ordered_rows:
        raw_start = row.get("start")
        raw_sum = row.get("sum")
        if raw_start is None or raw_sum is None:
            continue
        bucket_start = _as_datetime(raw_start)
        bucket_end = bucket_start + timedelta(hours=1)
        if bucket_end <= current_hour:
            if previous_end is not None and bucket_start != previous_end:
                samples = []
            samples.append(CumulativeSample(bucket_end, float(raw_sum)))
            previous_end = bucket_end
            if (raw_state := row.get("state")) is not None:
                last_source_state = float(raw_state)
    return SourceHistory(samples, last_source_state)


def _integrate_rate_rows(
    rows: list[dict[str, Any]], current_hour: datetime
) -> list[CumulativeSample]:
    """Integrate complete hourly mean rates into a cumulative timeline."""
    samples: list[CumulativeSample] = []
    cumulative = 0.0
    previous_end: datetime | None = None
    for row in rows:
        raw_start = row.get("start")
        raw_mean = row.get("mean")
        if raw_start is None or raw_mean is None:
            continue
        bucket_start = _as_datetime(raw_start)
        bucket_end = bucket_start + timedelta(hours=1)
        if bucket_end > current_hour:
            continue
        if previous_end != bucket_start:
            samples = [CumulativeSample(bucket_start, 0.0)]
            cumulative = 0.0
        cumulative += max(0.0, float(raw_mean))
        samples.append(CumulativeSample(bucket_end, cumulative))
        previous_end = bucket_end
    return samples


async def async_get_price_samples(
    hass: HomeAssistant, entity_id: str, start: datetime
) -> list[PriceSample]:
    """Read numeric price changes, including the state active at the start."""
    recorder = get_instance(hass)
    query = partial(
        get_significant_states,
        hass,
        start,
        dt_util.utcnow(),
        [entity_id],
        include_start_time_state=True,
        significant_changes_only=False,
        no_attributes=True,
    )
    result = await recorder.async_add_executor_job(query)
    samples: list[PriceSample] = []
    for state in result.get(entity_id, []):
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            value = None
        samples.append(
            PriceSample(
                state.last_changed, value if value is not None and value >= 0 else None
            )
        )
    return samples


async def async_get_last_statistic(
    hass: HomeAssistant, entity_id: str
) -> LastStatistic | None:
    """Return the latest imported state and cumulative statistics sum."""
    result = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, entity_id, False, {"state", "sum"}
    )
    rows = result.get(entity_id, [])
    if (
        not rows
        or rows[0].get("start") is None
        or rows[0].get("state") is None
        or rows[0].get("sum") is None
    ):
        return None
    row = rows[0]
    return LastStatistic(
        _as_datetime(row["start"]), float(row["state"]), float(row["sum"])
    )


async def async_backfill_statistics(
    hass: HomeAssistant,
    entity_id: str,
    metric: Metric,
    series: CumulativeSeries,
    *,
    statistic_unit: tuple[str, str],
) -> int:
    """Resume derived statistics without touching the open Recorder hour."""
    last = await async_get_last_statistic(hass, entity_id)
    current_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    timezone = dt_util.get_time_zone(hass.config.time_zone) or UTC
    values = series.historical_values(
        metric, timezone, current_hour, last.start if last else None
    )
    if not values:
        return 0

    unit, unit_class = statistic_unit
    metadata: StatisticMetaData = {
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": None,
        "source": RECORDER_DOMAIN,
        "statistic_id": entity_id,
        "unit_of_measurement": unit,
    }
    if "unit_class" in StatisticMetaData.__annotations__:
        metadata["unit_class"] = unit_class
    if last:
        baseline_state = last.state
        baseline_sum = last.total
    else:
        # Recorder starts short-term TOTAL statistics at zero when the entity
        # first appears. Anchor the latest imported hour to the same zero point
        # so live five-minute statistics continue without a discontinuity.
        baseline_state = values[-1].value
        baseline_sum = 0.0
    statistics: list[StatisticData] = [
        {
            "start": value.start,
            "state": value.value,
            "sum": round(baseline_sum + value.value - baseline_state, 8),
        }
        for value in values
    ]
    async_import_statistics(hass, metadata, statistics)
    _LOGGER.info("Backfilled %s hourly statistics for %s", len(statistics), entity_id)
    return len(statistics)


def state_value(state: Any, source: MeterSource) -> float | None:
    """Normalize a source state, rejecting unavailable and invalid values."""
    if state is None or state.state in {"unknown", "unavailable"}:
        return None
    try:
        value = source.spec.normalize(float(state.state), source.unit)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _as_datetime(value: datetime | float) -> datetime:
    """Normalize Recorder timestamp representations to aware UTC datetimes."""
    return value if isinstance(value, datetime) else datetime.fromtimestamp(value, UTC)
