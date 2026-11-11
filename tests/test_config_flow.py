"""Config, reauth, reconfigure and options flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.uradmonitor.const import (
    CONF_CLOUD_INTERVAL,
    CONF_LOCAL_INTERVAL,
    CONF_LOCALS,
    CONF_MODE,
    CONF_USER_ID,
    CONF_USER_KEY,
    DOMAIN,
)
from custom_components.uradmonitor.protocol import URadConnectionError, parse_local

from .conftest import CLOUD_FIXTURE, CLOUD_URL, LOCAL_FIXTURE, LOCAL_HOST

LOCAL_GET = "custom_components.uradmonitor.api.URadLocalClient.get"

pytestmark = pytest.mark.usefixtures("mock_setup_entry")


async def _start(hass: HomeAssistant, mode: str) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": mode})


async def test_cloud_only(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Credentials are tested, then the entry is created."""
    aioclient_mock.get(CLOUD_URL, text='{"error":"Authentification failed"}')
    result = await _start(hass, "mode_cloud")
    assert result["step_id"] == "cloud"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USER_ID: "1234", CONF_USER_KEY: "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(CLOUD_URL, text=CLOUD_FIXTURE)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USER_ID: " 1234 ", CONF_USER_KEY: " key "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "uRADMonitor account 1234"
    assert result["data"] == {
        CONF_MODE: "cloud",
        CONF_USER_ID: "1234",
        CONF_USER_KEY: "key",
        CONF_LOCALS: [],
    }


async def test_cloud_unreachable(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A timeout shows the reason."""
    aioclient_mock.get(CLOUD_URL, exc=TimeoutError())
    result = await _start(hass, "mode_cloud")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USER_ID: "1234", CONF_USER_KEY: "key"}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert "didn't answer" in result["description_placeholders"]["details"]


async def test_local_only(hass: HomeAssistant) -> None:
    """Each address must answer; the title names the units found."""
    result = await _start(hass, "mode_local")
    assert result["step_id"] == "local"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_LOCALS: []})
    assert result["errors"] == {CONF_LOCALS: "no_local"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCALS: ["10.0.0.5:99999"]}
    )
    assert result["errors"] == {CONF_LOCALS: "invalid_address"}

    with patch(LOCAL_GET, AsyncMock(side_effect=URadConnectionError("Can't reach"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_LOCALS: [LOCAL_HOST]}
        )
    assert result["errors"] == {CONF_LOCALS: "cannot_connect_local"}
    assert result["description_placeholders"]["address"] == LOCAL_HOST

    with patch(LOCAL_GET, AsyncMock(side_effect=lambda _: parse_local(LOCAL_FIXTURE))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_LOCALS: [f" {LOCAL_HOST}:80 ", ""]}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "uRADMonitor 82000B02"
    assert result["data"] == {
        CONF_MODE: "local",
        CONF_USER_ID: "",
        CONF_USER_KEY: "",
        CONF_LOCALS: [LOCAL_HOST],
    }


async def test_local_and_cloud(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Cloud first, then optional local units."""
    aioclient_mock.get(CLOUD_URL, text=CLOUD_FIXTURE)
    result = await _start(hass, "mode_both")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USER_ID: "1234", CONF_USER_KEY: "key"}
    )
    assert result["step_id"] == "local"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_LOCALS: []})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MODE] == "both"
    assert result["data"][CONF_LOCALS] == []


async def test_already_configured(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """The same account or address can't be added twice."""
    config_entry.add_to_hass(hass)
    result = await _start(hass, "mode_cloud")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USER_ID: "1234", CONF_USER_KEY: "key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"

    result = await _start(hass, "mode_local")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCALS: [f"{LOCAL_HOST}:80"]}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """New credentials are tested and saved."""
    config_entry.add_to_hass(hass)
    aioclient_mock.get(CLOUD_URL, text=CLOUD_FIXTURE)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USER_ID: "1234", CONF_USER_KEY: "new-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_USER_KEY] == "new-key"
    assert config_entry.data[CONF_LOCALS] == [LOCAL_HOST]


async def test_reconfigure_to_local_only(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Switching to Local only drops the credentials and keeps its own address."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "mode_local"}
    )
    assert result["step_id"] == "local"
    with patch(LOCAL_GET, AsyncMock(side_effect=lambda _: parse_local(LOCAL_FIXTURE))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_LOCALS: [LOCAL_HOST, "192.0.2.51"]}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data == {
        CONF_MODE: "local",
        CONF_USER_ID: "",
        CONF_USER_KEY: "",
        CONF_LOCALS: [LOCAL_HOST, "192.0.2.51"],
    }


async def test_options(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Poll intervals are saved as whole seconds."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CLOUD_INTERVAL: 600.0, CONF_LOCAL_INTERVAL: 30.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_CLOUD_INTERVAL: 600, CONF_LOCAL_INTERVAL: 30}
