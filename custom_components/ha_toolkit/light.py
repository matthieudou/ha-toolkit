"""Home Assistant light platform adapter for HA Toolkit."""

from .lights.group import async_setup_entry

__all__ = ["async_setup_entry"]
