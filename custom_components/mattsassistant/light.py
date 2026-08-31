"""Light Group+ platform for MattsAssistant."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    COLOR_MODES_BRIGHTNESS,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .models import LightGroupPlusConfig, TurnOnBehavior

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, State
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

PARALLEL_UPDATES = 0


@dataclass(frozen=True, slots=True)
class LightGroupPlusStoredData(ExtraStoredData):
    """State that persists independently from helpers and config options."""

    last_effect: str | None

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-serializable restore data."""
        return {"last_effect": self.last_effect}


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


class LightGroupPlus(LightEntity, RestoreEntity):
    """A native light group whose effects activate configured scenes."""

    _attr_icon = "mdi:lightbulb-group"
    _attr_should_poll = False
    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(self, unique_id: str, config: LightGroupPlusConfig) -> None:
        """Initialize the group and its scene mapping."""
        self._config = config
        self._attr_unique_id = unique_id
        self._attr_name = config.name
        self._attr_extra_state_attributes = {
            ATTR_ENTITY_ID: list(config.member_entity_ids)
        }
        self._effect_scenes: dict[str, str] = {}
        self._default_effect: str | None = None
        self._last_effect: str | None = None
        self._attr_effect: str | None = None
        self._attr_is_on = False
        self._attr_available = False
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF

    async def async_added_to_hass(self) -> None:
        """Resolve scenes, restore the last preset, and track members."""
        await super().async_added_to_hass()
        self._resolve_effects()
        if (stored := await self.async_get_last_extra_data()) is not None:
            restored_effect = stored.as_dict().get("last_effect")
            if restored_effect in self._effect_scenes:
                self._last_effect = restored_effect
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                self._config.member_entity_ids,
                self._async_member_state_changed,
            )
        )
        self._update_group_state()

    @property
    def extra_restore_state_data(self) -> LightGroupPlusStoredData:
        """Persist the last selected scene through restarts and reloads."""
        return LightGroupPlusStoredData(self._last_effect)

    @callback
    def _resolve_effects(self) -> None:
        """Map visible scene names to their native entity IDs."""
        self._effect_scenes = {}
        for entity_id in self._config.scene_entity_ids:
            if (state := self.hass.states.get(entity_id)) is not None:
                self._effect_scenes[state.name] = entity_id
        self._attr_effect_list = list(self._effect_scenes)
        self._default_effect = next(
            (
                effect
                for effect, entity_id in self._effect_scenes.items()
                if entity_id == self._config.default_scene_entity_id
            ),
            None,
        )

    @callback
    def _async_member_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Refresh native light state when a member changes."""
        self.async_set_context(event.context)
        self._update_group_state()
        self.async_write_ha_state()

    @callback
    def _update_group_state(self) -> None:
        """Aggregate the V1 state and brightness of member lights."""
        states = [
            state
            for entity_id in self._config.member_entity_ids
            if (state := self.hass.states.get(entity_id)) is not None
        ]
        usable = [
            state
            for state in states
            if state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        ]
        on_states = [state for state in states if state.state == STATE_ON]
        self._attr_available = bool(usable)
        self._attr_is_on = any(state.state == STATE_ON for state in states)
        brightness_values = [
            brightness
            for state in on_states
            if isinstance((brightness := state.attributes.get(ATTR_BRIGHTNESS)), int)
        ]
        self._attr_brightness = (
            round(sum(brightness_values) / len(brightness_values))
            if brightness_values
            else None
        )
        brightness_supported = any(
            _state_supports_brightness(state) for state in states
        )
        self._attr_supported_color_modes = {
            ColorMode.BRIGHTNESS if brightness_supported else ColorMode.ONOFF
        }
        self._attr_color_mode = next(iter(self._attr_supported_color_modes))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Activate a requested, default, or last scene."""
        requested_effect = kwargs.get("effect")
        effect = requested_effect or self._effect_for_plain_turn_on()
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
        self._last_effect = effect
        if ATTR_BRIGHTNESS in kwargs:
            await self.hass.services.async_call(
                "light",
                SERVICE_TURN_ON,
                {
                    ATTR_ENTITY_ID: list(self._config.member_entity_ids),
                    ATTR_BRIGHTNESS: kwargs[ATTR_BRIGHTNESS],
                },
                blocking=True,
                context=self._context,
            )
        self.async_write_ha_state()

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Turn off every member light."""
        await self.hass.services.async_call(
            "light",
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: list(self._config.member_entity_ids)},
            blocking=True,
            context=self._context,
        )

    def _effect_for_plain_turn_on(self) -> str | None:
        """Choose the configured preset for a turn_on without effect."""
        if (
            self._config.turn_on_behavior is TurnOnBehavior.LAST
            and self._last_effect in self._effect_scenes
        ):
            return self._last_effect
        return self._default_effect


def _state_supports_brightness(state: State) -> bool:
    """Return whether one member advertises a brightness color mode."""
    color_modes = state.attributes.get("supported_color_modes", [])
    return any(ColorMode(mode) in COLOR_MODES_BRIGHTNESS for mode in color_modes)
