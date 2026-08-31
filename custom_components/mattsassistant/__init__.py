"""MattsAssistant custom integration lifecycle."""

from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING

from homeassistant.helpers.start import async_at_started

from .const import (
    CONF_ATTACH_ENTITY_IDS,
    CONF_CONFIGURATION_TYPE,
    CONF_GROUPS,
    CONF_PRICE_ENTITY_ID,
    CONF_REBUILD_STATISTICS,
    CONF_SOURCE_ENTITY_IDS,
    CONFIGURATION_TYPE_LIGHT_GROUP_PLUS,
    LIGHT_GROUP_PLUS_PLATFORMS,
    METER_PLATFORMS,
)
from .migration import migrate_legacy_configuration
from .models import ConfigurationType, LightGroupPlusConfig, MeterGroup
from .recorder import async_clear_derived_statistics
from .runtime import MattsAssistantRuntimeData

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

CONFIG_ENTRY_VERSION = 4
LEGACY_CONFIGURATION_VERSION = 3


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MattsAssistant from a config entry."""
    config = {**entry.data, **entry.options}
    if config.get(CONF_CONFIGURATION_TYPE) == CONFIGURATION_TYPE_LIGHT_GROUP_PLUS:
        entry.runtime_data = LightGroupPlusConfig.from_dict(config)
        await hass.config_entries.async_forward_entry_setups(
            entry, LIGHT_GROUP_PLUS_PLATFORMS
        )
        entry.async_on_unload(entry.add_update_listener(_async_options_updated))
        return True
    if config.get(CONF_REBUILD_STATISTICS):
        entry.runtime_data = None
        entry.async_on_unload(
            async_at_started(
                hass,
                partial(_async_rebuild_statistics, entry=entry),
            )
        )
        return True
    runtime_data = MattsAssistantRuntimeData(
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
    """Unload a MattsAssistant config entry."""
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
    if isinstance(entry.runtime_data, MattsAssistantRuntimeData):
        await entry.runtime_data.async_stop()
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate selections from the unpublished energy-only prototypes."""
    if entry.version >= CONFIG_ENTRY_VERSION:
        return True
    data = (
        migrate_legacy_configuration(hass, entry)
        if entry.version < LEGACY_CONFIGURATION_VERSION
        else {**entry.data, **entry.options}
    )
    data[CONF_REBUILD_STATISTICS] = True
    hass.config_entries.async_update_entry(
        entry, data=data, options={}, version=CONFIG_ENTRY_VERSION
    )
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration after its configuration changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_rebuild_statistics(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Run a one-time statistics repair after Recorder starts processing tasks."""
    try:
        await async_clear_derived_statistics(hass, entry.entry_id)
    except Exception:  # The retained marker allows a later retry.
        _LOGGER.exception("Unable to rebuild statistics for %s", entry.entry_id)
        return

    data = dict(entry.data)
    data.pop(CONF_REBUILD_STATISTICS, None)
    hass.config_entries.async_update_entry(entry, data=data)
    await hass.config_entries.async_reload(entry.entry_id)
