"""uRADMonitor cloud and local JSON formats.

Maps the cloud ``GET /api/v1/devices`` and a unit's local ``GET /j`` replies to
``UnitSnapshot``. Position fields are dropped while parsing and never stored.
See https://www.uradmonitor.com/open-data/.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
import json
import math
from typing import Any

DOSE_KEY = "dose"
AQI_KEY = "aqi"
UPTIME_KEY = "uptime"
CPM_KEY = "cpm"

# Used for the dose when neither the cloud nor DETECTOR_FACTORS knows the tube.
DEFAULT_FACTOR = 0.01

# CPM → µSv/h conversion factors uRADMonitor reports for its tubes.
DETECTOR_FACTORS: dict[str, float] = {
    "SBM20": 0.006315,
    "SI29BG": 0.01,
}

# European AQI bands 1–6, as Home Assistant enum options.
AQI_BANDS = ("good", "fair", "moderate", "poor", "very_poor", "extremely_poor")

# Unit metadata and position — never readings.
_NON_READING_KEYS = frozenset(
    {
        "id",
        "type",
        "detector",
        "detector2",
        "factor",
        "factor2",
        "hw",
        "sw",
        "versionhw",
        "versionsw",
        "latitude",
        "longitude",
        "altitude",
        "speed",
        "lat",
        "lon",
        "lng",
        "gps",
        "city",
        "country",
        "placement",
        "mobile",
        "status",
        "errors",
        "faulty",
        "note",
        "picture",
        "owner",
        "timefirst",
        "timelast",
        "timelocal",
        "aqi",
    }
)


class Source(StrEnum):
    """Where a unit's readings came from."""

    NONE = "none"
    CLOUD = "cloud"
    LOCAL = "local"


class URadError(Exception):
    """A uRADMonitor reply or connection problem, with a readable message."""


class URadAuthError(URadError):
    """The cloud rejected the User ID or User Key."""


class URadConnectionError(URadError):
    """The cloud or a unit couldn't be reached or didn't answer in time."""


@dataclass(frozen=True, slots=True)
class SensorDefinition:
    """A known sensor: its key, display name, unit and scale from each source."""

    key: str
    name: str
    unit: str | None
    cloud_scale: float = 1
    local_scale: float = 1


@dataclass(frozen=True, slots=True)
class Reading:
    """One sensor value in normalized units."""

    key: str
    name: str
    unit: str | None
    value: float


@dataclass(slots=True)
class UnitSnapshot:
    """One unit's latest readings, keyed by sensor key in display order."""

    id: str
    source: Source
    online: bool
    last_seen: datetime | None = None
    detector: str | None = None
    factor: float | None = None
    aqi: int | None = None
    version_hw: int | None = None
    version_sw: int | None = None
    readings: dict[str, Reading] = field(default_factory=dict)

    def copy(self) -> UnitSnapshot:
        """Return a copy whose readings can be changed independently."""
        return replace(self, readings=dict(self.readings))


# Known order and display of sensors; anything else keeps its own name and unit.
SENSORS: tuple[SensorDefinition, ...] = (
    SensorDefinition(DOSE_KEY, "Radiation dose", "µSv/h"),
    SensorDefinition(CPM_KEY, "Radiation", "CPM"),
    SensorDefinition(AQI_KEY, "Air quality index", None),
    SensorDefinition("temperature", "Temperature", "°C"),
    SensorDefinition("humidity", "Humidity", "%"),
    SensorDefinition("pressure", "Pressure", "hPa", 0.01, 0.01),
    SensorDefinition("co2", "Carbon dioxide", "ppm"),
    SensorDefinition("ch2o", "Formaldehyde", "ppb", 1, 1000),
    SensorDefinition("voc", "VOC resistance", "Ω"),
    SensorDefinition("vocaqi", "VOC index", None),
    SensorDefinition("o3", "Ozone", "ppb"),
    SensorDefinition("pm1", "PM1.0", "µg/m³"),
    SensorDefinition("pm25", "PM2.5", "µg/m³"),
    SensorDefinition("pm10", "PM10", "µg/m³"),
    SensorDefinition("noise", "Noise", "dB"),
    SensorDefinition("radon", "Radon", "Bq/m³"),
    SensorDefinition("voltage", "Tube voltage", "V"),
    SensorDefinition("duty", "Duty cycle", "‰"),
    SensorDefinition("signal", "Signal", "dBm"),
    SensorDefinition(UPTIME_KEY, "Uptime", "s"),
)

_DEFINITIONS = {sensor.key: sensor for sensor in SENSORS}
_SORT_INDEX = {sensor.key: index for index, sensor in enumerate(SENSORS)}


def definition(key: str) -> SensorDefinition | None:
    """Return the known sensor for a key."""
    return _DEFINITIONS.get(key.lower())


def sort_index(key: str) -> int:
    """Known sensors first, in catalog order."""
    return _SORT_INDEX.get(key.lower(), len(SENSORS))


def parse_cloud_devices(text: str) -> list[UnitSnapshot]:
    """Parse ``GET /devices``; the server's ``{"error": …}`` raises URadError."""
    token = _parse_json(text)
    if isinstance(token, dict):
        error = token.get("error")
        if error:
            raise _cloud_error(str(error))
        raise URadError("The uRADMonitor server sent an unexpected reply.")
    if not isinstance(token, list):
        raise URadError("The uRADMonitor server sent an unexpected reply.")
    units = (parse_cloud_unit(item) for item in token if isinstance(item, dict))
    return [unit for unit in units if unit is not None]


def parse_cloud_unit(item: dict[str, Any]) -> UnitSnapshot | None:
    """Parse one unit of ``GET /devices``."""
    unit_id = normalize_id(_text(item.get("id")))
    if unit_id is None:
        return None

    unit = UnitSnapshot(
        id=unit_id,
        source=Source.CLOUD,
        online=number(item.get("status")) == 1,
        detector=_text(item.get("detector")),
        factor=_positive(number(item.get("factor"))),
        aqi=_aqi_band(number(item.get("aqi"))),
        version_hw=_int(item.get("versionhw")),
        version_sw=_int(item.get("versionsw")),
    )
    last = number(item.get("timelast"))
    if last is not None and last > 0:
        unit.last_seen = datetime.fromtimestamp(int(last), UTC)

    readings: list[Reading] = []
    for name, value in item.items():
        if not name.lower().startswith("last_"):
            continue
        key = name[5:].lower()
        if not key or key in _NON_READING_KEYS:
            continue
        raw = number(value)
        if raw is None:
            continue
        param = item.get(f"param_{key}")
        param = param if isinstance(param, list) else []
        readings.append(
            _reading(
                key,
                raw,
                Source.CLOUD,
                _text(param[0]) if len(param) > 0 else None,
                _text(param[1]) if len(param) > 1 else None,
            )
        )
    uptime = number(item.get("timelocal"))
    if unit.online and uptime is not None:
        readings.append(_reading(UPTIME_KEY, uptime, Source.CLOUD, None, None))
    return _complete(unit, readings)


def parse_local(text: str, now: datetime | None = None) -> UnitSnapshot:
    """Parse a unit's local ``/j``: ``{"data": {…}}`` or the same fields at the top level."""
    root = _parse_json(text)
    if not isinstance(root, dict):
        raise URadError("That address didn't return uRADMonitor JSON.")
    data = root.get("data")
    if not isinstance(data, dict):
        data = root
    unit_id = normalize_id(_text(data.get("id")) or _text(root.get("id")))
    if unit_id is None:
        raise URadError("That address returned JSON without a uRADMonitor unit ID.")

    unit = UnitSnapshot(
        id=unit_id,
        source=Source.LOCAL,
        online=True,
        last_seen=now or datetime.now(UTC),
        detector=_text(data.get("detector")),
    )
    readings: list[Reading] = []
    for name, value in data.items():
        key = name.lower()
        if key in _NON_READING_KEYS:
            continue
        raw = number(value)
        if raw is None:
            continue
        readings.append(_reading(key, raw, Source.LOCAL, None, None))
    return _complete(unit, readings)


def merge(cloud: UnitSnapshot | None, local: UnitSnapshot | None) -> UnitSnapshot | None:
    """One view of a unit.

    Local readings win while the unit answers locally; the cloud fills in sensors
    and details the local JSON lacks (AQI, tube factor, firmware).
    """
    if local is None:
        return cloud.copy() if cloud else None
    if cloud is None:
        return local.copy()

    merged = local.copy()
    merged.factor = cloud.factor or merged.factor
    merged.aqi = cloud.aqi
    merged.version_hw = cloud.version_hw if cloud.version_hw is not None else merged.version_hw
    merged.version_sw = cloud.version_sw if cloud.version_sw is not None else merged.version_sw
    merged.detector = merged.detector or cloud.detector
    readings = list(merged.readings.values())
    readings.extend(
        reading
        for key, reading in cloud.readings.items()
        if key not in (DOSE_KEY, AQI_KEY) and key not in merged.readings
    )
    return _complete(merged, readings)


def factor_for(detector: str | None) -> float:
    """The CPM → µSv/h factor for a tube, or the default."""
    if detector:
        return DETECTOR_FACTORS.get(detector.strip().upper(), DEFAULT_FACTOR)
    return DEFAULT_FACTOR


def normalize_id(unit_id: str | None) -> str | None:
    """Unit IDs are upper-case hex."""
    unit_id = (unit_id or "").strip()
    return unit_id.upper() or None


def base_url(host: str, port: int) -> str:
    """The unit's web page, e.g. ``http://10.0.0.5`` or ``http://[fe80::1]:8080``."""
    host = host.strip()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}" if port == 80 else f"http://{host}:{port}"


def number(value: Any) -> float | None:
    """uRADMonitor sends numbers both as JSON numbers and as strings ("23.09")."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return None if math.isnan(result) or math.isinf(result) else result


def _complete(unit: UnitSnapshot, readings: list[Reading]) -> UnitSnapshot:
    """Add the derived dose and AQI readings and order the readings for display."""
    readings = [r for r in readings if r.key not in (DOSE_KEY, AQI_KEY)]
    cpm = next((r for r in readings if r.key == CPM_KEY), None)
    if cpm is not None:
        factor = unit.factor or factor_for(unit.detector)
        readings.append(_reading(DOSE_KEY, cpm.value * factor, Source.NONE, None, None))
    if unit.aqi is not None:
        readings.append(_reading(AQI_KEY, unit.aqi, Source.NONE, None, None))
    readings.sort(key=lambda r: (sort_index(r.key), r.name.lower()))
    unit.readings = {r.key: r for r in readings}
    return unit


def _reading(key: str, raw: float, source: Source, name: str | None, unit: str | None) -> Reading:
    known = definition(key)
    if known is not None:
        scale = {Source.LOCAL: known.local_scale, Source.CLOUD: known.cloud_scale}.get(source, 1)
        return Reading(known.key, known.name, known.unit, raw * scale)
    return Reading(key, name or _title_case(key), unit or None, raw)


def _cloud_error(error: str) -> URadError:
    lowered = error.lower()
    if "authentif" in lowered or "authentic" in lowered or "validation" in lowered:
        return URadAuthError(
            "uRADMonitor rejected the User ID or User Key. Copy the API user-id and "
            "API key again from your profile's API Credentials "
            "(uradmonitor.com/wp-admin/profile.php)."
        )
    return URadError(f"uRADMonitor replied: {error}")


def _parse_json(text: str) -> Any:
    if not text or not text.strip():
        raise URadError("Empty reply.")
    try:
        return json.loads(text)
    except ValueError as err:
        raise URadError("The reply wasn't JSON.") from err


def _aqi_band(value: float | None) -> int | None:
    if value is None:
        return None
    band = round(value)
    return band if 1 <= band <= len(AQI_BANDS) else None


def _title_case(key: str) -> str:
    return " ".join(word[0].upper() + word[1:] for word in key.replace("_", " ").split())


def _positive(value: float | None) -> float | None:
    return value if value is not None and value > 0 else None


def _int(value: Any) -> int | None:
    result = number(value)
    return int(result) if result is not None else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
