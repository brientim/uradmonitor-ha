"""The tolerant local HTTP reader, addresses and the cloud request."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
import socket

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.uradmonitor.api import (
    LocalEndpoint,
    URadCloudClient,
    URadLocalClient,
    parse_endpoint,
    parse_endpoints,
)
from custom_components.uradmonitor.protocol import (
    DOSE_KEY,
    URadAuthError,
    URadConnectionError,
    URadError,
)

from .conftest import CLOUD_FIXTURE, CLOUD_URL, LIVE_A3_REPLY, LOCAL_FIXTURE


@asynccontextmanager
async def one_shot_server(reply: bytes) -> AsyncGenerator[tuple[LocalEndpoint, list[bytes]]]:
    """Accept connections, record the request, write a canned reply and close."""
    requests: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        requests.append(await reader.read(4096))
        writer.write(reply)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        yield LocalEndpoint("127.0.0.1", port), requests


def _with_body(template: str) -> bytes:
    body = LOCAL_FIXTURE.encode()
    return template.replace("{len}", str(len(body))).encode().replace(b"{body}", body)


@pytest.mark.parametrize(
    "template",
    [
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len}\r\n\r\n{body}",
        "HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{body}",
        "HTTP/1.0 200 OK\nContent-Length: {len}\n\n{body}",
        "{body}",
    ],
    ids=["content-length", "no-content-length", "bare-lf", "no-status-line"],
)
@pytest.mark.usefixtures("socket_enabled")
async def test_local_http_variants(template: str) -> None:
    """Every reply shape the units have been seen to send."""
    async with one_shot_server(_with_body(template)) as (endpoint, requests):
        unit = await URadLocalClient().get(endpoint)
    assert unit.id == "82000B02"
    assert requests[0].startswith(b"GET /j HTTP/1.0\r\n")


@pytest.mark.usefixtures("socket_enabled")
async def test_local_live_a3() -> None:
    """A model-8 reply with every sensor."""
    async with one_shot_server(LIVE_A3_REPLY.encode()) as (endpoint, _):
        unit = await URadLocalClient().get(endpoint)
    assert unit.id == "8200FF01"
    assert len(unit.readings) == 12
    assert unit.readings["ch2o"].value == pytest.approx(30)
    assert unit.readings["pressure"].value == pytest.approx(1008.14)
    assert unit.readings[DOSE_KEY].value == pytest.approx(0.15)
    assert unit.readings["noise"].value == 71


@pytest.mark.usefixtures("socket_enabled")
async def test_local_http_not_found() -> None:
    """Non-2xx replies are errors."""
    async with one_shot_server(b"HTTP/1.0 404 Not Found\r\n\r\nnope") as (endpoint, _):
        with pytest.raises(URadError, match="HTTP 404"):
            await URadLocalClient().get(endpoint)


@pytest.mark.usefixtures("socket_enabled")
async def test_local_refused() -> None:
    """A closed port is a connection error."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(URadConnectionError, match="Can't reach"):
        await URadLocalClient().get(LocalEndpoint("127.0.0.1", port))


@pytest.mark.usefixtures("socket_enabled")
async def test_local_timeout() -> None:
    """A unit that never answers times out."""

    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(5)

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    async with server:
        endpoint = LocalEndpoint("127.0.0.1", server.sockets[0].getsockname()[1])
        with pytest.raises(URadConnectionError, match="didn't answer"):
            await URadLocalClient(timeout=0.2).get(endpoint)


@pytest.mark.parametrize(
    ("text", "host", "port", "shown"),
    [
        ("10.0.0.5", "10.0.0.5", 80, "10.0.0.5"),
        (" 10.0.0.5:8080 ", "10.0.0.5", 8080, "10.0.0.5:8080"),
        ("http://unit.local/j", "unit.local", 80, "unit.local"),
        ("fe80::1", "fe80::1", 80, "[fe80::1]"),
        ("[fe80::1]:8080", "fe80::1", 8080, "[fe80::1]:8080"),
    ],
)
def test_parse_endpoint(text: str, host: str, port: int, shown: str) -> None:
    """Addresses with and without ports, including IPv6."""
    endpoint = parse_endpoint(text)
    assert (endpoint.host, endpoint.port, str(endpoint)) == (host, port, shown)
    assert parse_endpoint(str(endpoint)) == endpoint


@pytest.mark.parametrize("text", ["", "10.0.0.5:0", "10.0.0.5:99999", "10.0.0.5:abc", ":80"])
def test_parse_endpoint_invalid(text: str) -> None:
    """Blank addresses and bad ports."""
    with pytest.raises(ValueError):
        parse_endpoint(text)


def test_parse_endpoints_dedupes() -> None:
    """Duplicates and blanks are dropped."""
    endpoints = parse_endpoints([" 10.0.0.5 ", "10.0.0.5:80", "", "Unit.local", "unit.local"])
    assert [str(e) for e in endpoints] == ["10.0.0.5", "Unit.local"]


async def test_cloud_headers(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Credentials go in the headers, trimmed."""
    aioclient_mock.get(CLOUD_URL, text=CLOUD_FIXTURE)
    units = await URadCloudClient(async_get_clientsession(hass)).get_devices(" 1234 ", "abcdef")
    assert [u.id for u in units] == ["11000A01", "82000B02"]
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert str(url) == CLOUD_URL
    assert headers["X-User-id"] == "1234"
    assert headers["X-User-hash"] == "abcdef"


@pytest.mark.parametrize(
    ("mock", "error", "match"),
    [
        (
            lambda m: m.get(CLOUD_URL, text='{"error":"Authentification failed"}'),
            URadAuthError,
            "rejected",
        ),
        (lambda m: m.get(CLOUD_URL, status=502, text="Bad gateway"), URadError, "HTTP 502"),
        (lambda m: m.get(CLOUD_URL, exc=TimeoutError()), URadConnectionError, "didn't answer"),
    ],
    ids=["auth", "http-502", "timeout"],
)
async def test_cloud_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock: Callable[[AiohttpClientMocker], None],
    error: type[Exception],
    match: str,
) -> None:
    """Server errors become readable messages."""
    mock(aioclient_mock)
    with pytest.raises(error, match=match):
        await URadCloudClient(async_get_clientsession(hass)).get_devices("1", "x")


async def test_cloud_needs_credentials(hass: HomeAssistant) -> None:
    """No request without a User ID and User Key."""
    with pytest.raises(URadAuthError, match="Enter the User ID"):
        await URadCloudClient(async_get_clientsession(hass)).get_devices("", "x")
