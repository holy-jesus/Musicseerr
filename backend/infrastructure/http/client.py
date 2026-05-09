import os
import httpx
import logging
from typing import Optional
from urllib.request import getproxies
from contextlib import contextmanager
import ipaddress
from core.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _get_user_agent(settings: Optional[Settings] = None) -> str:
    if settings:
        return settings.get_user_agent()
    return get_settings().get_user_agent()


# Taken from httpx: https://github.com/encode/httpx/blob/master/httpx/_utils.py
def is_ipv4_hostname(hostname: str) -> bool:
    try:
        ipaddress.IPv4Address(hostname.split("/")[0])
    except Exception:
        return False
    return True


def is_ipv6_hostname(hostname: str) -> bool:
    try:
        ipaddress.IPv6Address(hostname.split("/")[0])
    except Exception:
        return False
    return True


def _get_proxy_map():
    proxy_info = getproxies()

    if not proxy_info:
        return {}

    mounts: dict[str, str | None] = {}

    socksio_installed = True
    try:
        import socksio
    except ImportError:
        socksio_installed = False

    for scheme in ("http", "https", "all"):
        hostname = proxy_info.get(scheme)

        if not hostname:
            continue

        if hostname.startswith("socks://"):
            if not socksio_installed:
                logger.warning("socksio not installed, `socks://` proxy not supported")
                continue

            logger.warning("httpx doesn't support `socks://`, replacing with `socks5://`.")
            hostname = hostname.replace("socks://", "socks5://")
        mounts[f"{scheme}://"] = hostname if "://" in hostname else f"http://{hostname}"

    if not mounts:
        logger.warning("No suitable proxy protocols found.")

    no_proxy_hosts = [host.strip() for host in proxy_info.get("no", "").split(",")]
    for hostname in no_proxy_hosts:
        if hostname == "*":
            return {}
        elif hostname:
            if "://" in hostname:
                mounts[hostname] = None
            elif is_ipv4_hostname(hostname):
                mounts[f"all://{hostname}"] = None
            elif is_ipv6_hostname(hostname):
                mounts[f"all://[{hostname}]"] = None
            elif hostname.lower() == "localhost":
                mounts[f"all://{hostname}"] = None
            else:
                mounts[f"all://*{hostname}"] = None

    return mounts


# ====


def _get_mounts(http2: bool = True) -> dict[str, httpx.AsyncHTTPTransport | None]:
    mounts = {}
    for key, proxy in _get_proxy_map().items():
        mounts[key] = (
            httpx.AsyncHTTPTransport(
                proxy=proxy,
                http2=http2,
                retries=0,
            )
            if proxy
            else None
        )
    return mounts


class HttpClientFactory:
    _clients: dict[str, httpx.AsyncClient] = {}

    @classmethod
    def get_client(
        cls,
        name: str = "default",
        timeout: float = 10.0,
        connect_timeout: float = 5.0,
        max_connections: int = 200,
        max_keepalive: int = 200,
        settings: Optional[Settings] = None,
        http2: bool = True,
        **kwargs,
    ) -> httpx.AsyncClient:
        if name not in cls._clients:
            cls._clients[name] = httpx.AsyncClient(
                http2=http2,
                timeout=httpx.Timeout(timeout, connect=connect_timeout),
                limits=httpx.Limits(
                    max_connections=max_connections,
                    max_keepalive_connections=max_keepalive,
                    keepalive_expiry=60.0,
                ),
                follow_redirects=True,
                transport=httpx.AsyncHTTPTransport(http2=http2, retries=0),
                headers={"User-Agent": _get_user_agent(settings)},
                mounts=_get_mounts(http2),
                **kwargs,
            )
        return cls._clients[name]

    @classmethod
    async def close_all(cls) -> None:
        for client in cls._clients.values():
            await client.aclose()
        cls._clients.clear()


def get_http_client(
    settings: Optional[Settings] = None,
    timeout: Optional[float] = None,
    connect_timeout: Optional[float] = None,
    max_connections: Optional[int] = None,
) -> httpx.AsyncClient:
    if settings is None:
        settings = get_settings()
    return HttpClientFactory.get_client(
        name="default",
        timeout=timeout or settings.http_timeout,
        connect_timeout=connect_timeout or settings.http_connect_timeout,
        max_connections=max_connections or settings.http_max_connections,
        max_keepalive=settings.http_max_keepalive,
        settings=settings,
    )


async def close_http_clients() -> None:
    await HttpClientFactory.close_all()


def get_listenbrainz_http_client(
    settings: Optional[Settings] = None,
    timeout: Optional[float] = None,
    connect_timeout: Optional[float] = None,
) -> httpx.AsyncClient:
    if settings is None:
        settings = get_settings()
    return HttpClientFactory.get_client(
        name="listenbrainz",
        timeout=timeout or settings.http_timeout,
        connect_timeout=connect_timeout or settings.http_connect_timeout,
        max_connections=20,
        max_keepalive=20,
        settings=settings,
        http2=False,
    )
