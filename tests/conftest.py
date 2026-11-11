"""Fixtures for uRADMonitor tests.

Unit IDs are synthetic; position fields hold Apple Park to prove they're dropped.
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.uradmonitor.const import (
    CONF_LOCALS,
    CONF_MODE,
    CONF_USER_ID,
    CONF_USER_KEY,
    DOMAIN,
    MODE_BOTH,
)

CLOUD_URL = "https://data.uradmonitor.com/api/v1/devices"

CLOUD_FIXTURE = """[
  {
    "id": "11000A01", "timefirst": 1421613359, "timelast": 1565551652, "timelocal": 91,
    "latitude": 37.3349, "longitude": -122.009, "altitude": 40, "city": "Cupertino", "country": "US",
    "placement": 0, "versionsw": 123, "versionhw": 109, "status": null, "mobile": null,
    "detector": "SBM20", "factor": 0.006315, "aqi": null,
    "param_temperature": ["Temperature", "°C"], "avg_temperature": "23.09", "last_temperature": null,
    "param_cpm": ["Radiation", "cpm"], "avg_cpm": 22, "last_cpm": null
  },
  {
    "id": "82000b02", "timefirst": 1481581690, "timelast": 1788010820, "timelocal": 202140,
    "latitude": 37.3349, "longitude": -122.009, "altitude": 83.84, "speed": null,
    "placement": 1, "versionsw": 124, "versionhw": 104, "status": 1, "mobile": 0,
    "detector": "SI29BG", "factor": 0.01, "aqi": 4, "errors": null, "faulty": null,
    "param_temperature": ["Temperature", "°C"], "avg_temperature": "20.02", "last_temperature": "20.04",
    "param_pressure": ["Pressure", "Pa"], "avg_pressure": 100323, "last_pressure": 100322,
    "param_humidity": ["Humidity", "% RH"], "last_humidity": "67.99",
    "param_co2": ["Carbon Dioxide", "ppm"], "last_co2": 577,
    "param_ch2o": ["Formaldehyde", "ppb"], "last_ch2o": 2,
    "param_pm25": ["PM2.5", "µg/m³"], "last_pm25": 3,
    "param_cpm": ["Radiation", "cpm"], "avg_cpm": 18, "last_cpm": 12,
    "param_lux": ["Light", "lx"], "last_lux": "123.456"
  }
]"""

LOCAL_FIXTURE = (
    '{"data": {"id":"82000b02","type":"82","detector":"SI29BG","temperature":21.5,"humidity":55.25,'
    '"pressure":101355,"ch2o":0.012,"cpm":15,"uptime":3700,"latitude":37.3349,"longitude":-122.009}}'
)

# The shape of a live model-8 (A3) reply: HTTP/1.0, text/html, no Content-Length.
LIVE_A3_REPLY = (
    "HTTP/1.0 200 OK\r\nContent-Type: text/html\r\nPragma: no-cache\r\n\r\n"
    '{"data":{"id":"8200FF01","type":"8","detector":"SI29BG","voltage":381,"cpm":15,"temperature":32.0,'
    '"humidity":27.69,"pressure":100814,"voc":723603,"co2":440,"noise":71,"ch2o":0.03,"pm25":1,"uptime":691493}}'
)

LOCAL_HOST = "192.0.2.50"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/uradmonitor."""


@pytest.fixture
def mock_setup_entry() -> Generator[None]:
    """Skip setting up the entry after the config flow."""
    with patch("custom_components.uradmonitor.async_setup_entry", return_value=True):
        yield


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Cloud account plus one local unit."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="uRADMonitor account 1234",
        data={
            CONF_MODE: MODE_BOTH,
            CONF_USER_ID: "1234",
            CONF_USER_KEY: "secret-key",
            CONF_LOCALS: [LOCAL_HOST],
        },
    )
