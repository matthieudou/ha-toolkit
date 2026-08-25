"""MattsAssistant custom integration lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import (
    CONF_ATTACH_ENTITY_IDS,
    CONF_CONFIGURATION_TYPE,
    CONF_GROUPS,
    CONF_PRICE_ENTITY_ID,
    CONF_SOURCE_ENTITY_IDS,
    PLATFORMS,
)
from .migration import migrate_legacy_configuration
from .models import ConfigurationType, MeterGroup
from .runtime import MattsAssistantRuntimeData

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

CONFIG_ENTRY_VERSION = 3


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MattsAssistant from a config entry."""
    config = {**entry.data, **entry.options}
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
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a MattsAssistant config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_stop()
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate selections from the unpublished energy-only prototypes."""
    if entry.version >= CONFIG_ENTRY_VERSION:
        return True
    data = migrate_legacy_configuration(hass, entry)
    hass.config_entries.async_update_entry(
        entry, data=data, options={}, version=CONFIG_ENTRY_VERSION
    )
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration after its configuration changes."""
    await hass.config_entries.async_reload(entry.entry_id)
