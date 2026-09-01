"""HA Toolkit custom integration lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import (
    CONF_ATTACH_ENTITY_IDS,
    CONF_CONFIGURATION_TYPE,
    CONF_GROUPS,
    CONF_PRICE_ENTITY_ID,
    CONF_SOURCE_ENTITY_IDS,
    CONFIGURATION_TYPE_LIGHT_GROUP_PLUS,
    LIGHT_GROUP_PLUS_PLATFORMS,
    METER_PLATFORMS,
)
from .models import ConfigurationType, LightGroupPlusConfig, MeterGroup
from .runtime import HAToolkitRuntimeData

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HA Toolkit from a config entry."""
    config = {**entry.data, **entry.options}
    if config.get(CONF_CONFIGURATION_TYPE) == CONFIGURATION_TYPE_LIGHT_GROUP_PLUS:
        entry.runtime_data = LightGroupPlusConfig.from_dict(config)
        await hass.config_entries.async_forward_entry_setups(
            entry, LIGHT_GROUP_PLUS_PLATFORMS
        )
        entry.async_on_unload(entry.add_update_listener(_async_options_updated))
        return True
    runtime_data = HAToolkitRuntimeData(
        hass=hass,
        entry=entry,
        configuration_type=ConfigurationType(config[CONF_CONFIGURATION_TYPE]),
        source_entity_ids=list(config.get(CONF_SOURCE_ENTITY_IDS, [])),
        attach_entity_ids=list(config.get(CONF_ATTACH_ENTITY_IDS, [])),
        groups=[MeterGroup.from_dict(group) for group in config.get(CONF_GROUPS, [])],
        price_entity_id=config.get(CONF_PRICE_ENTITY_ID),
    )
    entry.runtime_data = runtime_data
    await runtime_data.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, METER_PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a HA Toolkit config entry."""
    if entry.runtime_data is None:
        return True
    config = {**entry.data, **entry.options}
    platforms = (
        LIGHT_GROUP_PLUS_PLATFORMS
        if config.get(CONF_CONFIGURATION_TYPE) == CONFIGURATION_TYPE_LIGHT_GROUP_PLUS
        else METER_PLATFORMS
    )
    if not await hass.config_entries.async_unload_platforms(entry, platforms):
        return False
    if isinstance(entry.runtime_data, HAToolkitRuntimeData):
        await entry.runtime_data.async_stop()
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration after its configuration changes."""
    await hass.config_entries.async_reload(entry.entry_id)
