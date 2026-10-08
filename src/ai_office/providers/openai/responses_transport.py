"""One synchronous exchange for authenticated Responses-compatible requests."""

import http.client
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from ai_office.providers.openai.responses_auth import (
    OpenAIResponsesAuthenticatedHttpRequest,
)
from ai_office.providers.openai.responses_observability import (
    extract_openai_responses_content_type,
)


@dataclass(frozen=True)
class OpenAIResponsesRawHttpResponse:
    """Immutable raw response returned by the OpenAI HTTPS transport."""

    status_code: int
    reason: str
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def __repr__(self) -> str:
        """Keep raw body and arbitrary response headers out of representations."""
        return (
            "OpenAIResponsesRawHttpResponse("
            f"status_code={self.status_code!r}, "
            f"body_length={len(self.body)!r}, "
            f"content_type={extract_openai_responses_content_type(self.headers)!r}"
            ")"
        )

    def __str__(self) -> str:
        return self.__repr__()


class OpenAIResponsesTransportUrlError(ValueError):
    """Raised when an authenticated request does not have a safe HTTPS URL."""


class OpenAIResponsesTransportError(RuntimeError):
    """Raised when the HTTPS exchange cannot complete safely."""


def _reject_caller_supplied_framing_headers(
    headers: tuple[tuple[str, str], ...],
) -> None:
    """Keep request body framing exclusively under transport control."""
    if any(
        name.lower() in {"content-length", "transfer-encoding"}
        for name, _ in headers
    ):
        raise OpenAIResponsesTransportError(
            "OpenAI Responses transport owns request body framing"
        )


def _create_https_connection(
    hostname: str,
    port: int | None,
) -> http.client.HTTPSConnection:
    address = _require_public_https_destination(hostname, port)
    return _PinnedHTTPSConnection(hostname, address, port=port)


def _create_http_connection(
    hostname: str,
    port: int | None,
) -> http.client.HTTPConnection:
    return http.client.HTTPConnection(hostname, port=port)


def _parse_openai_responses_transport_url(
    url: str,
) -> tuple[str, str, int | None, str]:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        port = parsed.port
    except ValueError:
        raise OpenAIResponsesTransportUrlError(
            "OpenAI Responses transport URL is invalid"
        ) from None

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise OpenAIResponsesTransportUrlError(
            "OpenAI Responses transport requires HTTP or HTTPS"
        )
    if not hostname:
        raise OpenAIResponsesTransportUrlError(
            "OpenAI Responses transport URL requires a hostname"
        )
    if username is not None or password is not None:
        raise OpenAIResponsesTransportUrlError(
            "OpenAI Responses transport URL must not include user information"
        )
    if scheme == "http" and hostname.lower() != "127.0.0.1":
        raise OpenAIResponsesTransportUrlError(
            "HTTP Responses transport is restricted to canonical loopback"
        )

    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    return scheme, hostname, port, target


def send_openai_responses_http_request(
    request: OpenAIResponsesAuthenticatedHttpRequest,
) -> OpenAIResponsesRawHttpResponse:
    """Send one authenticated HTTPS request and preserve its raw response."""
    scheme, hostname, port, target = _parse_openai_responses_transport_url(request.url)
    body = request.body.encode("utf-8")
    _reject_caller_supplied_framing_headers(request.headers)
    connection: http.client.HTTPConnection | http.client.HTTPSConnection | None = None

    try:
        connection = (
            _create_http_connection(hostname, port)
            if scheme == "http"
            else _create_https_connection(hostname, port)
        )
        has_host_header = any(name.lower() == "host" for name, _ in request.headers)
        connection.putrequest(
            request.method,
            target,
            skip_host=has_host_header,
            skip_accept_encoding=True,
        )
        for name, value in request.headers:
            connection.putheader(name, value)
        connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)

        response = connection.getresponse()
        return OpenAIResponsesRawHttpResponse(
            status_code=response.status,
            reason=response.reason,
            headers=tuple(response.getheaders()),
            body=response.read(),
        )
    except Exception:
        raise OpenAIResponsesTransportError(
            "OpenAI Responses HTTPS transport failed"
        ) from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to one validated address while retaining hostname TLS checks."""

    def __init__(self, hostname: str, address: str, *, port: int | None = None) -> None:
        super().__init__(hostname, port=port)
        self._validated_address = address

    def connect(self) -> None:
        if self._tunnel_host is not None:
            raise OpenAIResponsesTransportError(
                "OpenAI Responses HTTPS proxy tunneling is unsupported"
            )
        self.sock = socket.create_connection(
            (self._validated_address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _require_public_https_destination(hostname: str, port: int | None) -> str:
    """Return one public address only when every DNS result is public."""
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                hostname, 443 if port is None else port, type=socket.SOCK_STREAM
            )
        }
        if not addresses or any(
            not ipaddress.ip_address(address).is_global for address in addresses
        ):
            raise ValueError
    except (OSError, ValueError):
        raise OpenAIResponsesTransportError(
            "OpenAI Responses HTTPS destination is not public"
        ) from None
    return sorted(addresses)[0]
