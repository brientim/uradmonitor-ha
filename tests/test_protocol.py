"""Parsing, units and merging (ports URadSelfTest)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import json

import pytest

from custom_components.uradmonitor.api import LocalEndpoint
from custom_components.uradmonitor.coordinator import LocalStatus, merge_units
from custom_components.uradmonitor.protocol import (
    AQI_KEY,
    DOSE_KEY,
    UPTIME_KEY,
    Source,
    URadAuthError,
    URadError,
    base_url,
    factor_for,
    merge,
    number,
    parse_cloud_devices,
    parse_local,
)

from .conftest import CLOUD_FIXTURE, LOCAL_FIXTURE


def test_cloud_offline_unit() -> None:
    """An offline unit has only null last_* values, so no readings."""
    unit = parse_cloud_devices(CLOUD_FIXTURE)[0]
    assert unit.id == "11000A01"
    assert unit.online is False
    assert unit.factor == 0.006315
    assert unit.readings == {}


def test_cloud_online_unit() -> None:
    """Values are normalized, derived and ordered."""
    unit = parse_cloud_devices(CLOUD_FIXTURE)[1]
    assert unit.id == "82000B02"
    assert unit.online is True
    assert unit.source is Source.CLOUD
    assert unit.aqi == 4
    assert unit.version_hw == 104
    assert unit.version_sw == 124
    assert unit.last_seen == datetime.fromtimestamp(1788010820, UTC)
    assert unit.readings["temperature"].value == pytest.approx(20.04)
    assert unit.readings["pressure"].value == pytest.approx(1003.22)
    assert unit.readings["ch2o"].value == 2
    assert unit.readings[DOSE_KEY].value == pytest.approx(0.12)
    assert unit.readings[AQI_KEY].value == 4
    assert unit.readings[UPTIME_KEY].value == 202140
    lux = unit.readings["lux"]
    assert (lux.name, lux.unit, lux.value) == ("Light", "lx", pytest.approx(123.456))
    keys = list(unit.readings)
    assert keys[0] == DOSE_KEY
    assert keys[-1] == "lux"


def test_drops_position() -> None:
    """Position never reaches a snapshot."""
    snapshots = [*parse_cloud_devices(CLOUD_FIXTURE), parse_local(LOCAL_FIXTURE)]
    for unit in snapshots:
        text = json.dumps(asdict(unit), default=str).lower()
        for forbidden in ("37.33", "122.0", "latitude", "longitude", "altitude", "cupertino"):
            assert forbidden not in text


def test_cloud_errors() -> None:
    """Auth errors are recognised; other errors pass through."""
    with pytest.raises(URadAuthError, match="rejected the User ID or User Key"):
        parse_cloud_devices('{"error":"Authentification failed"}')
    with pytest.raises(URadAuthError):
        parse_cloud_devices('{"error":"Public api needs validation"}')
    with pytest.raises(URadError, match=r"^uRADMonitor replied: Rate limited$") as err:
        parse_cloud_devices('{"error":"Rate limited"}')
    assert not isinstance(err.value, URadAuthError)


@pytest.mark.parametrize("reply", ["<html>maintenance</html>", "", '{"ok":1}', "42"])
def test_cloud_unexpected_reply(reply: str) -> None:
    """Anything but a list of units is an error."""
    with pytest.raises(URadError):
        parse_cloud_devices(reply)


def test_cloud_empty_account() -> None:
    """An account without units."""
    assert parse_cloud_devices("[]") == []


def test_local_data_object() -> None:
    """Local units scale ch2o and pressure and get the dose from the tube table."""
    now = datetime(2026, 11, 2, tzinfo=UTC)
    unit = parse_local(LOCAL_FIXTURE, now)
    assert unit.id == "82000B02"
    assert unit.source is Source.LOCAL
    assert unit.online is True
    assert unit.last_seen == now
    assert unit.readings["ch2o"].value == pytest.approx(12)
    assert unit.readings["pressure"].value == pytest.approx(1013.55)
    assert unit.readings[DOSE_KEY].value == pytest.approx(0.15)
    assert unit.readings[UPTIME_KEY].value == 3700
    assert "type" not in unit.readings
    assert AQI_KEY not in unit.readings


def test_local_top_level() -> None:
    """Fields at the top level and numbers as strings."""
    unit = parse_local('{"id":"1d000001","radon":84,"temperature":"18.66"}')
    assert unit.id == "1D000001"
    assert unit.readings["radon"].value == 84
    assert unit.readings["radon"].unit == "Bq/m³"
    assert unit.readings["temperature"].value == pytest.approx(18.66)
    assert DOSE_KEY not in unit.readings


@pytest.mark.parametrize("reply", ['{"data":{"temperature":20}}', "not json", "[1]"])
def test_local_invalid(reply: str) -> None:
    """A reply without a unit ID is an error."""
    with pytest.raises(URadError):
        parse_local(reply)


def test_merge_prefers_local() -> None:
    """Local readings win; the cloud fills in the rest."""
    cloud = parse_cloud_devices(CLOUD_FIXTURE)[1]
    merged = merge(cloud, parse_local(LOCAL_FIXTURE))
    assert merged is not None
    assert merged.source is Source.LOCAL
    assert merged.readings["temperature"].value == 21.5
    assert merged.readings["co2"].value == 577
    assert merged.readings[AQI_KEY].value == 4
    assert merged.readings[DOSE_KEY].value == pytest.approx(0.15)
    assert list(merged.readings).count(DOSE_KEY) == 1
    assert merged.version_sw == 124
    assert cloud.readings["temperature"].value == pytest.approx(20.04)


def test_merge_staleness() -> None:
    """Fresh local wins, stale local falls back to the cloud or shows offline."""
    now = datetime(2026, 11, 2, tzinfo=UTC)
    cloud = {u.id: u for u in parse_cloud_devices(CLOUD_FIXTURE)}
    endpoint = LocalEndpoint("10.0.0.5")
    local = {endpoint.key: parse_local(LOCAL_FIXTURE)}
    fresh = {endpoint.key: LocalStatus(endpoint, last_success=now - timedelta(seconds=30))}
    stale = {endpoint.key: LocalStatus(endpoint, last_success=now - timedelta(minutes=10))}
    window = timedelta(minutes=3)

    merged = merge_units(cloud, True, local, fresh, window, now)
    assert list(merged) == ["11000A01", "82000B02"]
    assert merged["82000B02"].source is Source.LOCAL

    merged = merge_units(cloud, True, local, stale, window, now)
    assert merged["82000B02"].source is Source.CLOUD

    merged = merge_units({}, True, local, stale, window, now)
    assert merged["82000B02"].online is False

    merged = merge_units(cloud, False, {}, {}, window, now)
    assert merged["82000B02"].online is False
    assert cloud["82000B02"].online is True


def test_helpers() -> None:
    """Addresses, factors and numbers."""
    assert base_url("10.0.0.5", 80) == "http://10.0.0.5"
    assert base_url("fe80::1", 8080) == "http://[fe80::1]:8080"
    assert factor_for("unknown-tube") == 0.01
    assert factor_for(" sbm20 ") == 0.006315
    assert number(True) is None
    assert number("nan") is None
    assert number(" 23.09 ") == 23.09
