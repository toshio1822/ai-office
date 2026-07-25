"""One synchronous HTTPS exchange for authenticated OpenAI Responses requests."""

import http.client
from dataclasses import dataclass
from urllib.parse import urlsplit

from ai_office.providers.openai.responses_auth import (
    OpenAIResponsesAuthenticatedHttpRequest,
)


@dataclass(frozen=True)
class OpenAIResponsesRawHttpResponse:
    """Immutable raw response returned by the OpenAI HTTPS transport."""

    status_code: int
    reason: str
    headers: tuple[tuple[str, str], ...]
    body: bytes


class OpenAIResponsesTransportUrlError(ValueError):
    """Raised when an authenticated request does not have a safe HTTPS URL."""


class OpenAIResponsesTransportError(RuntimeError):
    """Raised when the HTTPS exchange cannot complete safely."""


def _create_https_connection(
    hostname: str,
    port: int | None,
) -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection(hostname, port=port)


def _parse_openai_responses_transport_url(url: str) -> tuple[str, int | None, str]:
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

    if parsed.scheme.lower() != "https":
        raise OpenAIResponsesTransportUrlError(
            "OpenAI Responses transport requires HTTPS"
        )
    if not hostname:
        raise OpenAIResponsesTransportUrlError(
            "OpenAI Responses transport URL requires a hostname"
        )
    if username is not None or password is not None:
        raise OpenAIResponsesTransportUrlError(
            "OpenAI Responses transport URL must not include user information"
        )

    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    return hostname, port, target


def send_openai_responses_http_request(
    request: OpenAIResponsesAuthenticatedHttpRequest,
) -> OpenAIResponsesRawHttpResponse:
    """Send one authenticated HTTPS request and preserve its raw response."""
    hostname, port, target = _parse_openai_responses_transport_url(request.url)
    body = request.body.encode("utf-8")
    connection: http.client.HTTPSConnection | None = None

    try:
        connection = _create_https_connection(hostname, port)
        has_host_header = any(name.lower() == "host" for name, _ in request.headers)
        connection.putrequest(
            request.method,
            target,
            skip_host=has_host_header,
            skip_accept_encoding=True,
        )
        for name, value in request.headers:
            connection.putheader(name, value)
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
