"""Home Assistant sensor platform adapter for HA Toolkit."""

from .energy.sensor import async_setup_entry

__all__ = ["async_setup_entry"]
