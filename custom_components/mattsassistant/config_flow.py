"""Config flow for MattsAssistant."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_ATTACH_ENTITY_IDS,
    CONF_CONFIGURATION_TYPE,
    CONF_GROUP_ENTITY_IDS,
    CONF_GROUP_NAME,
    CONF_GROUPS,
    CONF_PRICE_ENTITY_ID,
    CONF_SOURCE_ENTITY_IDS,
    DOMAIN,
)
from .discovery import resolve_source
from .models import MEASUREMENT_SPECS, ConfigurationType, MeterGroup

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

CONF_DETACH_ENTITY_IDS = "detach_entity_ids"


class _MeterFlowMixin:
    """Share one complete configuration page between both flows."""

    _configuration_type: ConfigurationType
    _source_entity_ids: list[str]
    _attach_entity_ids: list[str]
    _groups: list[MeterGroup]
    _price_entity_id: str | None

    def _initialize_configuration(self, configuration: dict[str, Any]) -> None:
        self._configuration_type = ConfigurationType(
            configuration[CONF_CONFIGURATION_TYPE]
        )
        self._source_entity_ids = list(configuration.get(CONF_SOURCE_ENTITY_IDS, []))
        self._attach_entity_ids = list(
            configuration.get(CONF_ATTACH_ENTITY_IDS, self._source_entity_ids)
        )
        self._groups = [
            MeterGroup.from_dict(group) for group in configuration.get(CONF_GROUPS, [])
        ]
        self._price_entity_id = configuration.get(CONF_PRICE_ENTITY_ID)

    async def async_step_configuration(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Edit sources, device links, groups, and pricing on one page."""
        errors: dict[str, str] = {}
        if user_input is not None:
            sources = list(user_input.get(CONF_SOURCE_ENTITY_IDS, []))
            detached = set(user_input.get(CONF_DETACH_ENTITY_IDS, []))
            price_entity_id = user_input.get(CONF_PRICE_ENTITY_ID)
            submitted_groups = list(user_input.get(CONF_GROUPS, []))
            group_sources = [
                entity_id
                for group in submitted_groups
                for entity_id in group.get(CONF_GROUP_ENTITY_IDS, [])
            ]

            if not sources and not submitted_groups:
                errors["base"] = "no_targets"
            elif not _all_sources_valid(
                self.hass, [*sources, *group_sources], self._configuration_type
            ):
                errors["base"] = "invalid_sources"
            elif detached.difference(sources):
                errors[CONF_DETACH_ENTITY_IDS] = "invalid_detached_sources"
            elif price_entity_id and not _valid_price_entity(
                self.hass, price_entity_id
            ):
                errors["base"] = "invalid_price"
            else:
                groups, group_error = _reconcile_groups(submitted_groups, self._groups)
                if group_error:
                    errors[CONF_GROUPS] = group_error
                else:
                    attachable = set(_attachable_entities(self.hass, sources))
                    self._source_entity_ids = sources
                    self._attach_entity_ids = [
                        entity_id
                        for entity_id in sources
                        if entity_id in attachable and entity_id not in detached
                    ]
                    self._groups = groups
                    self._price_entity_id = price_entity_id
                    return self._create_configuration_entry(self._configuration())

        return self.async_show_form(
            step_id="configuration",
            data_schema=_configuration_schema(
                self._configuration_type,
                user_input,
                self._configuration(),
            ),
            errors=errors,
            description_placeholders={
                "price_unit": MEASUREMENT_SPECS[self._configuration_type].result_unit,
            },
        )

    def _configuration(self) -> dict[str, Any]:
        return {
            CONF_CONFIGURATION_TYPE: self._configuration_type.value,
            CONF_SOURCE_ENTITY_IDS: self._source_entity_ids,
            CONF_ATTACH_ENTITY_IDS: self._attach_entity_ids,
            CONF_GROUPS: [group.as_dict() for group in self._groups],
            CONF_PRICE_ENTITY_ID: self._price_entity_id,
        }

    def _create_configuration_entry(
        self, configuration: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        raise NotImplementedError


class MattsAssistantConfigFlow(
    _MeterFlowMixin, config_entries.ConfigFlow, domain=DOMAIN
):
    """Configure one type of meter and its aggregates."""

    VERSION = 3

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Select the kind of source values in this configuration."""
        if user_input is not None:
            await self.async_set_unique_id(uuid4().hex)
            self._initialize_configuration(
                {
                    CONF_CONFIGURATION_TYPE: user_input[CONF_CONFIGURATION_TYPE],
                    CONF_SOURCE_ENTITY_IDS: [],
                    CONF_ATTACH_ENTITY_IDS: [],
                    CONF_GROUPS: [],
                }
            )
            return await self.async_step_configuration()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONFIGURATION_TYPE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[item.value for item in ConfigurationType],
                            translation_key="configuration_type",
                        )
                    )
                }
            ),
        )

    def _create_configuration_entry(
        self, configuration: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        if len(self._source_entity_ids) == 1 and not self._groups:
            state = self.hass.states.get(self._source_entity_ids[0])
            title = state.name if state else "MattsAssistant"
        elif not self._source_entity_ids and len(self._groups) == 1:
            title = self._groups[0].name
        else:
            title = "MattsAssistant"
        return self.async_create_entry(title=title, data=configuration)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> MattsAssistantOptionsFlow:
        """Return the options flow."""
        return MattsAssistantOptionsFlow(config_entry)


class MattsAssistantOptionsFlow(_MeterFlowMixin, config_entries.OptionsFlow):
    """Change all settings from one options page."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options from the effective entry configuration."""
        self._config_entry = config_entry
        self._initialize_configuration({**config_entry.data, **config_entry.options})

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show the single configuration page."""
        return await self.async_step_configuration(user_input)

    def _create_configuration_entry(
        self, configuration: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        return self.async_create_entry(title="", data=configuration)


def _configuration_schema(
    configuration_type: ConfigurationType,
    user_input: dict[str, Any] | None,
    configuration: dict[str, Any],
) -> vol.Schema:
    """Build the complete editable configuration form."""
    defaults = user_input or {}
    source_entity_ids = list(configuration.get(CONF_SOURCE_ENTITY_IDS, []))
    attach_entity_ids = list(configuration.get(CONF_ATTACH_ENTITY_IDS, []))
    sources = defaults.get(CONF_SOURCE_ENTITY_IDS, source_entity_ids)
    detached = defaults.get(
        CONF_DETACH_ENTITY_IDS,
        [
            entity_id
            for entity_id in source_entity_ids
            if entity_id not in attach_entity_ids
        ],
    )
    group_defaults = defaults.get(
        CONF_GROUPS,
        [
            {
                CONF_GROUP_NAME: group[CONF_GROUP_NAME],
                CONF_GROUP_ENTITY_IDS: group[CONF_GROUP_ENTITY_IDS],
            }
            for group in configuration.get(CONF_GROUPS, [])
        ],
    )
    fields: dict[Any, Any] = {
        vol.Optional(CONF_SOURCE_ENTITY_IDS, default=sources): _source_selector(
            configuration_type
        ),
        vol.Optional(CONF_DETACH_ENTITY_IDS, default=detached): _source_selector(
            configuration_type
        ),
        vol.Optional(CONF_GROUPS, default=group_defaults): selector.ObjectSelector(
            selector.ObjectSelectorConfig(
                multiple=True,
                label_field=CONF_GROUP_NAME,
                fields={
                    CONF_GROUP_NAME: {
                        "required": True,
                        "selector": selector.TextSelector().serialize()["selector"],
                    },
                    CONF_GROUP_ENTITY_IDS: {
                        "required": True,
                        "selector": _source_selector(configuration_type).serialize()[
                            "selector"
                        ],
                    },
                },
            )
        ),
    }
    price_selector = selector.EntitySelector(
        selector.EntitySelectorConfig(
            filter=selector.EntityFilterSelectorConfig(
                domain=["input_number", "number", "sensor"]
            )
        )
    )
    if current_price := defaults.get(
        CONF_PRICE_ENTITY_ID, configuration.get(CONF_PRICE_ENTITY_ID)
    ):
        fields[vol.Optional(CONF_PRICE_ENTITY_ID, default=current_price)] = (
            price_selector
        )
    else:
        fields[vol.Optional(CONF_PRICE_ENTITY_ID)] = price_selector
    return vol.Schema(fields)


def _source_selector(configuration_type: ConfigurationType) -> selector.EntitySelector:
    spec = MEASUREMENT_SPECS[configuration_type]
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            filter=selector.EntityFilterSelectorConfig(
                domain="sensor", device_class=spec.source_device_class
            ),
            multiple=True,
            reorder=True,
        )
    )


def _attachable_entities(hass: Any, entity_ids: list[str]) -> list[str]:
    registry = er.async_get(hass)
    return [
        entity_id
        for entity_id in entity_ids
        if (entry := registry.async_get(entity_id)) is not None
        and entry.device_id is not None
    ]


def _all_sources_valid(
    hass: Any,
    entity_ids: list[str],
    configuration_type: ConfigurationType,
) -> bool:
    return all(
        resolve_source(hass, entity_id, configuration_type) is not None
        for entity_id in entity_ids
    )


def _reconcile_groups(
    submitted: list[dict[str, Any]], existing: list[MeterGroup]
) -> tuple[list[MeterGroup], str | None]:
    """Validate groups and retain stable IDs by name, then by position."""
    groups: list[MeterGroup] = []
    names: set[str] = set()
    existing_by_name = {group.name.casefold(): group for group in existing}
    for index, value in enumerate(submitted):
        name = str(value.get(CONF_GROUP_NAME, "")).strip()
        entity_ids = list(value.get(CONF_GROUP_ENTITY_IDS, []))
        if not name or not entity_ids:
            return [], "required"
        normalized = name.casefold()
        if normalized in names:
            return [], "duplicate_group_name"
        names.add(normalized)
        matched = existing_by_name.get(normalized)
        if matched is None and index < len(existing):
            matched = existing[index]
        groups.append(
            MeterGroup(matched.group_id if matched else uuid4().hex, name, entity_ids)
        )
    return groups, None


def _valid_price_entity(hass: Any, entity_id: str) -> bool:
    state = hass.states.get(entity_id)
    if state is None:
        return False
    try:
        return float(state.state) >= 0
    except (TypeError, ValueError):
        return False
