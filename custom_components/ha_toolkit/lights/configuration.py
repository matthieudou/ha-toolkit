"""Configuration validation and schemas for Light Group+ entries."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.helpers import selector

from custom_components.ha_toolkit.const import (
    CONF_MEMBER_ENTITY_IDS,
    CONF_NAME,
    CONF_SCENE_ENTITY_IDS,
)

from .models import LightGroupPlusConfig


def light_group_plus_schema(defaults: dict[str, Any] | None) -> vol.Schema:
    """Build the Light Group+ form."""
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, default=values.get(CONF_NAME, "")
            ): selector.TextSelector(),
            vol.Required(
                CONF_MEMBER_ENTITY_IDS,
                default=values.get(CONF_MEMBER_ENTITY_IDS, []),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="light", multiple=True, reorder=True
                )
            ),
            vol.Required(
                CONF_SCENE_ENTITY_IDS,
                default=values.get(CONF_SCENE_ENTITY_IDS, []),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="scene", multiple=True, reorder=True
                )
            ),
        }
    )


def validate_light_group_plus(
    hass: Any, user_input: dict[str, Any]
) -> tuple[LightGroupPlusConfig, str | None]:
    """Validate and normalize one Light Group+ submission."""
    name = str(user_input.get(CONF_NAME, "")).strip()
    members = tuple(user_input.get(CONF_MEMBER_ENTITY_IDS, []))
    scenes = tuple(user_input.get(CONF_SCENE_ENTITY_IDS, []))
    config = LightGroupPlusConfig(name, members, scenes)
    if not name or not members or not scenes:
        return config, "required"
    if len(members) != len(set(members)) or len(scenes) != len(set(scenes)):
        return config, "duplicate_entity"
    if any(hass.states.get(entity_id) is None for entity_id in (*members, *scenes)):
        return config, "missing_entity"
    scene_names = [
        state.name.casefold()
        for entity_id in scenes
        if (state := hass.states.get(entity_id)) is not None
    ]
    if len(scene_names) != len(set(scene_names)):
        return config, "duplicate_scene_name"
    return config, None
