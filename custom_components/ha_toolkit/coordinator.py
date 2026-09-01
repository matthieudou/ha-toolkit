"""Per-source runtime state for HA Toolkit meter sensors."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import REFRESH_INTERVAL
from .models import MeterSource, SourceMode
from .periods import (
    Consumption,
    CumulativeSample,
    CumulativeSeries,
    Metric,
    PriceSample,
)
from .recorder import async_get_price_samples, async_get_source_samples, state_value

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _RateSample:
    """A live rate, or an unavailable marker, active from one timestamp."""

    at: datetime
    value: float | None


class MeterSourceRuntime:
    """Maintain one normalized cumulative series from a meter or rate source."""

    def __init__(self, hass: HomeAssistant, source: MeterSource) -> None:
        """Initialize a source runtime."""
        self.hass = hass
        self.source = source
        self.series: CumulativeSeries | None = None
        self.available = False
        self._closed_samples: list[CumulativeSample] = []
        self._last_closed_source_state: float | None = None
        self._live_source_state: float | None = None
        self._live_cumulative: float | None = None
        self._live_updated_at: datetime | None = None
        self._live_rate_samples: list[_RateSample] = []
        self._listeners: set[Callable[[], None]] = set()
        self._unsubscribers: list[Callable[[], None]] = []

    async def async_start(self) -> None:
        """Load retained statistics and start tracking the source."""
        await self._async_refresh_history(full=True)
        self._apply_live_state(self.hass.states.get(self.source.entity_id))
        self._unsubscribers.extend(
            [
                async_track_state_change_event(
                    self.hass, [self.source.entity_id], self._async_source_changed
                ),
                async_track_time_interval(
                    self.hass, self._async_periodic_refresh, REFRESH_INTERVAL
                ),
            ]
        )

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register an entity state listener."""
        self._listeners.add(listener)

        @callback
        def remove_listener() -> None:
            self._listeners.discard(listener)

        return remove_listener

    @callback
    def async_stop(self) -> None:
        """Stop all source listeners."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    def consumption(self, metric: Metric) -> Consumption | None:
        """Return the current calculated value for one window."""
        if self.series is None:
            return None
        timezone = dt_util.get_time_zone(self.hass.config.time_zone) or UTC
        return self.series.consumption(metric, dt_util.utcnow(), timezone)

    async def _async_refresh_history(self, *, full: bool) -> None:
        start = None
        if not full and self._closed_samples:
            start = self._closed_samples[-1].at - timedelta(hours=2)
        try:
            history = await async_get_source_samples(self.hass, self.source, start)
        except Exception:  # Recorder failures must not stop live tracking.
            _LOGGER.exception("Unable to read statistics for %s", self.source.entity_id)
            return

        if self.source.spec.mode is SourceMode.RATE:
            self._merge_rate_history(history.samples, full=full)
            self._rebase_live_rate_samples()
        else:
            merged = {sample.at: sample for sample in self._closed_samples}
            merged.update({sample.at: sample for sample in history.samples})
            self._closed_samples = [merged[key] for key in sorted(merged)]
        if history.last_source_state is not None:
            self._last_closed_source_state = history.last_source_state
            self._live_source_state = None
            self._live_cumulative = None
            self._live_updated_at = None

    def _merge_rate_history(
        self, samples: list[CumulativeSample], *, full: bool
    ) -> None:
        """Replace a refreshed rate suffix without discarding older history."""
        if not samples:
            if full:
                self._closed_samples = []
            return
        if full or not self._closed_samples:
            self._closed_samples = samples
            return
        anchor = samples[0].at
        old_series = CumulativeSeries(self._closed_samples)
        if anchor > old_series.last_sample.at:
            self._closed_samples = samples
            return
        offset = old_series.value_at(anchor)[0]
        prefix = [sample for sample in self._closed_samples if sample.at < anchor]
        self._closed_samples = [
            *prefix,
            *(CumulativeSample(sample.at, sample.value + offset) for sample in samples),
        ]

    def _rebase_live_rate_samples(self) -> None:
        """Keep open-hour observations after Recorder advances its closed anchor."""
        if not self._closed_samples or not self._live_rate_samples:
            return
        anchor = self._closed_samples[-1].at
        active_value = self._live_rate_samples[0].value
        for sample in self._live_rate_samples:
            if sample.at > anchor:
                break
            active_value = sample.value
        retained = [sample for sample in self._live_rate_samples if sample.at > anchor]
        self._live_rate_samples = [_RateSample(anchor, active_value), *retained]
        self._calculate_live_rate()

    @callback
    def _apply_live_state(self, state: object) -> None:
        value = state_value(state, self.source)
        if value is None:
            self.available = False
            if self.source.spec.mode is SourceMode.RATE:
                self._append_live_rate(None)
            self._rebuild_series()
            self._notify()
            return

        if self.source.spec.mode is SourceMode.RATE:
            self._apply_live_rate(value)
        else:
            self._apply_live_cumulative_state(value)
        self.available = True
        self._rebuild_series()
        self._notify()

    def _apply_live_cumulative_state(self, value: float) -> None:
        """Extend Recorder's normalized sum with a cumulative live meter."""
        if self._live_source_state is None:
            if self._closed_samples:
                last = self._closed_samples[-1]
                last_state = self._last_closed_source_state
                if last_state is None:
                    last_state = value
                delta = value - last_state if value >= last_state else value
                self._live_cumulative = last.value + max(0.0, delta)
            else:
                self._live_cumulative = value
        else:
            delta = value - self._live_source_state
            if delta < 0:
                delta = value
            self._live_cumulative = (self._live_cumulative or 0.0) + max(0.0, delta)
        self._live_source_state = value
        self._live_updated_at = dt_util.utcnow()

    def _apply_live_rate(self, value: float) -> None:
        """Record and integrate one stepwise live rate observation."""
        self._append_live_rate(value)
        self._live_source_state = value

    def _append_live_rate(self, value: float | None) -> None:
        """Append a live observation without duplicating its timestamp."""
        now = dt_util.utcnow()
        if not self._live_rate_samples:
            anchor = self._closed_samples[-1].at if self._closed_samples else now
            self._live_rate_samples.append(_RateSample(anchor, value))
        if self._live_rate_samples[-1].at == now:
            self._live_rate_samples[-1] = _RateSample(now, value)
        elif self._live_rate_samples[-1].value != value:
            self._live_rate_samples.append(_RateSample(now, value))
        self._calculate_live_rate(now)

    def _calculate_live_rate(self, now: datetime | None = None) -> None:
        """Integrate retained rate steps after the latest closed hour."""
        if not self._live_rate_samples:
            self._live_cumulative = None
            return
        end = now or dt_util.utcnow()
        cumulative = self._closed_samples[-1].value if self._closed_samples else 0.0
        for current, following in zip(
            self._live_rate_samples,
            [*self._live_rate_samples[1:], _RateSample(end, None)],
            strict=True,
        ):
            if current.value is None:
                continue
            elapsed_hours = max(0.0, (following.at - current.at).total_seconds() / 3600)
            cumulative += current.value * elapsed_hours
        self._live_cumulative = cumulative
        self._live_updated_at = end

    def _rebuild_series(self) -> None:
        samples = list(self._closed_samples)
        if self._live_cumulative is not None:
            samples.append(CumulativeSample(dt_util.utcnow(), self._live_cumulative))
        if samples:
            self.series = CumulativeSeries(
                samples, estimated=self.source.spec.mode is SourceMode.RATE
            )

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    @callback
    def _async_source_changed(self, event: Event[EventStateChangedData]) -> None:
        self._apply_live_state(event.data["new_state"])

    async def _async_periodic_refresh(self, _now: datetime) -> None:
        await self._async_refresh_history(full=False)
        self._apply_live_state(self.hass.states.get(self.source.entity_id))


class PriceRuntime:
    """Track the numeric unit price that applies to new increments."""

    def __init__(self, hass: HomeAssistant, entity_id: str, start: datetime) -> None:
        """Initialize a price timeline."""
        self.hass = hass
        self.entity_id = entity_id
        self.start = start
        self.samples: list[PriceSample] = []
        self.available = False
        self._listeners: set[Callable[[], None]] = set()
        self._unsubscribe: Callable[[], None] | None = None

    async def async_start(self) -> None:
        """Load retained prices and listen for future changes."""
        try:
            self.samples = await async_get_price_samples(
                self.hass, self.entity_id, self.start
            )
        except Exception:
            _LOGGER.exception("Unable to read price history for %s", self.entity_id)
        self._apply_state(self.hass.states.get(self.entity_id))
        self._unsubscribe = async_track_state_change_event(
            self.hass, [self.entity_id], self._async_price_changed
        )

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a price listener."""
        self._listeners.add(listener)

        @callback
        def remove_listener() -> None:
            self._listeners.discard(listener)

        return remove_listener

    @callback
    def async_stop(self) -> None:
        """Stop price tracking."""
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    @callback
    def _apply_state(self, state: object) -> None:
        at = getattr(state, "last_changed", dt_util.utcnow())
        try:
            value = float(state.state)  # type: ignore[union-attr]
        except (AttributeError, TypeError, ValueError):
            self._store_sample(PriceSample(at, None))
            self.available = False
            self._notify()
            return
        if value < 0:
            self._store_sample(PriceSample(at, None))
            self.available = False
            self._notify()
            return
        self._store_sample(PriceSample(at, value))
        self.available = True
        self._notify()

    def _store_sample(self, sample: PriceSample) -> None:
        """Insert or replace one price observation."""
        by_time = {sample.at: sample for sample in self.samples}
        by_time[sample.at] = sample
        self.samples = [by_time[key] for key in sorted(by_time)]

    @callback
    def _async_price_changed(self, event: Event[EventStateChangedData]) -> None:
        self._apply_state(event.data["new_state"])

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()
