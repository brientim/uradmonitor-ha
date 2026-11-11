"""Diagnostics for uRADMonitor, without credentials or unit addresses."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_LOCALS, CONF_USER_ID, CONF_USER_KEY
from .coordinator import URadConfigEntry

TO_REDACT = {CONF_USER_ID, CONF_USER_KEY, CONF_LOCALS}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: URadConfigEntry
) -> dict[str, Any]:
    """Settings, source status and the latest merged units."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "cloud": {
            "used": coordinator.settings.uses_cloud,
            "ok": coordinator.cloud_ok,
            "message": coordinator.cloud_message,
            "last_success": coordinator.cloud_last_success,
        },
        "locals": [
            {
                "unit_id": status.unit_id,
                "ok": status.ok,
                "message": status.message.replace(str(status.endpoint), "**REDACTED**"),
                "last_success": status.last_success,
            }
            for status in coordinator.local_statuses.values()
        ],
        "units": [asdict(unit) for unit in (coordinator.data or {}).values()],
    }
