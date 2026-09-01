"""Light Group+ platform for HA Toolkit."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.group.light import LightGroup
from homeassistant.components.light import (
    LightEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_ON,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .models import LightGroupPlusConfig

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

PARALLEL_UPDATES = 0


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one Light Group+ entity."""
    config = entry.runtime_data
    if not isinstance(config, LightGroupPlusConfig):
        return
    async_add_entities([LightGroupPlus(entry.entry_id, config)])


class LightGroupPlus(LightGroup):
    """A native light group whose effects activate configured scenes."""

    def __init__(self, unique_id: str, config: LightGroupPlusConfig) -> None:
        """Initialize the group and its scene mapping."""
        super().__init__(unique_id, config.name, list(config.member_entity_ids), None)
        self._config = config
        self._effect_scenes: dict[str, str] = {}
        self._attr_effect: str | None = None

    async def async_added_to_hass(self) -> None:
        """Resolve scenes and start the native group listeners."""
        await super().async_added_to_hass()
        self._resolve_effects()
        self.async_update_group_state()

    @callback
    def _resolve_effects(self) -> None:
        """Map visible scene names to their native entity IDs."""
        self._effect_scenes = {}
        for entity_id in self._config.scene_entity_ids:
            if (state := self.hass.states.get(entity_id)) is not None:
                self._effect_scenes[state.name] = entity_id
        self._attr_effect_list = list(self._effect_scenes)

    @callback
    def async_update_group_state(self) -> None:
        """Aggregate native light attributes and restore scene effects."""
        current_effect = self._attr_effect
        super().async_update_group_state()
        self._attr_effect_list = list(self._effect_scenes)
        self._attr_effect = current_effect
        self._attr_supported_features |= LightEntityFeature.EFFECT

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Activate a requested scene, or the first scene by default."""
        effect = kwargs.get("effect") or next(iter(self._effect_scenes), None)
        scene_entity_id = self._effect_scenes.get(effect)
        if scene_entity_id is None:
            raise HomeAssistantError(f"Unknown Light Group+ effect: {effect}")
        await self.hass.services.async_call(
            "scene",
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: scene_entity_id},
            blocking=True,
            context=self._context,
        )
        self._attr_effect = effect
        forwarded = {key: value for key, value in kwargs.items() if key != "effect"}
        if forwarded:
            await super().async_turn_on(**forwarded)
        self.async_write_ha_state()
