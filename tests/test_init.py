"""Setup, entities, merging over time and diagnostics."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.uradmonitor.const import DOMAIN
from custom_components.uradmonitor.protocol import URadConnectionError, parse_local

from .conftest import CLOUD_FIXTURE, CLOUD_URL, LOCAL_FIXTURE, LOCAL_HOST

LOCAL_GET = "custom_components.uradmonitor.api.URadLocalClient.get"
UNIT = "uradmonitor_82000b02"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.fixture
def local_get() -> AsyncMock:
    """The local unit answers with the fixture."""
    with patch(LOCAL_GET, AsyncMock(side_effect=lambda _: parse_local(LOCAL_FIXTURE))) as mock:
        yield mock


async def test_setup_merges_cloud_and_local(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    local_get: AsyncMock,
) -> None:
    """Local readings win, the cloud adds AQI and its other sensors."""
    aioclient_mock.get(CLOUD_URL, text=CLOUD_FIXTURE)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    assert hass.states.get(f"sensor.{UNIT}_temperature").state == "21.5"
    assert hass.states.get(f"sensor.{UNIT}_radiation_dose").state == "0.15"
    assert (
        hass.states.get(f"sensor.{UNIT}_radiation_dose").attributes["unit_of_measurement"]
        == "μSv/h"
    )
    assert hass.states.get(f"sensor.{UNIT}_carbon_dioxide").state == "577.0"
    assert hass.states.get(f"sensor.{UNIT}_formaldehyde").state == "12.0"
    assert hass.states.get(f"sensor.{UNIT}_air_quality_index").state == "poor"
    assert hass.states.get(f"sensor.{UNIT}_light").state == "123.456"
    assert hass.states.get(f"sensor.{UNIT}_light").attributes["unit_of_measurement"] == "lx"
    assert hass.states.get(f"sensor.{UNIT}_data_source").state == "local"
    assert hass.states.get(f"binary_sensor.{UNIT}_connectivity").state == STATE_ON
    # Disabled by default.
    assert hass.states.get(f"sensor.{UNIT}_signal_strength") is None
    assert hass.states.get(f"sensor.{UNIT}_uptime") is None

    assert hass.states.get("binary_sensor.uradmonitor_11000a01_connectivity").state == STATE_OFF
    assert hass.states.get("sensor.uradmonitor_11000a01_data_source").state == "cloud"

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "82000B02")})
    assert device is not None
    assert (device.name, device.hw_version, device.sw_version) == (
        "uRADMonitor 82000B02",
        "104",
        "124",
    )
    assert device.configuration_url == f"http://{LOCAL_HOST}"


async def test_local_stale_falls_back_to_cloud(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    local_get: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """After three missed local intervals the cloud's readings take over."""
    aioclient_mock.get(CLOUD_URL, text=CLOUD_FIXTURE)
    await _setup(hass, config_entry)
    local_get.side_effect = URadConnectionError("Can't reach")

    await _tick(hass, freezer, 61)
    assert hass.states.get(f"sensor.{UNIT}_temperature").state == "21.5"

    for _ in range(3):
        await _tick(hass, freezer, 61)
    assert hass.states.get(f"sensor.{UNIT}_temperature").state == "20.04"
    assert hass.states.get(f"sensor.{UNIT}_data_source").state == "cloud"


async def test_local_only_unit_goes_offline(
    hass: HomeAssistant, local_get: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    """A local-only unit that stops answering shows offline and its readings unavailable."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={"mode": "local", "user_id": "", "user_key": "", "locals": [LOCAL_HOST]}
    )
    await _setup(hass, entry)
    assert hass.states.get(f"sensor.{UNIT}_temperature").state == "21.5"
    assert hass.states.get(f"sensor.{UNIT}_air_quality_index") is None

    local_get.side_effect = URadConnectionError("Can't reach")
    for _ in range(4):
        await _tick(hass, freezer, 61)
    assert hass.states.get(f"binary_sensor.{UNIT}_connectivity").state == STATE_OFF
    assert hass.states.get(f"sensor.{UNIT}_temperature").state == STATE_UNAVAILABLE

    local_get.side_effect = lambda _: parse_local(LOCAL_FIXTURE)
    await _tick(hass, freezer, 61)
    assert hass.states.get(f"sensor.{UNIT}_temperature").state == "21.5"


async def test_new_sensor_is_added(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A sensor a unit starts reporting later gets an entity without a reload."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={"mode": "cloud", "user_id": "1234", "user_key": "k", "locals": []}
    )
    aioclient_mock.get(CLOUD_URL, text=CLOUD_FIXTURE)
    await _setup(hass, entry)
    assert hass.states.get(f"sensor.{UNIT}_radon") is None

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        CLOUD_URL, text=CLOUD_FIXTURE.replace('"last_lux"', '"last_radon": 84, "last_lux"')
    )
    await _tick(hass, freezer, 301)
    assert hass.states.get(f"sensor.{UNIT}_radon").state == "84.0"


async def test_cloud_polled_on_its_own_interval(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    local_get: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Local units every minute, the cloud every five."""
    aioclient_mock.get(CLOUD_URL, text=CLOUD_FIXTURE)
    await _setup(hass, config_entry)
    assert aioclient_mock.call_count == 1

    for _ in range(4):
        await _tick(hass, freezer, 61)
    assert aioclient_mock.call_count == 1
    assert local_get.call_count == 5

    await _tick(hass, freezer, 61)
    assert aioclient_mock.call_count == 2


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Rejected credentials ask the user to sign in again."""
    aioclient_mock.get(CLOUD_URL, text='{"error":"Authentification failed"}')
    with patch(LOCAL_GET, AsyncMock(side_effect=lambda _: parse_local(LOCAL_FIXTURE))):
        await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_nothing_reachable_retries(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """With no source answering, setup is retried later."""
    aioclient_mock.get(CLOUD_URL, exc=TimeoutError())
    with patch(LOCAL_GET, AsyncMock(side_effect=URadConnectionError("Can't reach"))):
        await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    local_get: AsyncMock,
) -> None:
    """The entry unloads cleanly."""
    aioclient_mock.get(CLOUD_URL, text=CLOUD_FIXTURE)
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_diagnostics_redacted(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    local_get: AsyncMock,
) -> None:
    """No credentials, addresses or position in diagnostics."""
    aioclient_mock.get(CLOUD_URL, text=CLOUD_FIXTURE)
    await _setup(hass, config_entry)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)
    text = str(diagnostics)
    for secret in ("1234", "secret-key", LOCAL_HOST, "37.33", "latitude"):
        assert secret not in text
    assert [unit["id"] for unit in diagnostics["units"]] == ["11000A01", "82000B02"]
