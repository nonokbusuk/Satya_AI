import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests
import markdownify
from bs4 import BeautifulSoup
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import NewConnectionError
from urllib3.poolmanager import PoolManager, key_fn_by_scheme, pool_classes_by_scheme
from urllib3.util import connection as urllib3_connection

from . import storage
from .git_handler import GitHandler

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
REDIRECT_LIMIT = 5


class _PinnedHTTPConnection(HTTPConnection):
    """HTTP connection that connects to a pre-validated IP, not a fresh DNS lookup."""

    def __init__(self, *args, pinned_ip=None, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def _new_conn(self):
        if not self._pinned_ip:
            return super()._new_conn()
        try:
            return urllib3_connection.create_connection(
                (self._pinned_ip, self.port),
                self.timeout,
                source_address=self.source_address,
                socket_options=self.socket_options,
            )
        except OSError as exc:
            raise NewConnectionError(
                self, f"Failed to connect to validated address {self._pinned_ip}: {exc}"
            ) from exc


class _PinnedHTTPSConnection(HTTPSConnection):
    """HTTPS keeps the original hostname for SNI and certificate verification."""

    def __init__(self, *args, pinned_ip=None, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def _new_conn(self):
        if not self._pinned_ip:
            return super()._new_conn()
        try:
            return urllib3_connection.create_connection(
                (self._pinned_ip, self.port),
                self.timeout,
                source_address=self.source_address,
                socket_options=self.socket_options,
            )
        except OSError as exc:
            raise NewConnectionError(
                self, f"Failed to connect to validated address {self._pinned_ip}: {exc}"
            ) from exc


class _PinnedHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _PinnedHTTPConnection

    def __init__(self, *args, pinned_ip=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.conn_kw["pinned_ip"] = pinned_ip


class _PinnedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _PinnedHTTPSConnection

    def __init__(self, *args, pinned_ip=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.conn_kw["pinned_ip"] = pinned_ip


class _PinnedPoolManager(PoolManager):
    """A one-address pool; custom keying excludes the transport-only pinned_ip value."""

    pool_classes_by_scheme = dict(pool_classes_by_scheme)
    pool_classes_by_scheme["http"] = _PinnedHTTPConnectionPool
    pool_classes_by_scheme["https"] = _PinnedHTTPSConnectionPool

    key_fn_by_scheme = dict(key_fn_by_scheme)

    def __init__(self, *args, pinned_ip=None, **kwargs):
        super().__init__(*args, **kwargs)
        # PoolManager.__init__ assigns the module-level registries on some urllib3
        # versions, so explicitly copy our subclass registries before overriding.
        # This avoids mutating urllib3's global registries and keeps pinned pools active.
        self.pool_classes_by_scheme = dict(type(self).pool_classes_by_scheme)
        self.key_fn_by_scheme = dict(type(self).key_fn_by_scheme)
        self.connection_pool_kw["pinned_ip"] = pinned_ip
        for scheme, base_key_fn in tuple(self.key_fn_by_scheme.items()):
            def key_fn(context, _base=base_key_fn):
                clean_context = dict(context)
                clean_context.pop("pinned_ip", None)
                return _base(clean_context)
            self.key_fn_by_scheme[scheme] = key_fn


class _PinnedHTTPAdapter(requests.adapters.HTTPAdapter):
    def __init__(self, pinned_ip, *args, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        self.poolmanager = _PinnedPoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            pinned_ip=self._pinned_ip,
            **pool_kwargs,
        )


def _resolve_safe_ip(url: str):
    """Resolve once and pin the request to a globally routable address.

    All DNS answers must be public. The HTTP transport connects directly to the
    selected IP while retaining the original hostname for Host/SNI/certificate checks.
    This closes the validation-to-connection DNS-rebinding gap.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = socket.getaddrinfo(
            parsed.hostname, port, type=socket.SOCK_STREAM
        )
        ips = []
        for result in addresses:
            ip = ipaddress.ip_address(result[4][0])
            if not ip.is_global:
                return None
            if ip.compressed not in ips:
                ips.append(ip.compressed)
        return ips[0] if ips else None
    except (OSError, ValueError, IndexError, TypeError):
        return None


def _is_safe_url(url: str) -> bool:
    """Return whether the URL resolves exclusively to globally routable IPs."""
    return _resolve_safe_ip(url) is not None


def _fetch_once_pinned(url: str, pinned_ip: str, max_bytes: int = MAX_RESPONSE_BYTES):
    """Fetch one hop with DNS pinned, verified TLS, no environment proxies, and a size cap."""
    parsed = urlparse(url)
    session = requests.Session()
    session.trust_env = False
    session.mount(
        f"{parsed.scheme}://{parsed.netloc}",
        _PinnedHTTPAdapter(pinned_ip),
    )
    response = None
    try:
        response = session.get(
            url,
            timeout=(3, 10),
            allow_redirects=False,
            stream=True,
        )
        headers = dict(response.headers)
        if 300 <= response.status_code < 400:
            return response.status_code, headers, ""

        response.raise_for_status()
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise ValueError("Scraped response exceeds the 5 MiB limit.")
            except ValueError as exc:
                if str(exc).startswith("Scraped response"):
                    raise
                raise ValueError("Invalid Content-Length header.") from exc

        parts = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Scraped response exceeds the 5 MiB limit.")
            parts.append(chunk)
        encoding = response.encoding or "utf-8"
        return response.status_code, headers, b"".join(parts).decode(
            encoding, errors="replace"
        )
    finally:
        if response is not None:
            response.close()
        session.close()


class Scraper:
    def __init__(self, repo_path="."):
        self.repo_path = repo_path
        self.git_handler = GitHandler(repo_path)
        storage.ensure_satya_dirs()

    def fetch_and_save(self, url, title=None):
        current_url = url
        last_status = None
        headers = {}
        page_text = ""

        try:
            for _ in range(REDIRECT_LIMIT):
                pinned_ip = _resolve_safe_ip(current_url)
                if not pinned_ip:
                    print(
                        f"Error scraping {current_url}: URL resolved to an unsafe IP "
                        "or uses an invalid URL format."
                    )
                    return None

                last_status, headers, page_text = _fetch_once_pinned(
                    current_url, pinned_ip
                )
                if 300 <= last_status < 400:
                    location = headers.get("Location") or headers.get("location")
                    if not location:
                        print(f"Error scraping {current_url}: redirect has no Location header.")
                        return None
                    current_url = urljoin(current_url, location)
                    continue
                break

            if last_status is None or 300 <= last_status < 400:
                print(f"Error scraping {url}: redirect limit exceeded.")
                return None

            soup = BeautifulSoup(page_text, "html.parser")
            if not title:
                title = (
                    soup.title.string.strip()
                    if soup.title and soup.title.string
                    else "untitled_page"
                )

            safe_title = "".join(
                c for c in title if c.isalnum() or c in (" ", "-", "_")
            ).strip().replace(" ", "_")
            if not safe_title:
                safe_title = "untitled_page"
            filename = f"{safe_title[:120]}.md"

            markdown_content = markdownify.markdownify(page_text, heading_style="ATX")
            parsed_source = urlparse(url)
            # Do not persist query strings or URL fragments that may contain credentials.
            safe_source = parsed_source._replace(query="", fragment="").geturl()
            full_content = (
                f"# {title}\n\nSource: {safe_source}\n\n---\n\n"
                f"{markdown_content}\n\n---\n"
                "*Scraped autonomously via [Satya Agent Tracker]"
                "(https://github.com/anktechsol/Satya_AI)*"
            )

            saved_path = storage.save_markdown(filename, full_content)
            if saved_path:
                self.git_handler.commit_and_push(
                    [saved_path], f"Added Truth Source: {title}"
                )
                return filename
            return None
        except (requests.exceptions.RequestException, ValueError, OSError) as exc:
            print(f"Error scraping {current_url}: {exc}")
            return None
        except Exception as exc:
            print(f"Error scraping {current_url}: {exc}")
            return None

    def list_sources(self):
        return storage.list_truth_files()
