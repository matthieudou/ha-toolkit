"""Domain models for HA Toolkit light entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from custom_components.ha_toolkit.const import (
    CONF_MEMBER_ENTITY_IDS,
    CONF_NAME,
    CONF_SCENE_ENTITY_IDS,
)


@dataclass(frozen=True, slots=True)
class LightGroupPlusConfig:
    """Configuration owned by one Light Group+ config entry."""

    name: str
    member_entity_ids: tuple[str, ...]
    scene_entity_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> LightGroupPlusConfig:
        """Parse the serializable config-entry representation."""
        return cls(
            name=str(value[CONF_NAME]),
            member_entity_ids=tuple(value[CONF_MEMBER_ENTITY_IDS]),
            scene_entity_ids=tuple(value[CONF_SCENE_ENTITY_IDS]),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the serializable config-entry representation."""
        return {
            CONF_NAME: self.name,
            CONF_MEMBER_ENTITY_IDS: list(self.member_entity_ids),
            CONF_SCENE_ENTITY_IDS: list(self.scene_entity_ids),
        }
