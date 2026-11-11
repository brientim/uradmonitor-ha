"""uRADMonitor cloud API and local unit clients."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import re

import aiohttp

from .protocol import (
    UnitSnapshot,
    URadAuthError,
    URadConnectionError,
    URadError,
    parse_cloud_devices,
    parse_local,
)

CLOUD_BASE_URL = "https://data.uradmonitor.com/api/v1/"
CLOUD_TIMEOUT = 20
LOCAL_TIMEOUT = 8
USER_AGENT = "HomeAssistant-uRADMonitor/1.0"
_MAX_RESPONSE_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class LocalEndpoint:
    """A unit's address on the network."""

    host: str
    port: int = 80

    @property
    def key(self) -> str:
        """Identity for de-duplication: host is case-insensitive."""
        return f"{self.host.lower()}:{self.port}"

    def __str__(self) -> str:
        """Host, or host:port when not 80 (IPv6 in brackets)."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port == 80 else f"{host}:{self.port}"


_BRACKETED = re.compile(r"^\[(?P<host>[^\]]+)\](?::(?P<port>\d+))?$")


def parse_endpoint(text: str) -> LocalEndpoint:
    """Parse ``host``, ``host:port``, ``[ipv6]:port`` or a bare IPv6 address."""
    text = text.strip()
    for prefix in ("http://", "https://"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
    text = text.split("/", 1)[0]
    if not text:
        raise ValueError("Enter the unit's IP address or host name.")

    if match := _BRACKETED.match(text):
        host, port_text = match["host"], match["port"]
    elif text.count(":") == 1:
        host, port_text = text.split(":")
    else:
        host, port_text = text, None

    port = 80
    if port_text:
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            raise ValueError(f"Port for {host} must be 1–65535.")
        port = int(port_text)
    if not host.strip():
        raise ValueError("Enter the unit's IP address or host name.")
    return LocalEndpoint(host.strip(), port)


def parse_endpoints(lines: list[str]) -> list[LocalEndpoint]:
    """Parse and de-duplicate addresses, dropping blanks."""
    endpoints: dict[str, LocalEndpoint] = {}
    for line in lines:
        if not line.strip():
            continue
        endpoint = parse_endpoint(line)
        endpoints.setdefault(endpoint.key, endpoint)
    return list(endpoints.values())


class URadCloudClient:
    """The account's units from ``GET https://data.uradmonitor.com/api/v1/devices``."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str = CLOUD_BASE_URL) -> None:
        """Use Home Assistant's shared session."""
        self._session = session
        self._base_url = base_url if base_url.endswith("/") else f"{base_url}/"

    async def get_devices(self, user_id: str, user_key: str) -> list[UnitSnapshot]:
        """Every unit the account owns or was given access to."""
        user_id, user_key = (user_id or "").strip(), (user_key or "").strip()
        if not user_id or not user_key:
            raise URadAuthError(
                "Enter the User ID (API user-id) and User Key (API key) from your "
                "uRADMonitor profile's API Credentials."
            )
        headers = {
            "X-User-id": user_id,
            "X-User-hash": user_key,
            "User-Agent": USER_AGENT,
        }
        try:
            async with (
                asyncio.timeout(CLOUD_TIMEOUT),
                self._session.get(f"{self._base_url}devices", headers=headers) as response,
            ):
                body = await response.text()
        except TimeoutError as err:
            raise URadConnectionError(
                f"The uRADMonitor server didn't answer within {CLOUD_TIMEOUT} seconds."
            ) from err
        except aiohttp.ClientError as err:
            raise URadConnectionError(
                "Can't reach data.uradmonitor.com. Check Home Assistant's internet connection."
            ) from err

        # Errors normally arrive as {"error": …}, which parse_cloud_devices turns into a message.
        if response.status >= 400 and '"error"' not in body:
            raise URadError(f"The uRADMonitor server replied HTTP {response.status}.")
        return parse_cloud_devices(body)


class URadLocalClient:
    """A unit's local ``GET /j``.

    The units' embedded web server isn't always strictly HTTP-compliant, so this
    reads a plain HTTP/1.0 exchange over a socket and tolerates missing or
    partial headers.
    """

    def __init__(self, timeout: float = LOCAL_TIMEOUT) -> None:
        """Set the per-request timeout in seconds."""
        self._timeout = timeout

    async def get(self, endpoint: LocalEndpoint) -> UnitSnapshot:
        """The unit's current readings."""
        return parse_local(await self.fetch(endpoint, "/j"))

    async def fetch(self, endpoint: LocalEndpoint, path: str) -> str:
        """The body of ``GET path`` from the unit."""
        try:
            async with asyncio.timeout(self._timeout):
                return parse_response(await self._exchange(endpoint, path))
        except TimeoutError as err:
            raise URadConnectionError(
                f"The unit at {endpoint} didn't answer within {self._timeout:g} seconds."
            ) from err
        except OSError as err:
            raise URadConnectionError(
                f"Can't reach a uRADMonitor unit at {endpoint} ({err.strerror or err})."
            ) from err

    async def _exchange(self, endpoint: LocalEndpoint, path: str) -> bytes:
        reader, writer = await asyncio.open_connection(endpoint.host, endpoint.port)
        try:
            host = f"[{endpoint.host}]" if ":" in endpoint.host else endpoint.host
            writer.write(
                f"GET {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n"
                f"User-Agent: {USER_AGENT}\r\nAccept: */*\r\n\r\n".encode("ascii")
            )
            await writer.drain()

            buffer = bytearray()
            body_start = content_length = -1
            while True:
                try:
                    chunk = await reader.read(4096)
                except OSError:
                    if buffer:
                        break
                    raise
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > _MAX_RESPONSE_BYTES:
                    raise URadError("The unit's reply was unexpectedly large.")
                if body_start < 0:
                    body_start = _header_end(buffer)
                    if body_start >= 0:
                        content_length = _content_length(buffer, body_start)
                if body_start >= 0 and 0 <= content_length <= len(buffer) - body_start:
                    break
            return bytes(buffer)
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()


def parse_response(response: bytes) -> str:
    """The body of a raw HTTP response; a reply with no status line is all body."""
    if not response:
        raise URadError("The unit closed the connection without replying.")
    text = response.decode("utf-8", errors="replace")
    if text[:5].upper() != "HTTP/":
        return text.strip()

    end = _header_end(response)
    header_text = response[:end].decode("ascii", errors="replace") if end >= 0 else text
    status_line = header_text.replace("\r\n", "\n").split("\n", 1)[0]
    parts = status_line.split(None, 2)
    if len(parts) < 2 or not parts[1].startswith("2"):
        status = f"HTTP {parts[1]}" if len(parts) >= 2 else "with an unreadable status"
        raise URadError(f"The unit replied {status} for /j.")
    if end < 0:
        raise URadError("The unit's reply had no data.")

    length = _content_length(response, end)
    available = len(response) - end
    count = length if 0 <= length < available else available
    return response[end : end + count].decode("utf-8", errors="replace").strip()


def _header_end(data: bytes | bytearray) -> int:
    """Index just past the blank line ending the headers (CRLF or bare LF), or -1."""
    ends = [
        index + len(separator)
        for separator in (b"\r\n\r\n", b"\n\n")
        if (index := data.find(separator)) >= 0
    ]
    return min(ends) if ends else -1


def _content_length(data: bytes | bytearray, header_end: int) -> int:
    headers = data[:header_end].decode("ascii", errors="replace").replace("\r\n", "\n")
    for line in headers.split("\n"):
        name, colon, value = line.partition(":")
        if colon and name.strip().lower() == "content-length":
            value = value.strip()
            if value.isdigit():
                return int(value)
    return -1
