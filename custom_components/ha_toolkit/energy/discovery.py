"""Resolve configured sensor entities into immutable meter targets."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from custom_components.ha_toolkit.const import DOMAIN

from .models import (
    MEASUREMENT_SPECS,
    ConfigurationType,
    MeterGroup,
    MeterSource,
    MeterTarget,
    TargetKind,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def is_supported_sensor(
    attributes: dict[str, Any], configuration_type: ConfigurationType
) -> bool:
    """Return whether state attributes match a configuration type."""
    spec = MEASUREMENT_SPECS[configuration_type]
    unit = attributes.get("unit_of_measurement")
    if (
        attributes.get("device_class") != spec.source_device_class
        or attributes.get("state_class") not in spec.source_state_classes
        or not isinstance(unit, str)
    ):
        return False
    try:
        spec.normalize(1.0, unit)
    except (TypeError, ValueError):
        return False
    return True


def resolve_source(
    hass: HomeAssistant,
    entity_id: str,
    configuration_type: ConfigurationType,
) -> MeterSource | None:
    """Resolve one configured entity if its current metadata is compatible."""
    state = hass.states.get(entity_id)
    if state is None or not is_supported_sensor(state.attributes, configuration_type):
        return None
    entry = er.async_get(hass).async_get(entity_id)
    if entry and entry.platform == DOMAIN:
        return None
    return MeterSource(
        registry_entry_id=entry.id if entry else "",
        entity_id=entity_id,
        device_id=entry.device_id if entry else None,
        name=state.name or entity_id,
        unit=state.attributes["unit_of_measurement"],
        configuration_type=configuration_type,
    )


def resolve_meter_targets(
    hass: HomeAssistant,
    configuration_type: ConfigurationType,
    source_entity_ids: list[str],
    attach_entity_ids: list[str],
    groups: list[MeterGroup],
) -> tuple[MeterTarget, ...]:
    """Resolve individual selections and named groups into meter targets."""
    all_entity_ids = set(source_entity_ids)
    for group in groups:
        all_entity_ids.update(group.entity_ids)
    sources = {
        entity_id: source
        for entity_id in sorted(all_entity_ids)
        if (source := resolve_source(hass, entity_id, configuration_type)) is not None
    }

    attach = set(attach_entity_ids)
    targets: list[MeterTarget] = []
    for entity_id in source_entity_ids:
        if (source := sources.get(entity_id)) is None:
            continue
        targets.append(
            MeterTarget(
                key=source.key,
                kind=TargetKind.SOURCE,
                name=source.name,
                sources=(source,),
                attach_to_device=entity_id in attach and source.device_id is not None,
                device_id=source.device_id,
                source_entity_id=entity_id,
            )
        )

    for group in groups:
        configured_members = group.entity_ids
        if not configured_members or any(
            entity_id not in sources for entity_id in configured_members
        ):
            continue
        members = tuple(sources[entity_id] for entity_id in configured_members)
        targets.append(
            MeterTarget(
                key=f"group_{group.group_id}",
                kind=TargetKind.GROUP,
                name=group.name,
                sources=members,
            )
        )
    return tuple(targets)


def target_object_id(target: MeterTarget, quantity: str) -> str:
    """Return a readable base object id for generated sensor entities."""
    if target.source_entity_id:
        return target.source_entity_id.split(".", 1)[-1]
    return f"{slugify(target.name)}_{quantity}"
