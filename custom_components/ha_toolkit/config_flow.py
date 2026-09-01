"""Config flow adapter for HA Toolkit feature families."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ATTACH_ENTITY_IDS,
    CONF_CONFIGURATION_TYPE,
    CONF_GROUP_ENTITY_IDS,
    CONF_GROUPS,
    CONF_PRICE_ENTITY_ID,
    CONF_SOURCE_ENTITY_IDS,
    CONFIGURATION_TYPE_LIGHT_GROUP_PLUS,
    DOMAIN,
)
from .energy.configuration import (
    CONF_ENTITY_ID,
    CONF_INDIVIDUAL_SOURCES,
    CONF_KEEP_SEPARATE,
    all_sources_valid,
    attachable_entities,
    configuration_schema,
    reconcile_groups,
    valid_price_entity,
)
from .energy.models import MEASUREMENT_SPECS, ConfigurationType, MeterGroup
from .lights.configuration import (
    light_group_plus_schema,
    validate_light_group_plus,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


class _MeterFlowMixin:
    """Share the meter configuration page between setup and options flows."""

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
        """Edit meter sources, device links, groups, and pricing."""
        errors: dict[str, str] = {}
        if user_input is not None:
            submitted_sources = user_input.get(CONF_INDIVIDUAL_SOURCES, [])
            individual_sources = (
                submitted_sources
                if isinstance(submitted_sources, list)
                else [submitted_sources]
            )
            sources = [row.get(CONF_ENTITY_ID) for row in individual_sources]
            price_entity_id = user_input.get(CONF_PRICE_ENTITY_ID)
            submitted_groups = list(user_input.get(CONF_GROUPS, []))
            group_sources = [
                entity_id
                for group in submitted_groups
                for entity_id in group.get(CONF_GROUP_ENTITY_IDS, [])
            ]

            if not sources and not submitted_groups:
                errors["base"] = "no_targets"
            elif not all_sources_valid(
                self.hass, [*sources, *group_sources], self._configuration_type
            ):
                errors["base"] = "invalid_sources"
            elif len(sources) != len(set(sources)):
                errors[CONF_INDIVIDUAL_SOURCES] = "duplicate_source"
            elif price_entity_id and not valid_price_entity(self.hass, price_entity_id):
                errors["base"] = "invalid_price"
            else:
                groups, group_error = reconcile_groups(submitted_groups, self._groups)
                if group_error:
                    errors[CONF_GROUPS] = group_error
                else:
                    attachable = set(attachable_entities(self.hass, sources))
                    self._source_entity_ids = sources
                    self._attach_entity_ids = [
                        entity_id
                        for entity_id, row in zip(
                            sources, individual_sources, strict=True
                        )
                        if entity_id in attachable
                        and not row.get(CONF_KEEP_SEPARATE, False)
                    ]
                    self._groups = groups
                    self._price_entity_id = price_entity_id
                    return self._create_configuration_entry(self._configuration())

        return self.async_show_form(
            step_id="configuration",
            data_schema=configuration_schema(
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


class HAToolkitConfigFlow(_MeterFlowMixin, config_entries.ConfigFlow, domain=DOMAIN):
    """Route setup to one HA Toolkit feature family."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Select the feature to configure."""
        if user_input is not None:
            await self.async_set_unique_id(uuid4().hex)
            if (
                user_input[CONF_CONFIGURATION_TYPE]
                == CONFIGURATION_TYPE_LIGHT_GROUP_PLUS
            ):
                return await self.async_step_light_group_plus()
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
                            options=[
                                *[item.value for item in ConfigurationType],
                                CONFIGURATION_TYPE_LIGHT_GROUP_PLUS,
                            ],
                            translation_key="configuration_type",
                        )
                    )
                }
            ),
        )

    async def async_step_light_group_plus(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure a Light Group+ entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            config, error = validate_light_group_plus(self.hass, user_input)
            if error is None:
                return self.async_create_entry(
                    title=config.name,
                    data={
                        CONF_CONFIGURATION_TYPE: CONFIGURATION_TYPE_LIGHT_GROUP_PLUS,
                        **config.as_dict(),
                    },
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="light_group_plus",
            data_schema=light_group_plus_schema(user_input),
            errors=errors,
        )

    def _create_configuration_entry(
        self, configuration: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        if len(self._source_entity_ids) == 1 and not self._groups:
            state = self.hass.states.get(self._source_entity_ids[0])
            title = state.name if state else "HA Toolkit"
        elif not self._source_entity_ids and len(self._groups) == 1:
            title = self._groups[0].name
        else:
            title = "HA Toolkit"
        return self.async_create_entry(title=title, data=configuration)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> HAToolkitOptionsFlow:
        """Return the options flow."""
        return HAToolkitOptionsFlow(config_entry)


class HAToolkitOptionsFlow(_MeterFlowMixin, config_entries.OptionsFlow):
    """Route options to the configured HA Toolkit feature family."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options from the effective entry configuration."""
        self._config_entry = config_entry
        self._effective_config = {**config_entry.data, **config_entry.options}
        if (
            self._effective_config.get(CONF_CONFIGURATION_TYPE)
            != CONFIGURATION_TYPE_LIGHT_GROUP_PLUS
        ):
            self._initialize_configuration(self._effective_config)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show the feature configuration page."""
        if (
            self._effective_config.get(CONF_CONFIGURATION_TYPE)
            == CONFIGURATION_TYPE_LIGHT_GROUP_PLUS
        ):
            return await self.async_step_light_group_plus(user_input)
        return await self.async_step_configuration(user_input)

    async def async_step_light_group_plus(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Edit a Light Group+ entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            config, error = validate_light_group_plus(self.hass, user_input)
            if error is None:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_CONFIGURATION_TYPE: CONFIGURATION_TYPE_LIGHT_GROUP_PLUS,
                        **config.as_dict(),
                    },
                )
            errors["base"] = error
        defaults = user_input or self._effective_config
        return self.async_show_form(
            step_id="light_group_plus",
            data_schema=light_group_plus_schema(defaults),
            errors=errors,
        )

    def _create_configuration_entry(
        self, configuration: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        return self.async_create_entry(title="", data=configuration)
