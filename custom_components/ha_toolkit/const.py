"""Constants for the HA Toolkit integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "ha_toolkit"
NAME = "HA Toolkit"

CONF_CONFIGURATION_TYPE = "configuration_type"
CONF_SOURCE_ENTITY_IDS = "source_entity_ids"
CONF_ATTACH_ENTITY_IDS = "attach_entity_ids"
CONF_GROUPS = "groups"
CONF_GROUP_ID = "id"
CONF_GROUP_NAME = "name"
CONF_GROUP_ENTITY_IDS = "entity_ids"
CONF_PRICE_ENTITY_ID = "price_entity_id"

CONFIGURATION_TYPE_LIGHT_GROUP_PLUS = "light_group_plus"
CONF_NAME = "name"
CONF_MEMBER_ENTITY_IDS = "member_entity_ids"
CONF_SCENE_ENTITY_IDS = "scene_entity_ids"
# Config-flow-only fields.
CONF_GROUP_TO_EDIT = "group_to_edit"
CONF_REMOVE_GROUP = "remove_group"

METER_PLATFORMS = [Platform.SENSOR]
LIGHT_GROUP_PLUS_PLATFORMS = [Platform.LIGHT]
REFRESH_INTERVAL = timedelta(minutes=15)
