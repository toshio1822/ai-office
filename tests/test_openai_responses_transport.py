"""Tests for the one-request OpenAI Responses HTTPS transport boundary."""

from dataclasses import FrozenInstanceError, fields

import pytest

import ai_office.providers.openai.responses_transport as transport
from ai_office.providers.openai import (
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesRawHttpResponse,
    OpenAIResponsesTransportError,
    OpenAIResponsesTransportUrlError,
    send_openai_responses_http_request,
)


class FakeResponse:
    def __init__(self, status: int = 200, reason: str = "OK") -> None:
        self.status = status
        self.reason = reason

    def getheaders(self) -> list[tuple[str, str]]:
        return [("X-First", "one"), ("Set-Cookie", "a"), ("Set-Cookie", "b")]

    def read(self) -> bytes:
        return b"raw response bytes"


class FakeConnection:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response or FakeResponse()
        self.putrequest_calls: list[tuple[object, ...]] = []
        self.headers: list[tuple[str, str]] = []
        self.bodies: list[bytes] = []
        self.closed = False

    def putrequest(
        self,
        method: str,
        target: str,
        *,
        skip_host: bool,
        skip_accept_encoding: bool,
    ) -> None:
        self.putrequest_calls.append(
            (method, target, skip_host, skip_accept_encoding)
        )

    def putheader(self, name: str, value: str) -> None:
        self.headers.append((name, value))

    def endheaders(self, body: bytes) -> None:
        self.bodies.append(body)

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


def authenticated_request(
    url: str = "https://api.example.test/v1/responses?x=1",
) -> OpenAIResponsesAuthenticatedHttpRequest:
    return OpenAIResponsesAuthenticatedHttpRequest(
        method="POST",
        url=url,
        headers=(
            ("Content-Type", "application/json"),
            ("X-Duplicate", "first"),
            ("X-Duplicate", "second"),
            ("Authorization", "Bearer test-secret"),
        ),
        body="日本語 ✨",
    )


def test_raw_response_is_frozen_and_preserves_raw_values() -> None:
    response = OpenAIResponsesRawHttpResponse(
        status_code=429,
        reason="Too Many Requests",
        headers=(("Set-Cookie", "a"), ("Set-Cookie", "b")),
        body=b"bytes",
    )

    assert [field.name for field in fields(response)] == [
        "status_code",
        "reason",
        "headers",
        "body",
    ]
    with pytest.raises(FrozenInstanceError):
        response.body = b"changed"  # type: ignore[misc]


def test_transport_sends_one_ordered_request_and_preserves_raw_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    created: list[tuple[str, int | None]] = []

    def factory(hostname: str, port: int | None) -> FakeConnection:
        created.append((hostname, port))
        return connection

    monkeypatch.setattr(transport, "_create_https_connection", factory)

    response = send_openai_responses_http_request(authenticated_request())

    assert created == [("api.example.test", None)]
    assert connection.putrequest_calls == [("POST", "/v1/responses?x=1", False, True)]
    assert connection.headers == list(authenticated_request().headers)
    assert connection.bodies == ["日本語 ✨".encode()]
    assert connection.closed is True
    assert response == OpenAIResponsesRawHttpResponse(
        status_code=200,
        reason="OK",
        headers=(("X-First", "one"), ("Set-Cookie", "a"), ("Set-Cookie", "b")),
        body=b"raw response bytes",
    )


@pytest.mark.parametrize("status", [302, 404, 500])
def test_completed_non_success_statuses_are_returned(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    connection = FakeConnection(FakeResponse(status, "status"))
    monkeypatch.setattr(
        transport, "_create_https_connection", lambda hostname, port: connection
    )

    response = send_openai_responses_http_request(authenticated_request())

    assert response.status_code == status
    assert response.reason == "status"
    assert connection.closed is True


def test_empty_path_and_explicit_https_port_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    created: list[tuple[str, int | None]] = []

    def factory(hostname: str, port: int | None) -> FakeConnection:
        created.append((hostname, port))
        return connection

    monkeypatch.setattr(transport, "_create_https_connection", factory)

    send_openai_responses_http_request(
        authenticated_request("https://api.example.test:8443?query=value")
    )

    assert created == [("api.example.test", 8443)]
    assert connection.putrequest_calls == [("POST", "/?query=value", False, True)]


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.test/path",
        "https:///path",
        "https://user@api.example.test/",
        "https://:password@api.example.test/",
    ],
)
def test_invalid_urls_are_rejected_before_connection_creation(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    created = False

    def factory(hostname: str, port: int | None) -> FakeConnection:
        nonlocal created
        created = True
        return FakeConnection()

    monkeypatch.setattr(transport, "_create_https_connection", factory)

    with pytest.raises(OpenAIResponsesTransportUrlError):
        send_openai_responses_http_request(authenticated_request(url))

    assert created is False


def test_transport_failure_is_safe_closes_once_and_never_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingConnection(FakeConnection):
        def putrequest(
            self,
            method: str,
            target: str,
            *,
            skip_host: bool,
            skip_accept_encoding: bool,
        ) -> None:
            raise RuntimeError("Bearer test-secret 日本語 ✨ raw response bytes")

    connection = FailingConnection()
    created = 0

    def factory(hostname: str, port: int | None) -> FailingConnection:
        nonlocal created
        created += 1
        return connection

    monkeypatch.setattr(transport, "_create_https_connection", factory)

    with pytest.raises(OpenAIResponsesTransportError) as error:
        send_openai_responses_http_request(authenticated_request())

    assert str(error.value) == "OpenAI Responses HTTPS transport failed"
    assert "test-secret" not in str(error.value)
    assert "日本語" not in str(error.value)
    assert "raw response bytes" not in str(error.value)
    assert created == 1
    assert connection.closed is True
