"""Migrate unpublished MattsAssistant configuration formats."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from .const import (
    CONF_ATTACH_ENTITY_IDS,
    CONF_AUTO_DISCOVER,
    CONF_CONFIGURATION_TYPE,
    CONF_DEVICE_IDS,
    CONF_GROUPS,
    CONF_LABEL_IDS,
    CONF_PRICE_ENTITY_ID,
    CONF_SOURCE_ENTITY_IDS,
    CONF_SOURCE_ENTRY_IDS,
    DOMAIN,
)
from .discovery import is_supported_sensor
from .models import ConfigurationType, MeterGroup

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


def migrate_legacy_configuration(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Convert device and label selections while preserving registry entities."""
    old_data = {**entry.data, **entry.options}
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    label_registry = lr.async_get(hass)
    selected_devices = set(old_data.get(CONF_DEVICE_IDS, []))
    selected_entry_ids = set(old_data.get(CONF_SOURCE_ENTRY_IDS, []))
    auto_discover = old_data.get(CONF_AUTO_DISCOVER, False)
    candidates: dict[str, tuple[str, str | None, set[str]]] = {}
    for state in hass.states.async_all("sensor"):
        if not is_supported_sensor(
            state.attributes, ConfigurationType.ELECTRICITY_ENERGY
        ):
            continue
        registry_entry = entity_registry.async_get(state.entity_id)
        if registry_entry is None or registry_entry.platform == DOMAIN:
            continue
        device = (
            device_registry.async_get(registry_entry.device_id)
            if registry_entry.device_id
            else None
        )
        labels = set(registry_entry.labels)
        if device:
            labels.update(device.labels)
        candidates[state.entity_id] = (
            registry_entry.id,
            registry_entry.device_id,
            labels,
        )

    source_entity_ids = [
        entity_id
        for entity_id, (registry_entry_id, device_id, _labels) in candidates.items()
        if auto_discover
        or device_id in selected_devices
        or registry_entry_id in selected_entry_ids
    ]
    old_to_new_target_keys = {
        candidates[entity_id][0]: candidates[entity_id][0]
        for entity_id in source_entity_ids
    }
    groups: list[MeterGroup] = []
    for label_id in old_data.get(CONF_LABEL_IDS, []):
        label = label_registry.async_get_label(label_id)
        members = [
            entity_id
            for entity_id, (_entry_id, _device_id, labels) in candidates.items()
            if label_id in labels
        ]
        if not label or not members:
            continue
        group_id = uuid4().hex
        groups.append(MeterGroup(group_id, label.name, members))
        old_to_new_target_keys[f"label_{label_id}"] = f"group_{group_id}"

    _migrate_unique_ids(hass, entry, old_to_new_target_keys)
    return {
        CONF_CONFIGURATION_TYPE: ConfigurationType.ELECTRICITY_ENERGY.value,
        CONF_SOURCE_ENTITY_IDS: source_entity_ids,
        CONF_ATTACH_ENTITY_IDS: source_entity_ids,
        CONF_GROUPS: [group.as_dict() for group in groups],
        CONF_PRICE_ENTITY_ID: old_data.get(CONF_PRICE_ENTITY_ID),
    }


def _migrate_unique_ids(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    old_to_new_target_keys: dict[str, str],
) -> None:
    """Namespace old generated unique IDs without changing their entity IDs."""
    registry = er.async_get(hass)
    entries = [
        item
        for item in registry.entities.values()
        if item.platform == DOMAIN and item.config_entry_id == config_entry.entry_id
    ]
    for registry_entry in entries:
        for old_key, new_key in old_to_new_target_keys.items():
            prefix = f"{old_key}_"
            if not registry_entry.unique_id.startswith(prefix):
                continue
            suffix = registry_entry.unique_id.removeprefix(prefix)
            registry.async_update_entity(
                registry_entry.entity_id,
                new_unique_id=f"{config_entry.entry_id}_{new_key}_{suffix}",
            )
            break
