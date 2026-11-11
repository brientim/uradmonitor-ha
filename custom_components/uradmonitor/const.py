"""Constants for uRADMonitor."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "uradmonitor"
LOGGER = logging.getLogger(__package__)

MANUFACTURER: Final = "Magnasci"

CONF_MODE: Final = "mode"
CONF_USER_ID: Final = "user_id"
CONF_USER_KEY: Final = "user_key"
CONF_LOCALS: Final = "locals"
CONF_CLOUD_INTERVAL: Final = "cloud_interval"
CONF_LOCAL_INTERVAL: Final = "local_interval"

# Local first with cloud fallback.
MODE_BOTH: Final = "both"
MODE_LOCAL: Final = "local"
MODE_CLOUD: Final = "cloud"
MODES: Final = (MODE_BOTH, MODE_LOCAL, MODE_CLOUD)

DEFAULT_CLOUD_INTERVAL: Final = 300
DEFAULT_LOCAL_INTERVAL: Final = 60
# Units upload about once a minute; keep the load on uRADMonitor's free API light.
MIN_CLOUD_INTERVAL: Final = 120
MIN_LOCAL_INTERVAL: Final = 10
MAX_INTERVAL: Final = 3600

# A source that hasn't answered for this many of its intervals is treated as offline.
STALE_AFTER_INTERVALS: Final = 3
