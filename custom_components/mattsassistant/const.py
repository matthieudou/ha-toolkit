"""Constants for the MattsAssistant integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "mattsassistant"
NAME = "MattsAssistant"

CONF_CONFIGURATION_TYPE = "configuration_type"
CONF_SOURCE_ENTITY_IDS = "source_entity_ids"
CONF_ATTACH_ENTITY_IDS = "attach_entity_ids"
CONF_GROUPS = "groups"
CONF_GROUP_ID = "id"
CONF_GROUP_NAME = "name"
CONF_GROUP_ENTITY_IDS = "entity_ids"
CONF_PRICE_ENTITY_ID = "price_entity_id"

# Config-flow-only fields.
CONF_GROUP_TO_EDIT = "group_to_edit"
CONF_REMOVE_GROUP = "remove_group"

# Legacy keys from the unpublished prototypes, used only during migration.
CONF_AUTO_DISCOVER = "auto_discover"
CONF_DEVICE_IDS = "device_ids"
CONF_LABEL_IDS = "label_ids"
CONF_SOURCE_ENTRY_IDS = "source_entry_ids"

PLATFORMS = [Platform.SENSOR]
REFRESH_INTERVAL = timedelta(minutes=15)
