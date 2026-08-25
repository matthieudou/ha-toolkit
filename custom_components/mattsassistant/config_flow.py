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
    CONF_GROUP_TO_EDIT,
    CONF_GROUPS,
    CONF_PRICE_ENTITY_ID,
    CONF_REMOVE_GROUP,
    CONF_SOURCE_ENTITY_IDS,
    DOMAIN,
)
from .discovery import resolve_source
from .models import MEASUREMENT_SPECS, ConfigurationType, MeterGroup

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


class _MeterFlowMixin:
    """Shared source and group steps for config and options flows."""

    _configuration_type: ConfigurationType
    _source_entity_ids: list[str]
    _attach_entity_ids: list[str]
    _groups: list[MeterGroup]
    _price_entity_id: str | None
    _group_to_edit: str | None

    def _initialize_configuration(self, configuration: dict[str, Any]) -> None:
        """Load mutable flow state from a stored configuration."""
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
        self._group_to_edit = None

    async def async_step_sources(
        self,
        user_input: dict[str, Any] | None = None,
        *,
        errors: dict[str, str] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Select individual sources and an optional price entity."""
        errors = errors or {}
        if user_input is not None:
            selected = list(user_input.get(CONF_SOURCE_ENTITY_IDS, []))
            price_entity_id = user_input.get(CONF_PRICE_ENTITY_ID)
            invalid = [
                entity_id
                for entity_id in selected
                if resolve_source(self.hass, entity_id, self._configuration_type)
                is None
            ]
            if invalid:
                errors["base"] = "invalid_sources"
            elif price_entity_id and not _valid_price_entity(
                self.hass, price_entity_id
            ):
                errors["base"] = "invalid_price"
            else:
                previous = set(self._source_entity_ids)
                attached = set(self._attach_entity_ids).intersection(selected)
                attached.update(set(selected) - previous)
                self._source_entity_ids = selected
                self._attach_entity_ids = [
                    entity_id for entity_id in selected if entity_id in attached
                ]
                self._price_entity_id = price_entity_id
                return await self.async_step_source_options()
        return self.async_show_form(
            step_id="sources",
            data_schema=_sources_schema(
                self._configuration_type,
                self._source_entity_ids,
                self._price_entity_id,
            ),
            errors=errors,
            description_placeholders={
                "price_unit": MEASUREMENT_SPECS[self._configuration_type].result_unit,
            },
        )

    async def async_step_source_options(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Choose which individual entities attach to their source device."""
        if not self._source_entity_ids:
            return await self.async_step_manage_groups()
        eligible = _attachable_entities(self.hass, self._source_entity_ids)
        if not eligible:
            self._attach_entity_ids = []
            return await self.async_step_manage_groups()
        if user_input is not None:
            self._attach_entity_ids = [
                entity_id
                for entity_id in user_input.get(CONF_ATTACH_ENTITY_IDS, [])
                if entity_id in eligible
            ]
            return await self.async_step_manage_groups()
        defaults = [
            entity_id for entity_id in self._attach_entity_ids if entity_id in eligible
        ]
        return self.async_show_form(
            step_id="source_options",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ATTACH_ENTITY_IDS, default=defaults
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            include_entities=eligible,
                            multiple=True,
                            reorder=True,
                        )
                    )
                }
            ),
        )

    async def async_step_manage_groups(
        self, _user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show group management actions before saving."""
        options = ["add_group"]
        if self._groups:
            options.append("edit_group")
        options.extend(["edit_sources", "finish"])
        return self.async_show_menu(step_id="manage_groups", menu_options=options)

    async def async_step_edit_sources(
        self, _user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Return to individual source selection."""
        return await self.async_step_sources()

    async def async_step_add_group(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Add one named aggregate."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input[CONF_GROUP_NAME]).strip()
            entity_ids = list(user_input.get(CONF_GROUP_ENTITY_IDS, []))
            if not name:
                errors[CONF_GROUP_NAME] = "required"
            elif _duplicate_group_name(self._groups, name):
                errors[CONF_GROUP_NAME] = "duplicate_group_name"
            elif not entity_ids:
                errors[CONF_GROUP_ENTITY_IDS] = "required"
            elif not _all_sources_valid(
                self.hass, entity_ids, self._configuration_type
            ):
                errors["base"] = "invalid_sources"
            else:
                self._groups.append(MeterGroup(uuid4().hex, name, entity_ids))
                return await self.async_step_manage_groups()
        return self.async_show_form(
            step_id="add_group",
            data_schema=_group_schema(self._configuration_type, user_input or {}),
            errors=errors,
        )

    async def async_step_edit_group(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Select a configured group to edit."""
        if user_input is not None:
            self._group_to_edit = str(user_input[CONF_GROUP_TO_EDIT])
            return await self.async_step_group_details()
        options = [
            selector.SelectOptionDict(value=group.group_id, label=group.name)
            for group in self._groups
        ]
        return self.async_show_form(
            step_id="edit_group",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GROUP_TO_EDIT): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_group_details(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Edit or remove one named aggregate."""
        group = next(
            item for item in self._groups if item.group_id == self._group_to_edit
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_REMOVE_GROUP):
                self._groups.remove(group)
                self._group_to_edit = None
                return await self.async_step_manage_groups()
            name = str(user_input[CONF_GROUP_NAME]).strip()
            entity_ids = list(user_input.get(CONF_GROUP_ENTITY_IDS, []))
            if not name:
                errors[CONF_GROUP_NAME] = "required"
            elif _duplicate_group_name(self._groups, name, exclude_id=group.group_id):
                errors[CONF_GROUP_NAME] = "duplicate_group_name"
            elif not entity_ids:
                errors[CONF_GROUP_ENTITY_IDS] = "required"
            elif not _all_sources_valid(
                self.hass, entity_ids, self._configuration_type
            ):
                errors["base"] = "invalid_sources"
            else:
                group.name = name
                group.entity_ids = entity_ids
                self._group_to_edit = None
                return await self.async_step_manage_groups()
        defaults = {**group.as_dict(), **(user_input or {})}
        schema = _group_schema(self._configuration_type, defaults).extend(
            {
                vol.Optional(
                    CONF_REMOVE_GROUP, default=False
                ): selector.BooleanSelector(selector.BooleanSelectorConfig())
            }
        )
        return self.async_show_form(
            step_id="group_details", data_schema=schema, errors=errors
        )

    async def async_step_finish(
        self, _user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Validate and persist the flow state."""
        if not self._source_entity_ids and not self._groups:
            return await self.async_step_sources(errors={"base": "no_targets"})
        return self._create_configuration_entry(self._configuration())

    def _configuration(self) -> dict[str, Any]:
        """Serialize the current flow state."""
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
            return await self.async_step_sources()
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
    """Change individual sources, groups, device links and pricing."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options from effective entry configuration."""
        self._config_entry = config_entry
        self._initialize_configuration({**config_entry.data, **config_entry.options})

    async def async_step_init(
        self, _user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Start by editing source selection."""
        return await self.async_step_sources()

    def _create_configuration_entry(
        self, configuration: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        return self.async_create_entry(title="", data=configuration)


def _source_selector(configuration_type: ConfigurationType) -> selector.EntitySelector:
    """Build an entity selector restricted to the expected device class."""
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


def _sources_schema(
    configuration_type: ConfigurationType,
    source_entity_ids: list[str],
    price_entity_id: str | None,
) -> vol.Schema:
    """Build the individual-source form."""
    fields: dict[Any, Any] = {
        vol.Optional(
            CONF_SOURCE_ENTITY_IDS, default=source_entity_ids
        ): _source_selector(configuration_type)
    }
    price_selector = selector.EntitySelector(
        selector.EntitySelectorConfig(
            filter=selector.EntityFilterSelectorConfig(
                domain=["input_number", "number", "sensor"]
            )
        )
    )
    if price_entity_id:
        fields[vol.Optional(CONF_PRICE_ENTITY_ID, default=price_entity_id)] = (
            price_selector
        )
    else:
        fields[vol.Optional(CONF_PRICE_ENTITY_ID)] = price_selector
    return vol.Schema(fields)


def _group_schema(
    configuration_type: ConfigurationType, defaults: dict[str, Any]
) -> vol.Schema:
    """Build a named aggregate form."""
    return vol.Schema(
        {
            vol.Required(
                CONF_GROUP_NAME, default=defaults.get(CONF_GROUP_NAME, "")
            ): selector.TextSelector(selector.TextSelectorConfig()),
            vol.Required(
                CONF_GROUP_ENTITY_IDS,
                default=defaults.get(CONF_GROUP_ENTITY_IDS, []),
            ): _source_selector(configuration_type),
        }
    )


def _attachable_entities(hass: Any, entity_ids: list[str]) -> list[str]:
    """Return selected entities that belong to a device."""
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
    """Return whether every submitted group member remains compatible."""
    return all(
        resolve_source(hass, entity_id, configuration_type) is not None
        for entity_id in entity_ids
    )


def _duplicate_group_name(
    groups: list[MeterGroup], name: str, *, exclude_id: str | None = None
) -> bool:
    """Return whether another group already uses a visible name."""
    normalized = name.casefold()
    return any(
        group.group_id != exclude_id and group.name.casefold() == normalized
        for group in groups
    )


def _valid_price_entity(hass: Any, entity_id: str) -> bool:
    """Return whether the selected price currently has a non-negative number."""
    state = hass.states.get(entity_id)
    if state is None:
        return False
    try:
        return float(state.state) >= 0
    except (TypeError, ValueError):
        return False
