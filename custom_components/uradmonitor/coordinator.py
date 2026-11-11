"""Polls the uRADMonitor cloud account and local units and merges them per unit."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

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
    MODES,
    STALE_AFTER_INTERVALS,
)
from .protocol import UnitSnapshot, URadAuthError, URadError, merge

type URadConfigEntry = ConfigEntry[URadCoordinator]

# A cloud poll is due this close to its interval, so coordinator timing jitter doesn't skip a cycle.
_CLOUD_SLACK = timedelta(seconds=5)


@dataclass(slots=True)
class LocalStatus:
    """The latest result from one local address."""

    endpoint: LocalEndpoint
    unit_id: str | None = None
    ok: bool = False
    message: str = "Connecting…"
    last_success: datetime | None = None


@dataclass(frozen=True, slots=True)
class Settings:
    """The entry's data and options, normalized."""

    mode: str
    user_id: str
    user_key: str
    locals: list[LocalEndpoint]
    cloud_interval: int
    local_interval: int

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> Settings:
        """Read and clamp the entry's settings."""
        mode = str(entry.data.get(CONF_MODE, MODE_BOTH)).strip().lower()
        return cls(
            mode=mode if mode in MODES else MODE_BOTH,
            user_id=str(entry.data.get(CONF_USER_ID, "")).strip(),
            user_key=str(entry.data.get(CONF_USER_KEY, "")).strip(),
            locals=parse_endpoints(entry.data.get(CONF_LOCALS, [])),
            cloud_interval=_clamp(
                entry.options.get(CONF_CLOUD_INTERVAL, DEFAULT_CLOUD_INTERVAL),
                MIN_CLOUD_INTERVAL,
            ),
            local_interval=_clamp(
                entry.options.get(CONF_LOCAL_INTERVAL, DEFAULT_LOCAL_INTERVAL),
                MIN_LOCAL_INTERVAL,
            ),
        )

    @property
    def uses_cloud(self) -> bool:
        """The cloud is read unless Local only, and only with credentials."""
        return self.mode != MODE_LOCAL and bool(self.user_id and self.user_key)

    @property
    def uses_local(self) -> bool:
        """Local units are read unless Cloud only."""
        return self.mode != MODE_CLOUD and bool(self.locals)


class URadCoordinator(DataUpdateCoordinator[dict[str, UnitSnapshot]]):
    """Merged snapshots by unit ID.

    Ticks at the local interval (or the cloud interval when there are no local
    units) and reads the cloud only when its own interval is due. Local readings
    win while the unit answered within three local intervals.
    """

    config_entry: URadConfigEntry

    def __init__(self, hass: HomeAssistant, entry: URadConfigEntry) -> None:
        """Set up the clients from the entry."""
        self.settings = Settings.from_entry(entry)
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=self.settings.local_interval
                if self.settings.uses_local
                else self.settings.cloud_interval
            ),
        )
        self.cloud = URadCloudClient(async_get_clientsession(hass))
        self.local = URadLocalClient()
        self.cloud_ok = False
        self.cloud_message = "Connecting…" if self.settings.uses_cloud else "Not used"
        self.cloud_last_success: datetime | None = None
        self._cloud_due: datetime | None = None
        self._cloud_units: dict[str, UnitSnapshot] = {}
        self._local_units: dict[str, UnitSnapshot] = {}
        self.local_statuses: dict[str, LocalStatus] = (
            {e.key: LocalStatus(e) for e in self.settings.locals}
            if self.settings.uses_local
            else {}
        )
        self._logged: dict[str, str] = {}

    async def async_request_full_refresh(self) -> None:
        """Read every source now, including the cloud before its interval is due."""
        self._cloud_due = None
        await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, UnitSnapshot]:
        now = dt_util.utcnow()
        tasks = []
        if self.settings.uses_cloud and (self._cloud_due is None or now >= self._cloud_due):
            tasks.append(self._poll_cloud(now))
        if self.settings.uses_local:
            tasks.extend(self._poll_local(status) for status in self.local_statuses.values())
        await asyncio.gather(*tasks)

        units = self.merged(dt_util.utcnow())
        if not units:
            problems = self.problems()
            if problems:
                raise UpdateFailed("; ".join(problems))
        return units

    async def _poll_cloud(self, now: datetime) -> None:
        self._cloud_due = now + timedelta(seconds=self.settings.cloud_interval) - _CLOUD_SLACK
        try:
            units = await self.cloud.get_devices(self.settings.user_id, self.settings.user_key)
        except URadAuthError as err:
            self.cloud_ok = False
            self.cloud_message = str(err)
            raise ConfigEntryAuthFailed(str(err)) from err
        except URadError as err:
            self.cloud_ok = False
            self.cloud_message = str(err)
            self._log_once("cloud", f"Cloud: {err}")
            return
        self._cloud_units = {unit.id: unit for unit in units}
        self.cloud_ok = True
        self.cloud_last_success = dt_util.utcnow()
        self.cloud_message = "1 unit" if len(units) == 1 else f"{len(units)} units"
        self._recovered("cloud", "Cloud")

    async def _poll_local(self, status: LocalStatus) -> None:
        source = f"local:{status.endpoint.key}"
        try:
            unit = await self.local.get(status.endpoint)
        except URadError as err:
            status.ok = False
            status.message = str(err)
            self._log_once(source, f"Local {status.endpoint}: {err}")
            return
        self._local_units[status.endpoint.key] = unit
        status.unit_id = unit.id
        status.ok = True
        status.message = f"Unit {unit.id}"
        status.last_success = dt_util.utcnow()
        self._recovered(source, f"Local {status.endpoint}")

    def merged(self, now: datetime) -> dict[str, UnitSnapshot]:
        """Combine cloud and local snapshots by unit ID."""
        cloud_stale = timedelta(seconds=self.settings.cloud_interval * STALE_AFTER_INTERVALS)
        cloud_fresh = (
            self.cloud_last_success is not None and now - self.cloud_last_success <= cloud_stale
        )
        return merge_units(
            self._cloud_units,
            cloud_fresh,
            self._local_units,
            self.local_statuses,
            timedelta(seconds=self.settings.local_interval * STALE_AFTER_INTERVALS),
            now,
        )

    def problems(self) -> list[str]:
        """Sources that failed their latest read."""
        problems = []
        if self.settings.uses_cloud and not self.cloud_ok:
            problems.append(f"Cloud: {self.cloud_message}")
        problems.extend(
            f"{status.endpoint}: {status.message}"
            for status in self.local_statuses.values()
            if not status.ok
        )
        return problems

    def _log_once(self, source: str, message: str) -> None:
        """Log each distinct problem once, so an unreachable unit doesn't fill the log."""
        if self._logged.get(source) != message:
            self._logged[source] = message
            LOGGER.warning(message)

    def _recovered(self, source: str, label: str) -> None:
        if self._logged.pop(source, None) is not None:
            LOGGER.info("%s is answering again", label)


def merge_units(
    cloud_units: dict[str, UnitSnapshot],
    cloud_fresh: bool,
    local_by_endpoint: dict[str, UnitSnapshot],
    local_statuses: dict[str, LocalStatus],
    local_stale_after: timedelta,
    now: datetime,
) -> dict[str, UnitSnapshot]:
    """Fresh local units merged with the cloud; stale sources show their units offline."""
    fresh_local: dict[str, UnitSnapshot] = {}
    stale_local: dict[str, UnitSnapshot] = {}
    for key, unit in local_by_endpoint.items():
        status = local_statuses.get(key)
        fresh = (
            status is not None
            and status.last_success is not None
            and now - status.last_success <= local_stale_after
        )
        (fresh_local if fresh else stale_local)[unit.id] = unit

    merged: dict[str, UnitSnapshot] = {}
    for unit_id in sorted({*cloud_units, *fresh_local, *stale_local}):
        cloud = cloud_units.get(unit_id)
        if cloud is not None and not cloud_fresh:
            cloud = cloud.copy()
            cloud.online = False
        if unit_id in fresh_local:
            unit = merge(cloud, fresh_local[unit_id])
        elif cloud is not None:
            unit = cloud.copy()
        else:
            unit = stale_local[unit_id].copy()
            unit.online = False
        if unit is not None:
            merged[unit_id] = unit
    return merged


def _clamp(value: object, minimum: int) -> int:
    try:
        number = int(float(str(value)))
    except ValueError:
        number = minimum
    return max(minimum, min(MAX_INTERVAL, number))
