"""Calendar, rolling and cost sensors for HA Toolkit."""

from __future__ import annotations

from datetime import UTC
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NAME
from .discovery import target_object_id
from .models import TargetKind
from .periods import CALENDAR_METRICS, METRICS, Metric
from .recorder import async_backfill_statistics

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .periods import CumulativeSeries
    from .runtime import HAToolkitRuntimeData, MeterTargetRuntime


class ValueKind(StrEnum):
    """Kinds of cumulative value exposed for every metric."""

    MEASUREMENT = "measurement"
    COST = "cost"


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up all configured individual and group sensors."""
    runtime_data: HAToolkitRuntimeData = entry.runtime_data
    entities: list[HAToolkitSensor] = []
    for target in runtime_data.targets:
        entities.extend(MeasurementMetricSensor(target, metric) for metric in METRICS)
        if runtime_data.price is not None:
            entities.extend(CostMetricSensor(target, metric) for metric in METRICS)
    _remove_stale_registry_entries(entry, entities)
    async_add_entities(entities)


class HAToolkitSensor(SensorEntity):
    """Base entity for one target and cumulative metric."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, runtime: MeterTargetRuntime, metric: Metric, value_kind: ValueKind
    ) -> None:
        """Initialize a target metric."""
        self.runtime = runtime
        self.metric = metric
        self.value_kind = value_kind
        metric_suffix = metric.value
        suffix = (
            metric_suffix
            if value_kind is ValueKind.MEASUREMENT
            else f"cost_{metric_suffix}"
        )
        entry_prefix = f"{runtime.entry_id}_" if runtime.entry_id else ""
        self._attr_unique_id = f"{entry_prefix}{runtime.target.key}_{suffix}"
        quantity = runtime.target.sources[0].spec.quantity
        self._attr_translation_key = (
            f"{quantity}_{metric_suffix}"
            if value_kind is ValueKind.MEASUREMENT
            else suffix
        )
        object_id = target_object_id(runtime.target, quantity)
        # The entity platform treats this as a suggestion. The registry still
        # slugifies it, resolves collisions and preserves user renames.
        self.entity_id = f"sensor.{object_id}_{suffix}"
        self._set_device_info()

    def _set_device_info(self) -> None:
        target = self.runtime.target
        entry_prefix = f"{self.runtime.entry_id}_" if self.runtime.entry_id else ""
        device_type = "group" if target.kind is TargetKind.GROUP else "meter"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_prefix}{target.key}")},
            name=target.name,
            manufacturer=NAME,
            model=f"{target.sources[0].configuration_type.value} {device_type}",
            via_device=self._source_via_device(),
        )

    def _source_via_device(self) -> tuple[str, str] | None:
        """Return a stable parent identifier for a linked individual meter."""
        target = self.runtime.target
        if (
            target.kind is not TargetKind.SOURCE
            or not target.attach_to_device
            or not target.device_id
        ):
            return None
        source_device = dr.async_get(self.runtime.hass).async_get(target.device_id)
        if source_device is None or not source_device.identifiers:
            return None
        return min(source_device.identifiers)

    @property
    def _series(self) -> CumulativeSeries | None:
        return (
            self.runtime.measurement_series()
            if self.value_kind is ValueKind.MEASUREMENT
            else self.runtime.cost_series()
        )

    @property
    def available(self) -> bool:
        """Return whether all inputs and the requested series are available."""
        if not self.runtime.available or self._series is None:
            return False
        return self.value_kind is ValueKind.MEASUREMENT or bool(
            self.runtime.price and self.runtime.price.available
        )

    @property
    def native_value(self) -> float | None:
        """Return the current metric value."""
        series = self._series
        if series is None:
            return None
        timezone = dt_util.get_time_zone(self.hass.config.time_zone) or UTC
        result = series.consumption(self.metric, dt_util.utcnow(), timezone)
        return result.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose inputs and history quality."""
        series = self._series
        attributes: dict[str, Any] = {
            "source_entity_ids": [
                source.source.entity_id for source in self.runtime.sources
            ],
            "window_type": _window_type(self.metric),
        }
        if self.value_kind is ValueKind.COST and self.runtime.price:
            attributes["price_entity_id"] = self.runtime.price.entity_id
        if series is not None:
            timezone = dt_util.get_time_zone(self.hass.config.time_zone) or UTC
            result = series.consumption(self.metric, dt_util.utcnow(), timezone)
            history_complete = result.complete
            if self.value_kind is ValueKind.COST:
                measurement = self.runtime.measurement_series()
                history_complete = bool(
                    history_complete
                    and measurement
                    and series.first_sample.at <= measurement.first_sample.at
                )
            attributes.update(
                {
                    "window_start": result.start.isoformat(),
                    "window_end": result.end.isoformat(),
                    "history_complete": history_complete,
                    "estimated": result.estimated,
                }
            )
        return attributes

    async def async_added_to_hass(self) -> None:
        """Attach requested physical metrics and resume statistics backfill."""
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.async_add_listener(self._handle_update))
        backfill = self._async_backfill()
        name = f"Backfill statistics for {self.entity_id}"
        if self.runtime.entry is not None:
            self.runtime.entry.async_create_background_task(self.hass, backfill, name)
        else:
            self.hass.async_create_background_task(backfill, name)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    async def _async_backfill(self) -> None:
        series = self._series
        if series is None:
            return
        if self.value_kind is ValueKind.MEASUREMENT:
            spec = self.runtime.target.sources[0].spec
            await async_backfill_statistics(
                self.hass,
                self.entity_id,
                self.metric,
                series,
                statistic_unit=(spec.result_unit, spec.result_unit_class),
            )
        else:
            await async_backfill_statistics(
                self.hass,
                self.entity_id,
                self.metric,
                series,
                statistic_unit=(self.hass.config.currency, "monetary"),
            )


class MeasurementMetricSensor(HAToolkitSensor):
    """Consumption by a target in one metric window."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3

    def __init__(self, runtime: MeterTargetRuntime, metric: Metric) -> None:
        """Initialize a consumption metric."""
        spec = runtime.target.sources[0].spec
        self._attr_device_class = spec.result_device_class
        self._attr_native_unit_of_measurement = spec.result_unit
        super().__init__(runtime, metric, ValueKind.MEASUREMENT)


class CostMetricSensor(HAToolkitSensor):
    """Cost accumulated by a target in one metric window."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, runtime: MeterTargetRuntime, metric: Metric) -> None:
        """Initialize a cost metric."""
        self._attr_native_unit_of_measurement = runtime.hass.config.currency
        super().__init__(runtime, metric, ValueKind.COST)


def _window_type(metric: Metric) -> str:
    if metric is Metric.TOTAL:
        return "lifetime"
    return "calendar" if metric in CALENDAR_METRICS else "rolling"


@callback
def _remove_stale_registry_entries(
    entry: ConfigEntry, entities: list[HAToolkitSensor]
) -> None:
    """Remove generated entities and virtual devices no longer in the config."""
    hass = entry.runtime_data.hass
    entity_registry = er.async_get(hass)
    expected_unique_ids = {entity.unique_id for entity in entities}
    for registry_entry in list(entity_registry.entities.values()):
        if (
            registry_entry.platform == DOMAIN
            and registry_entry.domain == "sensor"
            and registry_entry.config_entry_id == entry.entry_id
            and registry_entry.unique_id not in expected_unique_ids
        ):
            entity_registry.async_remove(registry_entry.entity_id)

    expected_device_identifiers = {
        identifier
        for entity in entities
        if entity.device_info
        for identifier in entity.device_info.get("identifiers", set())
    }
    device_registry = dr.async_get(hass)
    for entity in entities:
        device_info = entity.device_info
        if not device_info:
            continue
        identifiers = set(device_info.get("identifiers", set()))
        device = device_registry.async_get_device(identifiers=identifiers)
        if device is None:
            continue
        via_device = device_info.get("via_device")
        parent = (
            device_registry.async_get_device(identifiers={via_device})
            if via_device
            else None
        )
        desired_parent_id = parent.id if parent else None
        if device.via_device_id != desired_parent_id:
            device_registry.async_update_device(
                device.id, via_device_id=desired_parent_id
            )

    for device in list(device_registry.devices.values()):
        integration_identifiers = {
            identifier for identifier in device.identifiers if identifier[0] == DOMAIN
        }
        if (
            entry.entry_id in device.config_entries
            and integration_identifiers
            and integration_identifiers.isdisjoint(expected_device_identifiers)
        ):
            device_registry.async_remove_device(device.id)
