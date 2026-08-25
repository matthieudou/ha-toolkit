"""Calendar, rolling and cost sensors for MattsAssistant."""

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
    from .runtime import MattsAssistantRuntimeData, MeterTargetRuntime


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
    runtime_data: MattsAssistantRuntimeData = entry.runtime_data
    entities: list[MattsAssistantSensor] = []
    for target in runtime_data.targets:
        entities.extend(MeasurementMetricSensor(target, metric) for metric in METRICS)
        if runtime_data.price is not None:
            entities.extend(CostMetricSensor(target, metric) for metric in METRICS)
    _remove_stale_registry_entries(entry, entities)
    async_add_entities(entities)


class MattsAssistantSensor(SensorEntity):
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
        if target.kind is TargetKind.GROUP:
            entry_prefix = f"{self.runtime.entry_id}_" if self.runtime.entry_id else ""
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{entry_prefix}{target.key}")},
                name=target.name,
                manufacturer=NAME,
                model=f"{target.sources[0].configuration_type.value} group",
            )

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
        target = self.runtime.target
        if target.kind is TargetKind.SOURCE:
            registry = er.async_get(self.hass)
            if registry.async_get(self.entity_id):
                device_id = (
                    target.device_id
                    if target.attach_to_device and target.device_id
                    else None
                )
                registry.async_update_entity(self.entity_id, device_id=device_id)
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


class MeasurementMetricSensor(MattsAssistantSensor):
    """Consumption by a target in one metric window."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3

    def __init__(self, runtime: MeterTargetRuntime, metric: Metric) -> None:
        """Initialize a consumption metric."""
        spec = runtime.target.sources[0].spec
        self._attr_device_class = spec.result_device_class
        self._attr_native_unit_of_measurement = spec.result_unit
        super().__init__(runtime, metric, ValueKind.MEASUREMENT)


class CostMetricSensor(MattsAssistantSensor):
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
    entry: ConfigEntry, entities: list[MattsAssistantSensor]
) -> None:
    """Remove generated entities and group devices no longer in the config."""
    hass = entry.runtime_data.hass
    entity_registry = er.async_get(hass)
    expected_unique_ids = {entity.unique_id for entity in entities}
    for registry_entry in list(entity_registry.entities.values()):
        if (
            registry_entry.platform == DOMAIN
            and registry_entry.config_entry_id == entry.entry_id
            and registry_entry.unique_id not in expected_unique_ids
        ):
            entity_registry.async_remove(registry_entry.entity_id)

    expected_group_identifiers = {
        identifier
        for entity in entities
        if entity.device_info
        for identifier in entity.device_info.get("identifiers", set())
    }
    device_registry = dr.async_get(hass)
    for device in list(device_registry.devices.values()):
        integration_identifiers = {
            identifier for identifier in device.identifiers if identifier[0] == DOMAIN
        }
        if (
            entry.entry_id in device.config_entries
            and integration_identifiers
            and integration_identifiers.isdisjoint(expected_group_identifiers)
        ):
            device_registry.async_remove_device(device.id)
