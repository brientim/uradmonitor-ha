"""Config flow for uRADMonitor: pick the data source, then the cloud account and/or local units."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import LocalEndpoint, URadCloudClient, URadLocalClient, parse_endpoints
from .const import (
    CONF_CLOUD_INTERVAL,
    CONF_LOCAL_INTERVAL,
    CONF_LOCALS,
    CONF_MODE,
    CONF_USER_ID,
    CONF_USER_KEY,
    DEFAULT_CLOUD_INTERVAL,
    DEFAULT_LOCAL_INTERVAL,
    DOMAIN,
    LOGGER,
    MAX_INTERVAL,
    MIN_CLOUD_INTERVAL,
    MIN_LOCAL_INTERVAL,
    MODE_BOTH,
    MODE_CLOUD,
    MODE_LOCAL,
)
from .coordinator import URadConfigEntry
from .protocol import URadAuthError, URadConnectionError, URadError

_MENU = ["mode_both", "mode_local", "mode_cloud"]

_CLOUD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USER_ID): TextSelector(),
        vol.Required(CONF_USER_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

_LOCAL_SCHEMA = vol.Schema(
    {vol.Optional(CONF_LOCALS, default=[]): TextSelector(TextSelectorConfig(multiple=True))}
)


class URadConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry per uRADMonitor account and/or set of local units."""

    VERSION = 1

    def __init__(self) -> None:
        """Collect the entry's data across steps."""
        self._data: dict[str, Any] = {}
        self._unit_ids: list[str] = []

    @staticmethod
    @callback
    def async_get_options_flow(entry: URadConfigEntry) -> URadOptionsFlow:
        """Poll intervals."""
        return URadOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose where to read units from."""
        return self.async_show_menu(step_id="user", menu_options=_MENU)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the data source, account or local units."""
        self._data = dict(self._get_reconfigure_entry().data)
        return self.async_show_menu(step_id="reconfigure", menu_options=_MENU)

    async def async_step_mode_both(self, user_input: None = None) -> ConfigFlowResult:
        """Local units first, the cloud for everything else and as a fallback."""
        self._data[CONF_MODE] = MODE_BOTH
        return await self.async_step_cloud()

    async def async_step_mode_local(self, user_input: None = None) -> ConfigFlowResult:
        """Only units on the network; no account or internet needed."""
        self._data[CONF_MODE] = MODE_LOCAL
        return await self.async_step_local()

    async def async_step_mode_cloud(self, user_input: None = None) -> ConfigFlowResult:
        """Every unit on the account, over the internet."""
        self._data[CONF_MODE] = MODE_CLOUD
        self._data[CONF_LOCALS] = []
        return await self.async_step_cloud()

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The account's API Credentials."""
        errors: dict[str, str] = {}
        placeholders = {"details": ""}
        if user_input is not None:
            user_id = user_input[CONF_USER_ID].strip()
            if self._configured(user_id=user_id):
                return self.async_abort(reason="already_configured")
            errors = await self._test_cloud(user_id, user_input[CONF_USER_KEY], placeholders)
            if not errors:
                self._data[CONF_USER_ID] = user_id
                self._data[CONF_USER_KEY] = user_input[CONF_USER_KEY].strip()
                if self._data[CONF_MODE] == MODE_BOTH:
                    return await self.async_step_local()
                return await self._finish()

        suggested = user_input or {
            CONF_USER_ID: self._data.get(CONF_USER_ID, ""),
            CONF_USER_KEY: self._data.get(CONF_USER_KEY, ""),
        }
        return self.async_show_form(
            step_id="cloud",
            data_schema=self.add_suggested_values_to_schema(_CLOUD_SCHEMA, suggested),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_local(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Addresses of units on the network, read from their /j page."""
        errors: dict[str, str] = {}
        placeholders = {"address": "", "details": ""}
        if user_input is not None:
            try:
                endpoints = parse_endpoints(user_input.get(CONF_LOCALS, []))
            except ValueError as err:
                errors[CONF_LOCALS] = "invalid_address"
                placeholders["details"] = str(err)
            else:
                if not endpoints and self._data[CONF_MODE] == MODE_LOCAL:
                    errors[CONF_LOCALS] = "no_local"
                elif self._configured(endpoints=endpoints):
                    return self.async_abort(reason="already_configured")
                else:
                    errors = await self._test_local(endpoints, placeholders)
                if not errors:
                    self._data[CONF_LOCALS] = [str(e) for e in endpoints]
                    return await self._finish()

        suggested = user_input or {CONF_LOCALS: self._data.get(CONF_LOCALS, [])}
        return self.async_show_form(
            step_id="local",
            data_schema=self.add_suggested_values_to_schema(_LOCAL_SCHEMA, suggested),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """uRADMonitor rejected the saved User ID or User Key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the API Credentials again."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        placeholders = {"details": ""}
        if user_input is not None:
            user_id = user_input[CONF_USER_ID].strip()
            errors = await self._test_cloud(user_id, user_input[CONF_USER_KEY], placeholders)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USER_ID: user_id,
                        CONF_USER_KEY: user_input[CONF_USER_KEY].strip(),
                    },
                )

        suggested = user_input or {CONF_USER_ID: entry.data.get(CONF_USER_ID, "")}
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(_CLOUD_SCHEMA, suggested),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _finish(self) -> ConfigFlowResult:
        cloud = self._data[CONF_MODE] != MODE_LOCAL
        data = {
            CONF_MODE: self._data[CONF_MODE],
            CONF_USER_ID: self._data.get(CONF_USER_ID, "") if cloud else "",
            CONF_USER_KEY: self._data.get(CONF_USER_KEY, "") if cloud else "",
            CONF_LOCALS: self._data.get(CONF_LOCALS, []),
        }
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(self._get_reconfigure_entry(), data=data)
        if data[CONF_USER_ID]:
            title = f"uRADMonitor account {data[CONF_USER_ID]}"
        else:
            title = "uRADMonitor " + ", ".join(self._unit_ids)
        return self.async_create_entry(title=title, data=data)

    async def _test_cloud(
        self, user_id: str, user_key: str, placeholders: dict[str, str]
    ) -> dict[str, str]:
        client = URadCloudClient(async_get_clientsession(self.hass))
        try:
            units = await client.get_devices(user_id, user_key)
        except URadAuthError:
            return {"base": "invalid_auth"}
        except URadConnectionError as err:
            placeholders["details"] = str(err)
            return {"base": "cannot_connect"}
        except URadError as err:
            placeholders["details"] = str(err)
            return {"base": "cloud_error"}
        except Exception:
            LOGGER.exception("Unexpected error signing in to uRADMonitor")
            return {"base": "unknown"}
        self._unit_ids = [unit.id for unit in units]
        return {}

    async def _test_local(
        self, endpoints: list[LocalEndpoint], placeholders: dict[str, str]
    ) -> dict[str, str]:
        client = URadLocalClient()
        unit_ids = list(self._unit_ids) if self._data[CONF_MODE] == MODE_BOTH else []
        for endpoint in endpoints:
            try:
                unit = await client.get(endpoint)
            except URadError as err:
                placeholders["address"] = str(endpoint)
                placeholders["details"] = str(err)
                return {CONF_LOCALS: "cannot_connect_local"}
            except Exception:
                LOGGER.exception("Unexpected error reading the uRADMonitor unit at %s", endpoint)
                return {"base": "unknown"}
            if unit.id not in unit_ids:
                unit_ids.append(unit.id)
        self._unit_ids = unit_ids
        return {}

    def _configured(
        self, user_id: str | None = None, endpoints: list[LocalEndpoint] | None = None
    ) -> bool:
        """Whether another entry already reads this account or one of these addresses."""
        keys = {e.key for e in endpoints or []}
        current = (
            self._get_reconfigure_entry().entry_id if self.source == SOURCE_RECONFIGURE else None
        )
        for entry in self._async_current_entries(include_ignore=False):
            if entry.entry_id == current:
                continue
            if user_id and entry.data.get(CONF_USER_ID, "").strip() == user_id:
                return True
            if keys and keys & {e.key for e in parse_endpoints(entry.data.get(CONF_LOCALS, []))}:
                return True
        return False


class URadOptionsFlow(OptionsFlowWithReload):
    """How often to read the cloud and the local units."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Poll intervals in seconds."""
        if user_input is not None:
            return self.async_create_entry(
                data={key: int(value) for key, value in user_input.items()}
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CLOUD_INTERVAL,
                    default=options.get(CONF_CLOUD_INTERVAL, DEFAULT_CLOUD_INTERVAL),
                ): _seconds(MIN_CLOUD_INTERVAL, 30),
                vol.Required(
                    CONF_LOCAL_INTERVAL,
                    default=options.get(CONF_LOCAL_INTERVAL, DEFAULT_LOCAL_INTERVAL),
                ): _seconds(MIN_LOCAL_INTERVAL, 5),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


def _seconds(minimum: int, step: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=MAX_INTERVAL,
            step=step,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="s",
        )
    )
