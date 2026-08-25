"""Shared runtime for one MattsAssistant config entry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import REFRESH_INTERVAL
from .coordinator import MeterSourceRuntime, PriceRuntime
from .discovery import resolve_meter_targets
from .periods import CumulativeSeries

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant

    from .models import ConfigurationType, MeterGroup, MeterTarget


@dataclass(slots=True)
class MeterTargetRuntime:
    """Cache one physical or aggregate target behind a small interface."""

    hass: HomeAssistant
    target: MeterTarget
    sources: tuple[MeterSourceRuntime, ...]
    price: PriceRuntime | None
    entry_id: str = ""
    _measurement_series: CumulativeSeries | None = field(default=None, init=False)
    _cost_series: CumulativeSeries | None = field(default=None, init=False)
    _listeners: set[Callable[[], None]] = field(default_factory=set, init=False)
    _unsubscribers: list[Callable[[], None]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        """Build target series and subscribe once to every input."""
        self._refresh_series()
        self._unsubscribers.extend(
            source.async_add_listener(self._handle_input_update)
            for source in self.sources
        )
        if self.price:
            self._unsubscribers.append(
                self.price.async_add_listener(self._handle_input_update)
            )

    @property
    def available(self) -> bool:
        """Return whether every member source is available."""
        return bool(self.sources) and all(source.available for source in self.sources)

    def measurement_series(self) -> CumulativeSeries | None:
        """Return the cached normalized cumulative measurement series."""
        return self._measurement_series

    def cost_series(self) -> CumulativeSeries | None:
        """Return the cached series valued with the retained price timeline."""
        return self._cost_series

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to target-series changes."""
        self._listeners.add(listener)

        def remove_listener() -> None:
            self._listeners.discard(listener)

        return remove_listener

    def stop(self) -> None:
        """Stop shared input subscriptions."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self._listeners.clear()

    def _refresh_series(self) -> None:
        """Rebuild aggregate and price series once for the whole target."""
        series = [source.series for source in self.sources]
        if any(item is None for item in series):
            self._measurement_series = None
            self._cost_series = None
            return
        available = [item for item in series if item is not None]
        try:
            self._measurement_series = (
                available[0] if len(available) == 1 else CumulativeSeries.sum(available)
            )
        except ValueError:
            self._measurement_series = None
            self._cost_series = None
            return
        self._cost_series = (
            self._measurement_series.priced(self.price.samples) if self.price else None
        )

    def _handle_input_update(self) -> None:
        """Refresh cached series and notify every derived entity."""
        self._refresh_series()
        for listener in tuple(self._listeners):
            listener()


@dataclass(slots=True)
class MattsAssistantRuntimeData:
    """Own all live state and subscriptions for one config entry."""

    hass: HomeAssistant
    entry: ConfigEntry
    configuration_type: ConfigurationType
    source_entity_ids: list[str]
    attach_entity_ids: list[str]
    groups: list[MeterGroup]
    price_entity_id: str | None
    sources: dict[str, MeterSourceRuntime] = field(default_factory=dict)
    targets: tuple[MeterTargetRuntime, ...] = ()
    price: PriceRuntime | None = None
    _target_signature: tuple[object, ...] = ()
    _unsubscribe_rescan: Callable[[], None] | None = None
    _unsubscribe_started: Callable[[], None] | None = None

    async def async_start(self) -> None:
        """Resolve targets, load each physical source once and load pricing."""
        resolved_targets = self._resolve_targets()
        unique_sources = {
            source.key: source
            for target in resolved_targets
            for source in target.sources
        }
        self.sources = {
            key: MeterSourceRuntime(self.hass, source)
            for key, source in unique_sources.items()
        }
        await asyncio.gather(
            *(runtime.async_start() for runtime in self.sources.values())
        )

        if self.price_entity_id and self.sources:
            starts = [
                runtime.series.first_sample.at
                for runtime in self.sources.values()
                if runtime.series is not None
            ]
            price_history_start = min(starts) if starts else dt_util.utcnow()
            self.price = PriceRuntime(
                self.hass, self.price_entity_id, price_history_start
            )
            await self.price.async_start()

        self.targets = tuple(
            MeterTargetRuntime(
                hass=self.hass,
                target=target,
                sources=tuple(self.sources[source.key] for source in target.sources),
                price=self.price,
                entry_id=self.entry.entry_id,
            )
            for target in resolved_targets
        )
        self._target_signature = _signature(resolved_targets)
        self._unsubscribe_rescan = async_track_time_interval(
            self.hass, self._async_rescan, REFRESH_INTERVAL
        )
        if not self.hass.is_running:
            self._unsubscribe_started = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._async_started
            )

    async def async_stop(self) -> None:
        """Stop target, source and price listeners."""
        for target in self.targets:
            target.stop()
        for runtime in self.sources.values():
            runtime.async_stop()
        if self.price:
            self.price.async_stop()
        if self._unsubscribe_rescan:
            self._unsubscribe_rescan()
        if self._unsubscribe_started:
            self._unsubscribe_started()

    def _resolve_targets(self) -> tuple[MeterTarget, ...]:
        return resolve_meter_targets(
            self.hass,
            self.configuration_type,
            self.source_entity_ids,
            self.attach_entity_ids,
            self.groups,
        )

    async def _async_rescan(self, _now: object) -> None:
        """Reload when source metadata or registry links change."""
        targets = self._resolve_targets()
        if _signature(targets) != self._target_signature:
            await self.hass.config_entries.async_reload(self.entry.entry_id)

    async def _async_started(self, _event: Event) -> None:
        self._unsubscribe_started = None
        await self._async_rescan(None)


def _signature(targets: tuple[MeterTarget, ...]) -> tuple[object, ...]:
    """Return registry-derived values that require an entity reload."""
    return tuple(
        (
            target.key,
            target.name,
            target.attach_to_device,
            target.device_id,
            tuple(
                (source.key, source.entity_id, source.unit) for source in target.sources
            ),
        )
        for target in targets
    )
