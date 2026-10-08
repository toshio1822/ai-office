"""Immutable, secret-free execution targets for model invocations."""

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import urlsplit, urlunsplit

from ai_office.request_headers import (
    ConfiguredRequestHeader,
    ConfiguredRequestHeaderError,
    validate_request_headers,
)

OPENAI_RESPONSES_PROTOCOL = "openai-responses"
OPENAI_CHAT_COMPLETIONS_PROTOCOL = "openai-chat-completions"
SUPPORTED_EXECUTION_PROTOCOLS = frozenset(
    (OPENAI_RESPONSES_PROTOCOL, OPENAI_CHAT_COMPLETIONS_PROTOCOL)
)
_OPENAI_ENDPOINT = "https://api.openai.com/v1/responses"
_OMNIROUTE_ENDPOINT = "http://127.0.0.1:20128/v1/responses"
SUPPORTED_EXECUTION_PROVIDERS = frozenset(("openai", "omniroute"))
_DESTINATION_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ENVIRONMENT_VARIABLE_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def is_supported_execution_provider(value: object) -> bool:
    """Return whether a persisted provider identity is Phase-221 supported."""
    return type(value) is str and (
        value in SUPPORTED_EXECUTION_PROVIDERS
        or _DESTINATION_NAME_PATTERN.fullmatch(value) is not None
    )


class ModelExecutionTargetError(ValueError):
    """Raised when an execution target is not an exact safe target value."""


def canonicalize_execution_target_url(value: str) -> str:
    """Canonicalize one endpoint without resolving DNS or changing identity."""
    if type(value) is not str or not value or value != value.strip():
        raise ModelExecutionTargetError("execution target endpoint is invalid")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        # Accessing port forces validation of malformed numeric ports.
        port = parsed.port
    except (TypeError, ValueError):
        raise ModelExecutionTargetError(
            "execution target endpoint is invalid"
        ) from None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ModelExecutionTargetError("execution target endpoint is invalid")
    host = hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    path = parsed.path or "/"
    canonical = urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))
    if parsed.scheme.lower() == "http" and (
        host != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ModelExecutionTargetError(
            "plaintext execution target must be canonical loopback"
        )
    return canonical


@dataclass(frozen=True)
class ModelExecutionTarget:
    """A provider-independent, immutable and secret-free execution identity."""

    provider: str
    protocol: str
    base_url: str
    credential_environment_variable: str
    allow_loopback_http: bool
    configuration_fingerprint: str | None = None
    request_headers: tuple[ConfiguredRequestHeader, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "provider",
            "protocol",
            "base_url",
            "credential_environment_variable",
        ):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ModelExecutionTargetError(
                    "execution target contains an invalid field"
                )
        if self.protocol not in SUPPORTED_EXECUTION_PROTOCOLS:
            raise ModelExecutionTargetError("execution target protocol is unsupported")
        if type(self.allow_loopback_http) is not bool:
            raise ModelExecutionTargetError(
                "execution target loopback policy is invalid"
            )
        canonical = canonicalize_execution_target_url(self.base_url)
        object.__setattr__(self, "base_url", canonical)
        if self.configuration_fingerprint is not None and (
            type(self.configuration_fingerprint) is not str
            or _SHA256_PATTERN.fullmatch(self.configuration_fingerprint) is None
        ):
            raise ModelExecutionTargetError(
                "execution target configuration fingerprint is invalid"
            )
        try:
            validate_request_headers(self.request_headers)
        except ConfiguredRequestHeaderError as error:
            raise ModelExecutionTargetError(
                "execution target request headers are invalid"
            ) from error

    @property
    def endpoint(self) -> str:
        """Return the canonical endpoint identity used by the HTTP adapter."""
        return self.base_url

    def descriptor(self) -> dict[str, object]:
        """Return the safe descriptor suitable for preview output."""
        value: dict[str, object] = {
            "allow_loopback_http": self.allow_loopback_http,
            "provider": self.provider,
            "protocol": self.protocol,
            "endpoint": self.base_url,
            "credential_environment_variable": self.credential_environment_variable,
        }
        if self.configuration_fingerprint is not None:
            value["configuration_fingerprint"] = self.configuration_fingerprint
        if self.request_headers:
            value["request_headers"] = [
                {"name": header.name, "value": header.value}
                for header in self.request_headers
            ]
        return value


# These are values, not mutable configuration or credential holders.
DIRECT_OPENAI_EXECUTION_TARGET = ModelExecutionTarget(
    provider="openai",
    protocol=OPENAI_RESPONSES_PROTOCOL,
    base_url=_OPENAI_ENDPOINT,
    credential_environment_variable="OPENAI_API_KEY",
    allow_loopback_http=False,
)
LOCAL_OMNIROUTE_EXECUTION_TARGET = ModelExecutionTarget(
    provider="omniroute",
    protocol=OPENAI_RESPONSES_PROTOCOL,
    base_url=_OMNIROUTE_ENDPOINT,
    credential_environment_variable="OMNIROUTE_API_KEY",
    allow_loopback_http=True,
)

# Short aliases make the two built-ins discoverable without introducing a
# provider-specific model or configuration object.
OPENAI_EXECUTION_TARGET = DIRECT_OPENAI_EXECUTION_TARGET
OMNIROUTE_EXECUTION_TARGET = LOCAL_OMNIROUTE_EXECUTION_TARGET


def direct_openai_execution_target() -> ModelExecutionTarget:
    """Return the canonical direct-OpenAI target."""
    return DIRECT_OPENAI_EXECUTION_TARGET


def local_omniroute_execution_target() -> ModelExecutionTarget:
    """Return the canonical loopback OmniRoute target."""
    return LOCAL_OMNIROUTE_EXECUTION_TARGET


def openai_execution_target() -> ModelExecutionTarget:
    """Compatibility alias for :func:`direct_openai_execution_target`."""
    return DIRECT_OPENAI_EXECUTION_TARGET


def omniroute_execution_target() -> ModelExecutionTarget:
    """Compatibility alias for the canonical local OmniRoute target."""
    return LOCAL_OMNIROUTE_EXECUTION_TARGET


def execution_target_for_name(name: str) -> ModelExecutionTarget:
    """Resolve the only two operator-selectable target names."""
    if type(name) is not str:
        raise ModelExecutionTargetError("execution target selection is invalid")
    if name == "openai":
        return DIRECT_OPENAI_EXECUTION_TARGET
    if name == "omniroute":
        return LOCAL_OMNIROUTE_EXECUTION_TARGET
    raise ModelExecutionTargetError("execution target selection is invalid")


def validate_model_execution_target(target: object) -> ModelExecutionTarget:
    """Validate an exact target instance without consulting the environment."""
    if type(target) is not ModelExecutionTarget:
        raise ModelExecutionTargetError("execution target is invalid")
    # The constructor validates the value; repeat the field checks here so a
    # deliberately malformed object created with object.__new__ cannot cross a
    # provider boundary.
    if (
        type(target.provider) is not str
        or not target.provider
        or type(target.protocol) is not str
        or target.protocol not in SUPPORTED_EXECUTION_PROTOCOLS
        or type(target.base_url) is not str
        or not target.base_url
        or type(target.credential_environment_variable) is not str
        or not target.credential_environment_variable
        or type(target.allow_loopback_http) is not bool
        or (
            target.configuration_fingerprint is not None
            and (
                type(target.configuration_fingerprint) is not str
                or _SHA256_PATTERN.fullmatch(target.configuration_fingerprint) is None
            )
        )
    ):
        raise ModelExecutionTargetError("execution target is invalid")
    if canonicalize_execution_target_url(target.base_url) != target.base_url:
        raise ModelExecutionTargetError("execution target is not canonical")
    try:
        validate_request_headers(target.request_headers)
    except ConfiguredRequestHeaderError as error:
        raise ModelExecutionTargetError(
            "execution target request headers are invalid"
        ) from error
    return target


def validate_execution_target_for_provider(
    target: object,
    *,
    provider: str | None = None,
) -> ModelExecutionTarget:
    """Validate the only two execution targets supported by this phase.

    ``ModelExecutionTarget`` remains provider-independent so it can be reused
    by the adapter boundary.  The paid-execution boundary is deliberately
    narrower: Phase 221 has two canonical built-ins and no arbitrary URL
    option.  Keeping that restriction here also makes a mutated target fail
    before a credential or transport is consulted.
    """
    value = validate_model_execution_target(target)
    expected_provider = value.provider if provider is None else provider
    if type(expected_provider) is not str or value.provider != expected_provider:
        raise ModelExecutionTargetError("execution target provider is unsupported")
    if expected_provider in SUPPORTED_EXECUTION_PROVIDERS:
        canonical = execution_target_for_name(expected_provider)
        if value != canonical:
            raise ModelExecutionTargetError("execution target is not canonical")
    elif (
        _DESTINATION_NAME_PATTERN.fullmatch(expected_provider) is None
        or expected_provider in SUPPORTED_EXECUTION_PROVIDERS
        or value.configuration_fingerprint is None
        or value.allow_loopback_http
        or not value.endpoint.startswith("https://")
        or _ENVIRONMENT_VARIABLE_PATTERN.fullmatch(
            value.credential_environment_variable
        )
        is None
    ):
        raise ModelExecutionTargetError("execution target provider is unsupported")
    return value


def execution_target_fingerprint(target: ModelExecutionTarget) -> str:
    """Return a deterministic digest of the complete non-secret target."""
    target = validate_model_execution_target(target)
    value: dict[str, object] = {
        "allow_loopback_http": target.allow_loopback_http,
        "base_url": target.base_url,
        "credential_environment_variable": target.credential_environment_variable,
        "protocol": target.protocol,
        "provider": target.provider,
    }
    if target.configuration_fingerprint is not None:
        value["configuration_fingerprint"] = target.configuration_fingerprint
    if target.request_headers:
        value["request_headers"] = [
            [header.name, header.value] for header in target.request_headers
        ]
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "DIRECT_OPENAI_EXECUTION_TARGET",
    "LOCAL_OMNIROUTE_EXECUTION_TARGET",
    "ModelExecutionTarget",
    "ModelExecutionTargetError",
    "OMNIROUTE_EXECUTION_TARGET",
    "OPENAI_EXECUTION_TARGET",
    "OPENAI_CHAT_COMPLETIONS_PROTOCOL",
    "OPENAI_RESPONSES_PROTOCOL",
    "SUPPORTED_EXECUTION_PROTOCOLS",
    "SUPPORTED_EXECUTION_PROVIDERS",
    "canonicalize_execution_target_url",
    "direct_openai_execution_target",
    "execution_target_fingerprint",
    "execution_target_for_name",
    "is_supported_execution_provider",
    "local_omniroute_execution_target",
    "omniroute_execution_target",
    "openai_execution_target",
    "validate_execution_target_for_provider",
    "validate_model_execution_target",
]
