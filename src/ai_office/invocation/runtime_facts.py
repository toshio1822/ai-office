"""Provider-independent typed runtime facts and canonical snapshot identity."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

RuntimeFactOrigin = Literal[
    "persisted_state",
    "persisted_event",
    "human_supplied",
]
RuntimeFactValueKind = Literal[
    "identifier",
    "enum",
    "integer",
    "boolean",
    "timestamp",
]

_RUNTIME_FACTS_SCHEMA_VERSION = "runtime-facts.v1"
_RUNTIME_FACTS_ERROR_MESSAGE = "runtime facts are invalid"
_ORIGINS = frozenset({"persisted_state", "persisted_event", "human_supplied"})
_VALUE_KINDS = frozenset(
    {"identifier", "enum", "integer", "boolean", "timestamp"}
)
_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TOKEN_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SOURCE_REF_PATTERN = re.compile(
    r"^[a-z][a-z0-9]*(?::[a-z0-9]+(?:[._-][a-z0-9]+)*)?$"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_RFC3339_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?P<fraction>\.\d+)?(?P<offset>Z|[+-]\d{2}:\d{2})$"
)
_MIN_SIGNED_INT64 = -(2**63)
_MAX_SIGNED_INT64 = 2**63 - 1


class RuntimeFactsError(ValueError):
    """Raised when typed runtime-facts data violates the v1 contract."""

    def __init__(self) -> None:
        super().__init__(_RUNTIME_FACTS_ERROR_MESSAGE)


@dataclass(frozen=True)
class RuntimeFactProvenance:
    """Safe source identity for one runtime fact."""

    origin: RuntimeFactOrigin
    workflow_id: str
    source_ref: str
    source_sha256: str
    observed_at: str | None = None

    def __post_init__(self) -> None:
        _validate_member(self.origin, _ORIGINS)
        _validate_identifier(self.workflow_id)
        _validate_source_ref(self.source_ref)
        _validate_sha256(self.source_sha256)
        if self.observed_at is not None and type(self.observed_at) is not str:
            _invalid()
        if self.observed_at is not None:
            object.__setattr__(
                self,
                "observed_at",
                normalize_runtime_fact_timestamp(self.observed_at),
            )


@dataclass(frozen=True)
class RuntimeFact:
    """One strictly typed, provenance-bound runtime fact."""

    key: str
    value_kind: RuntimeFactValueKind
    value: str | int | bool
    provenance: RuntimeFactProvenance

    def __post_init__(self) -> None:
        _validate_fact_key(self.key)
        _validate_member(self.value_kind, _VALUE_KINDS)
        if type(self.provenance) is not RuntimeFactProvenance:
            _invalid()
        _validate_fact_value(self.value_kind, self.value)
        if self.value_kind == "timestamp":
            assert type(self.value) is str
            object.__setattr__(
                self,
                "value",
                normalize_runtime_fact_timestamp(self.value),
            )


@dataclass(frozen=True)
class RuntimeFactsSnapshot:
    """Immutable, canonically ordered v1 runtime-facts snapshot."""

    schema_version: Literal["runtime-facts.v1"] = _RUNTIME_FACTS_SCHEMA_VERSION
    facts: tuple[RuntimeFact, ...] = ()

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str:
            _invalid()
        if self.schema_version != _RUNTIME_FACTS_SCHEMA_VERSION:
            _invalid()
        if type(self.facts) is not tuple:
            _invalid()
        if any(type(fact) is not RuntimeFact for fact in self.facts):
            _invalid()
        keys = tuple(fact.key for fact in self.facts)
        if len(keys) != len(set(keys)):
            _invalid()
        object.__setattr__(
            self,
            "facts",
            tuple(sorted(self.facts, key=_runtime_fact_sort_key)),
        )

    @property
    def digest(self) -> str:
        """Return the deterministic SHA-256 identity of this snapshot."""
        return runtime_facts_snapshot_digest(self)


def normalize_runtime_fact_timestamp(value: str) -> str:
    """Normalize one offset-aware RFC3339 timestamp to UTC ``Z`` form."""
    _validate_safe_string(value)
    match = _RFC3339_PATTERN.fullmatch(value)
    if match is None:
        _invalid()
    try:
        offset = match.group("offset")
        parsed = datetime.fromisoformat(
            match.group("date")
            + ("+00:00" if offset == "Z" else offset)
        )
        normalized = parsed.astimezone(UTC)
    except (OverflowError, ValueError):
        _invalid()
    base = (
        f"{normalized.year:04d}-{normalized.month:02d}-{normalized.day:02d}"
        f"T{normalized.hour:02d}:{normalized.minute:02d}:{normalized.second:02d}"
    )
    fraction = match.group("fraction")
    if fraction is not None:
        fraction = fraction[1:].rstrip("0")
        if fraction:
            base += f".{fraction}"
    return f"{base}Z"


def runtime_facts_snapshot_canonical_bytes(
    snapshot: RuntimeFactsSnapshot,
) -> bytes:
    """Serialize one validated snapshot to canonical UTF-8 JSON bytes."""
    return serialize_runtime_facts_snapshot_canonical(snapshot).encode("utf-8")


def serialize_runtime_facts_snapshot_canonical(
    snapshot: RuntimeFactsSnapshot,
) -> str:
    """Serialize one validated snapshot to compact deterministic JSON."""
    if type(snapshot) is not RuntimeFactsSnapshot:
        _invalid()
    value = {
        "schema_version": snapshot.schema_version,
        "facts": [
            {
                "key": fact.key,
                "value_kind": fact.value_kind,
                "value": fact.value,
                "provenance": {
                    "origin": fact.provenance.origin,
                    "workflow_id": fact.provenance.workflow_id,
                    "source_ref": fact.provenance.source_ref,
                    "source_sha256": fact.provenance.source_sha256,
                    "observed_at": fact.provenance.observed_at,
                },
            }
            for fact in snapshot.facts
        ],
    }
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        _invalid()


def runtime_facts_snapshot_digest(snapshot: RuntimeFactsSnapshot) -> str:
    """Return the SHA-256 digest of canonical snapshot UTF-8 bytes."""
    return sha256(runtime_facts_snapshot_canonical_bytes(snapshot)).hexdigest()


def _validate_fact_value(value_kind: object, value: object) -> None:
    if value_kind == "identifier":
        _validate_identifier(value)
    elif value_kind == "enum":
        _validate_enum(value)
    elif value_kind == "integer":
        if (
            type(value) is not int
            or not _MIN_SIGNED_INT64 <= value <= _MAX_SIGNED_INT64
        ):
            _invalid()
    elif value_kind == "boolean":
        if type(value) is not bool:
            _invalid()
    elif value_kind == "timestamp":
        if type(value) is not str:
            _invalid()
        normalize_runtime_fact_timestamp(value)
    else:
        _invalid()


def _validate_fact_key(value: object) -> None:
    _validate_safe_string(value)
    assert type(value) is str
    if not _TOKEN_PATTERN.fullmatch(value):
        _invalid()


def _validate_identifier(value: object) -> None:
    _validate_safe_string(value)
    assert type(value) is str
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        _invalid()


def _validate_enum(value: object) -> None:
    _validate_safe_string(value)
    assert type(value) is str
    if not _TOKEN_PATTERN.fullmatch(value):
        _invalid()


def _validate_source_ref(value: object) -> None:
    _validate_safe_string(value)
    assert type(value) is str
    if not _SOURCE_REF_PATTERN.fullmatch(value):
        _invalid()


def _validate_sha256(value: object) -> None:
    _validate_safe_string(value)
    assert type(value) is str
    if not _SHA256_PATTERN.fullmatch(value):
        _invalid()


def _validate_safe_string(value: object) -> None:
    if type(value) is not str or value == "":
        _invalid()
    assert type(value) is str
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        _invalid()


def _validate_member(value: object, members: frozenset[str]) -> None:
    if type(value) is not str or value not in members:
        _invalid()


def _runtime_fact_sort_key(
    fact: RuntimeFact,
) -> tuple[str, str, str, str, str, str, str, int, str]:
    observed_at = fact.provenance.observed_at
    return (
        fact.key,
        fact.value_kind,
        _canonical_scalar(fact.value),
        fact.provenance.origin,
        fact.provenance.workflow_id,
        fact.provenance.source_ref,
        fact.provenance.source_sha256,
        0 if observed_at is None else 1,
        "" if observed_at is None else observed_at,
    )


def _canonical_scalar(value: str | int | bool) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _invalid() -> None:
    raise RuntimeFactsError() from None


EMPTY_RUNTIME_FACTS = RuntimeFactsSnapshot()


__all__ = [
    "EMPTY_RUNTIME_FACTS",
    "RuntimeFact",
    "RuntimeFactOrigin",
    "RuntimeFactProvenance",
    "RuntimeFactsError",
    "RuntimeFactsSnapshot",
    "RuntimeFactValueKind",
    "normalize_runtime_fact_timestamp",
    "runtime_facts_snapshot_canonical_bytes",
    "runtime_facts_snapshot_digest",
    "serialize_runtime_facts_snapshot_canonical",
]
