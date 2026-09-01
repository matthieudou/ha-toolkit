"""Configuration validation and schemas for meter entries."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from custom_components.ha_toolkit.const import (
    CONF_ATTACH_ENTITY_IDS,
    CONF_GROUP_ENTITY_IDS,
    CONF_GROUP_NAME,
    CONF_GROUPS,
    CONF_PRICE_ENTITY_ID,
    CONF_SOURCE_ENTITY_IDS,
)

from .discovery import resolve_source
from .models import MEASUREMENT_SPECS, ConfigurationType, MeterGroup

CONF_ENTITY_ID = "entity_id"
CONF_INDIVIDUAL_SOURCES = "individual_sources"
CONF_KEEP_SEPARATE = "keep_separate"


def configuration_schema(
    configuration_type: ConfigurationType,
    user_input: dict[str, Any] | None,
    configuration: dict[str, Any],
) -> vol.Schema:
    """Build the complete editable meter form."""
    defaults = user_input or {}
    source_entity_ids = list(configuration.get(CONF_SOURCE_ENTITY_IDS, []))
    attach_entity_ids = list(configuration.get(CONF_ATTACH_ENTITY_IDS, []))
    individual_sources = defaults.get(
        CONF_INDIVIDUAL_SOURCES,
        [
            {
                CONF_ENTITY_ID: entity_id,
                CONF_KEEP_SEPARATE: entity_id not in attach_entity_ids,
            }
            for entity_id in source_entity_ids
        ],
    )
    group_defaults = defaults.get(
        CONF_GROUPS,
        [
            {
                CONF_GROUP_NAME: group[CONF_GROUP_NAME],
                CONF_GROUP_ENTITY_IDS: group[CONF_GROUP_ENTITY_IDS],
            }
            for group in configuration.get(CONF_GROUPS, [])
        ],
    )
    fields: dict[Any, Any] = {
        vol.Optional(
            CONF_INDIVIDUAL_SOURCES, default=individual_sources
        ): selector.ObjectSelector(
            selector.ObjectSelectorConfig(
                multiple=True,
                label_field=CONF_ENTITY_ID,
                translation_key="individual_source",
                fields={
                    CONF_ENTITY_ID: {
                        "required": True,
                        "selector": source_selector(
                            configuration_type, multiple=False
                        ).serialize()["selector"],
                    },
                    CONF_KEEP_SEPARATE: {
                        "selector": selector.BooleanSelector().serialize()["selector"]
                    },
                },
            )
        ),
        vol.Optional(CONF_GROUPS, default=group_defaults): selector.ObjectSelector(
            selector.ObjectSelectorConfig(
                multiple=True,
                label_field=CONF_GROUP_NAME,
                fields={
                    CONF_GROUP_NAME: {
                        "required": True,
                        "selector": selector.TextSelector().serialize()["selector"],
                    },
                    CONF_GROUP_ENTITY_IDS: {
                        "required": True,
                        "selector": source_selector(configuration_type).serialize()[
                            "selector"
                        ],
                    },
                },
            )
        ),
    }
    price_selector = selector.EntitySelector(
        selector.EntitySelectorConfig(
            filter=selector.EntityFilterSelectorConfig(
                domain=["input_number", "number", "sensor"]
            )
        )
    )
    if current_price := defaults.get(
        CONF_PRICE_ENTITY_ID, configuration.get(CONF_PRICE_ENTITY_ID)
    ):
        fields[vol.Optional(CONF_PRICE_ENTITY_ID, default=current_price)] = (
            price_selector
        )
    else:
        fields[vol.Optional(CONF_PRICE_ENTITY_ID)] = price_selector
    return vol.Schema(fields)


def source_selector(
    configuration_type: ConfigurationType, *, multiple: bool = True
) -> selector.EntitySelector:
    """Build a selector for compatible meter sources."""
    spec = MEASUREMENT_SPECS[configuration_type]
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            filter=selector.EntityFilterSelectorConfig(
                domain="sensor", device_class=spec.source_device_class
            ),
            multiple=multiple,
            reorder=multiple,
        )
    )


def attachable_entities(hass: Any, entity_ids: list[str]) -> list[str]:
    """Return sources that belong to a physical device."""
    registry = er.async_get(hass)
    return [
        entity_id
        for entity_id in entity_ids
        if (entry := registry.async_get(entity_id)) is not None
        and entry.device_id is not None
    ]


def all_sources_valid(
    hass: Any,
    entity_ids: list[str],
    configuration_type: ConfigurationType,
) -> bool:
    """Return whether every source matches the meter configuration."""
    return all(
        resolve_source(hass, entity_id, configuration_type) is not None
        for entity_id in entity_ids
    )


def reconcile_groups(
    submitted: list[dict[str, Any]], existing: list[MeterGroup]
) -> tuple[list[MeterGroup], str | None]:
    """Validate groups and retain stable IDs by name, then by position."""
    groups: list[MeterGroup] = []
    names: set[str] = set()
    existing_by_name = {group.name.casefold(): group for group in existing}
    for index, value in enumerate(submitted):
        name = str(value.get(CONF_GROUP_NAME, "")).strip()
        entity_ids = list(value.get(CONF_GROUP_ENTITY_IDS, []))
        if not name or not entity_ids:
            return [], "required"
        normalized = name.casefold()
        if normalized in names:
            return [], "duplicate_group_name"
        names.add(normalized)
        matched = existing_by_name.get(normalized)
        if matched is None and index < len(existing):
            matched = existing[index]
        groups.append(
            MeterGroup(matched.group_id if matched else uuid4().hex, name, entity_ids)
        )
    return groups, None


def valid_price_entity(hass: Any, entity_id: str) -> bool:
    """Return whether an entity exposes a non-negative numeric price."""
    state = hass.states.get(entity_id)
    if state is None:
        return False
    try:
        return float(state.state) >= 0
    except (TypeError, ValueError):
        return False
