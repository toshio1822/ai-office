"""Strict administrator registry for OpenAI-compatible destinations."""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from ai_office.execution_target import (
    SUPPORTED_EXECUTION_PROTOCOLS,
    ModelExecutionTarget,
    ModelExecutionTargetError,
    canonicalize_execution_target_url,
    validate_execution_target_for_provider,
)
from ai_office.request_headers import (
    ConfiguredRequestHeaderError,
    parse_configured_request_headers,
)

_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ENVIRONMENT_VARIABLE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_ENTRY_KEYS = frozenset({"endpoint", "protocol", "credential", "models"})
_OPTIONAL_ENTRY_KEYS = frozenset({"headers"})
_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata.google.internal.",
        "instance-data",
        "instance-data.",
    }
)


class ExecutionDestinationError(ValueError):
    """Raised when the administrator registry is unavailable or unsafe."""


@dataclass(frozen=True)
class ExecutionDestination:
    """One immutable destination plus its administrator-permitted models."""

    target: ModelExecutionTarget
    models: tuple[str, ...]

    def select_model(self, model: str) -> str:
        if type(model) is not str or model not in self.models:
            raise ExecutionDestinationError("execution model is not permitted")
        return model


@dataclass(frozen=True)
class ExecutionDestinationRegistry:
    """A detached, deterministic snapshot of one administrator registry."""

    destinations: tuple[ExecutionDestination, ...]

    def resolve(self, name: str) -> ExecutionDestination:
        matches = tuple(
            item for item in self.destinations if item.target.provider == name
        )
        if len(matches) != 1:
            raise ExecutionDestinationError("execution destination is invalid")
        return matches[0]


def load_execution_destination_registry(path: Path) -> ExecutionDestinationRegistry:
    """Load a strict YAML registry without reading credentials or the network."""
    if type(path) is not type(Path()):
        raise ExecutionDestinationError("execution destination registry is invalid")
    try:
        if path.is_symlink() or not path.is_file():
            raise ExecutionDestinationError("execution destination registry is invalid")
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except ExecutionDestinationError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ExecutionDestinationError(
            "execution destination registry is invalid"
        ) from error
    if type(value) is not dict or frozenset(value) != {"destinations"}:
        raise ExecutionDestinationError("execution destination registry is invalid")
    raw_destinations = value["destinations"]
    if type(raw_destinations) is not dict or not raw_destinations:
        raise ExecutionDestinationError("execution destination registry is invalid")

    destinations: list[ExecutionDestination] = []
    for name, entry in raw_destinations.items():
        destinations.append(_parse_destination(name, entry))
    destinations.sort(key=lambda item: item.target.provider)
    return ExecutionDestinationRegistry(tuple(destinations))


def _parse_destination(name: object, entry: object) -> ExecutionDestination:
    if (
        type(name) is not str
        or _NAME.fullmatch(name) is None
        or name in {"openai", "omniroute"}
        or type(entry) is not dict
        or frozenset(entry) not in {_ENTRY_KEYS, _ENTRY_KEYS | _OPTIONAL_ENTRY_KEYS}
    ):
        raise ExecutionDestinationError("execution destination is invalid")
    endpoint = entry["endpoint"]
    protocol = entry["protocol"]
    credential = entry["credential"]
    raw_models = entry["models"]
    raw_headers = entry.get("headers")
    try:
        request_headers = (
            ()
            if raw_headers is None
            else parse_configured_request_headers(raw_headers)
        )
    except ConfiguredRequestHeaderError as error:
        raise ExecutionDestinationError(
            "execution destination is invalid"
        ) from error
    if (
        type(endpoint) is not str
        or type(protocol) is not str
        or protocol not in SUPPORTED_EXECUTION_PROTOCOLS
        or type(credential) is not str
        or _ENVIRONMENT_VARIABLE.fullmatch(credential) is None
        or type(raw_models) is not list
        or not raw_models
        or any(type(model) is not str or not model.strip() for model in raw_models)
        or len(raw_models) != len(set(raw_models))
    ):
        raise ExecutionDestinationError("execution destination is invalid")
    try:
        canonical_endpoint = canonicalize_execution_target_url(endpoint)
        parsed = urlsplit(canonical_endpoint)
    except ModelExecutionTargetError as error:
        raise ExecutionDestinationError("execution destination is unsafe") from error
    if (
        parsed.scheme != "https"
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
        or _is_blocked_host(parsed.hostname)
    ):
        raise ExecutionDestinationError("execution destination is unsafe")
    canonical_value = {
        "credential": credential,
        "endpoint": canonical_endpoint,
        "models": raw_models,
        "name": name,
        "protocol": protocol,
    }
    if request_headers:
        canonical_value["headers"] = [
            [header.name, header.value] for header in request_headers
        ]
    fingerprint = sha256(
        json.dumps(
            canonical_value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    target = ModelExecutionTarget(
        provider=name,
        protocol=protocol,
        base_url=canonical_endpoint,
        credential_environment_variable=credential,
        allow_loopback_http=False,
        configuration_fingerprint=fingerprint,
        request_headers=request_headers,
    )
    validate_execution_target_for_provider(target, provider=name)
    return ExecutionDestination(target=target, models=tuple(raw_models))


def _is_blocked_host(host: str) -> bool:
    value = host.rstrip(".").lower()
    if value in _BLOCKED_HOSTS or value.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not address.is_global


__all__ = [
    "ExecutionDestination",
    "ExecutionDestinationError",
    "ExecutionDestinationRegistry",
    "load_execution_destination_registry",
]
