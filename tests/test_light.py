"""Tests for the Light Group+ platform."""

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TOGGLE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.core import HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_toolkit.const import (
    CONF_CONFIGURATION_TYPE,
    CONF_MEMBER_ENTITY_IDS,
    CONF_NAME,
    CONF_SCENE_ENTITY_IDS,
    CONFIGURATION_TYPE_LIGHT_GROUP_PLUS,
    DOMAIN,
)
from custom_components.ha_toolkit.light import LightGroupPlus
from custom_components.ha_toolkit.models import LightGroupPlusConfig


def _entity(hass: HomeAssistant) -> LightGroupPlus:
    hass.states.async_set(
        "light.ceiling",
        "off",
        {"supported_color_modes": [ColorMode.BRIGHTNESS]},
    )
    hass.states.async_set("light.lamp", "off")
    hass.states.async_set(
        "scene.living_room_default", "scening", {"friendly_name": "Default"}
    )
    hass.states.async_set(
        "scene.living_room_cozy", "scening", {"friendly_name": "Cozy"}
    )
    entity = LightGroupPlus(
        "entry-id",
        LightGroupPlusConfig(
            "Living room",
            ("light.ceiling", "light.lamp"),
            ("scene.living_room_default", "scene.living_room_cozy"),
        ),
    )
    entity.hass = hass
    entity.entity_id = "light.living_room"
    entity._resolve_effects()
    entity.async_update_group_state()
    return entity


def test_effects_are_configured_scene_names_in_order(hass: HomeAssistant) -> None:
    """Native effects expose only configured scene names."""
    entity = _entity(hass)

    assert entity.effect_list == ["Default", "Cozy"]
    assert entity.supported_color_modes == {ColorMode.BRIGHTNESS}


def test_group_is_on_when_any_member_is_on_and_averages_brightness(
    hass: HomeAssistant,
) -> None:
    """Member state follows classic any-member light-group semantics."""
    entity = _entity(hass)
    hass.states.async_set("light.ceiling", "on", {ATTR_BRIGHTNESS: 100})
    hass.states.async_set("light.lamp", "on", {ATTR_BRIGHTNESS: 200})

    entity.async_update_group_state()

    assert entity.is_on is True
    assert entity.brightness == 150


async def test_explicit_effect_activates_scene_for_the_current_runtime(
    hass: HomeAssistant,
) -> None:
    """An effect delegates to scene.turn_on and is retained only in memory."""
    entity = _entity(hass)
    entity.async_write_ha_state = lambda: None
    calls: list[ServiceCall] = []

    async def record_call(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("scene", SERVICE_TURN_ON, record_call)

    await entity.async_turn_on(effect="Cozy")

    assert len(calls) == 1
    assert calls[0].domain == "scene"
    assert calls[0].service == SERVICE_TURN_ON
    assert calls[0].data == {ATTR_ENTITY_ID: "scene.living_room_cozy"}
    assert entity.effect == "Cozy"


async def test_plain_turn_on_uses_first_scene_and_forwards_brightness(
    hass: HomeAssistant,
) -> None:
    """A plain turn-on uses the first scene before forwarding brightness."""
    entity = _entity(hass)
    entity.async_write_ha_state = lambda: None
    calls: list[ServiceCall] = []

    async def record_call(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("scene", SERVICE_TURN_ON, record_call)
    hass.services.async_register("light", SERVICE_TURN_ON, record_call)

    await entity.async_turn_on(brightness=90)

    assert calls[0].domain == "scene"
    assert calls[0].data == {ATTR_ENTITY_ID: "scene.living_room_default"}
    assert calls[1].domain == "light"
    assert calls[1].data == {
        ATTR_ENTITY_ID: ["light.ceiling", "light.lamp"],
        ATTR_BRIGHTNESS: 90,
    }


async def test_turn_off_targets_every_member(hass: HomeAssistant) -> None:
    """Turning off the group delegates to all configured lights."""
    entity = _entity(hass)
    calls: list[ServiceCall] = []

    async def record_call(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("light", SERVICE_TURN_OFF, record_call)

    await entity.async_turn_off()

    assert len(calls) == 1
    assert calls[0].domain == "light"
    assert calls[0].service == SERVICE_TURN_OFF
    assert calls[0].data == {ATTR_ENTITY_ID: ["light.ceiling", "light.lamp"]}


async def test_config_entry_exposes_native_effects_and_toggle(
    recorder_mock: object,
    enable_custom_integrations: None,
    hass: HomeAssistant,
) -> None:
    """The loaded entity works through Home Assistant's standard light services."""
    del recorder_mock, enable_custom_integrations
    hass.states.async_set("light.ceiling", "off")
    hass.states.async_set("light.lamp", "off")
    hass.states.async_set(
        "scene.living_room_default", "scening", {"friendly_name": "Default"}
    )
    hass.states.async_set(
        "scene.living_room_cozy", "scening", {"friendly_name": "Cozy"}
    )
    scene_calls: list[ServiceCall] = []

    async def record_scene(call: ServiceCall) -> None:
        scene_calls.append(call)

    hass.services.async_register("scene", SERVICE_TURN_ON, record_scene)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living room",
        unique_id="light-group-plus",
        version=1,
        data={
            CONF_CONFIGURATION_TYPE: CONFIGURATION_TYPE_LIGHT_GROUP_PLUS,
            CONF_NAME: "Living room",
            CONF_MEMBER_ENTITY_IDS: ["light.ceiling", "light.lamp"],
            CONF_SCENE_ENTITY_IDS: [
                "scene.living_room_default",
                "scene.living_room_cozy",
            ],
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("light.living_room")
    assert state is not None
    assert state.attributes["effect_list"] == ["Default", "Cozy"]

    await hass.services.async_call(
        "light",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "light.living_room", "effect": "Cozy"},
        blocking=True,
    )
    assert scene_calls[-1].data == {ATTR_ENTITY_ID: "scene.living_room_cozy"}

    await hass.services.async_call(
        "light",
        SERVICE_TOGGLE,
        {ATTR_ENTITY_ID: "light.living_room"},
        blocking=True,
    )
    assert scene_calls[-1].data == {ATTR_ENTITY_ID: "scene.living_room_default"}
