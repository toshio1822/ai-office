"""Immutable durable evidence for one Phase 268 readiness assessment.

Phase 269 stores an already-derived
:class:`PublicationRegenerationReadinessAssessment` without reinterpreting its
readiness semantics or changing either source or regeneration lineage.  This
module has no provider, network, environment, clock, or workflow-state access.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from ai_office.engine.post_terminal_facts import (
    PostTerminalFacts,
    PublicationClaimContract,
)
from ai_office.engine.publication_regeneration_readiness import (
    PublicationRegenerationReadinessAssessment,
    PublicationRegenerationReadinessError,
    publication_regeneration_readiness_assessment_digest,
    serialize_publication_regeneration_readiness_assessment_canonical,
)

_RECORD_ERROR_MESSAGE = "publication regeneration readiness record is invalid"
_PERSISTENCE_ERROR_MESSAGE = (
    "publication regeneration readiness record persistence failed"
)
_LOAD_ERROR_MESSAGE = "publication regeneration readiness record could not be loaded"
_RECORD_SCHEMA_VERSION = "publication-regeneration-readiness-record.v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_READINESS_VALUES = frozenset(
    {
        "ready",
        "insufficient_evidence",
        "stale_or_inconsistent",
        "result_failure",
    }
)
_RECORD_KEYS = frozenset(
    {
        "assessment",
        "assessment_sha256",
        "readiness",
        "reason_codes",
        "regeneration_id",
        "result_record_sha256",
        "schema_version",
        "source_audit_sha256",
    }
)
_ASSESSMENT_KEYS = frozenset(
    {
        "business_output_sha256",
        "claim_contract_sha256",
        "evaluated_claim_contract",
        "outcome",
        "readiness",
        "reason_codes",
        "regeneration_id",
        "result_record_sha256",
        "schema_version",
        "source_audit_sha256",
        "source_post_terminal_facts",
        "source_post_terminal_facts_sha256",
    }
)
_FACTS_KEYS = frozenset(
    {
        "completed_step_ids",
        "events_sha256",
        "final_output_sha256",
        "schema_version",
        "state_sha256",
        "terminal_employee_id",
        "terminal_provider",
        "terminal_reason",
        "terminal_status",
        "terminal_step_id",
        "terminal_step_index",
        "workflow_id",
    }
)
_CLAIM_KEYS = frozenset(
    {
        "asserted_terminal_status",
        "business_output_sha256",
        "post_terminal_facts_sha256",
        "schema_version",
        "scope",
        "workflow_id",
    }
)


@dataclass(frozen=True)
class PublicationRegenerationReadinessRecordFailureDetail:
    """Safe classification for invalid readiness-record evidence."""

    classification: str


class PublicationRegenerationReadinessRecordError(ValueError):
    """Raised when a readiness-evidence record is invalid."""

    def __init__(self, classification: str = "record") -> None:
        super().__init__(_RECORD_ERROR_MESSAGE)
        self.detail = PublicationRegenerationReadinessRecordFailureDetail(
            classification
        )


class PublicationRegenerationReadinessRecordPersistenceError(
    PublicationRegenerationReadinessRecordError
):
    """Raised when readiness evidence cannot be durably persisted."""

    def __init__(self, classification: str = "persistence") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = PublicationRegenerationReadinessRecordFailureDetail(
            classification
        )


class PublicationRegenerationReadinessRecordConflictError(
    PublicationRegenerationReadinessRecordPersistenceError
):
    """Raised when a target already contains different bytes."""


class PublicationRegenerationReadinessRecordLoadError(
    PublicationRegenerationReadinessRecordError
):
    """Raised when a sidecar is not exact canonical record evidence."""

    def __init__(self, classification: str = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = PublicationRegenerationReadinessRecordFailureDetail(
            classification
        )


@dataclass(frozen=True)
class PublicationRegenerationReadinessRecord:
    """Immutable evidence wrapper for one exact Phase 268 assessment."""

    schema_version: Literal["publication-regeneration-readiness-record.v1"]
    assessment: PublicationRegenerationReadinessAssessment
    assessment_sha256: str
    regeneration_id: str
    result_record_sha256: str
    source_audit_sha256: str
    readiness: Literal[
        "ready",
        "insufficient_evidence",
        "stale_or_inconsistent",
        "result_failure",
    ]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_publication_regeneration_readiness_record(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical record JSON."""
        return publication_regeneration_readiness_record_digest(self)


def build_publication_regeneration_readiness_record(
    assessment: PublicationRegenerationReadinessAssessment,
) -> PublicationRegenerationReadinessRecord:
    """Build pure immutable evidence from one exact Phase 268 assessment."""
    if type(assessment) is not PublicationRegenerationReadinessAssessment:
        _raise_record("assessment_type")
    try:
        assessment_digest = publication_regeneration_readiness_assessment_digest(
            assessment
        )
    except (
        PublicationRegenerationReadinessError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        _raise_record("assessment")
    return PublicationRegenerationReadinessRecord(
        schema_version=_RECORD_SCHEMA_VERSION,
        assessment=assessment,
        assessment_sha256=assessment_digest,
        regeneration_id=assessment.regeneration_id,
        result_record_sha256=assessment.result_record_sha256,
        source_audit_sha256=assessment.source_audit_sha256,
        readiness=assessment.readiness,
        reason_codes=assessment.reason_codes,
    )


def serialize_publication_regeneration_readiness_record_canonical(
    record: PublicationRegenerationReadinessRecord,
) -> str:
    """Serialize one readiness record as compact deterministic JSON."""
    _validate_publication_regeneration_readiness_record(record)
    try:
        value = {
            "assessment": json.loads(
                serialize_publication_regeneration_readiness_assessment_canonical(
                    record.assessment
                )
            ),
            "assessment_sha256": record.assessment_sha256,
            "readiness": record.readiness,
            "reason_codes": list(record.reason_codes),
            "regeneration_id": record.regeneration_id,
            "result_record_sha256": record.result_record_sha256,
            "schema_version": record.schema_version,
            "source_audit_sha256": record.source_audit_sha256,
        }
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        _raise_record("record_serialization")


def publication_regeneration_readiness_record_canonical_bytes(
    record: PublicationRegenerationReadinessRecord,
) -> bytes:
    """Return canonical record JSON encoded as exact UTF-8 bytes."""
    try:
        return serialize_publication_regeneration_readiness_record_canonical(
            record
        ).encode("utf-8")
    except UnicodeError:
        _raise_record("record_encoding")


def publication_regeneration_readiness_record_digest(
    record: PublicationRegenerationReadinessRecord,
) -> str:
    """Return the SHA-256 digest of canonical record JSON bytes."""
    return sha256(
        publication_regeneration_readiness_record_canonical_bytes(record)
    ).hexdigest()


def persist_publication_regeneration_readiness_record(
    path: Path,
    record: PublicationRegenerationReadinessRecord,
) -> None:
    """Create or idempotently re-persist one explicit readiness sidecar.

    The record is fully validated and canonicalized before filesystem access.
    Exclusive creation and all durability operations are conservative: an
    ambiguity after creation leaves the artifact in place and is never repaired
    or deleted here.
    """
    contents = publication_regeneration_readiness_record_canonical_bytes(record)
    _validate_record_persistence_path(path)
    try:
        handle = path.open("xb")
    except FileExistsError:
        _persist_existing_record(path, contents)
        return
    except OSError:
        _raise_persistence("create")

    try:
        with handle:
            written = handle.write(contents)
            if written != len(contents):
                raise OSError("short readiness-record write")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_record_directory(path.parent)
    except OSError:
        # Once exclusive create succeeds, the artifact is deliberately retained.
        _raise_persistence("ambiguous")


def load_publication_regeneration_readiness_record(
    path: Path,
) -> PublicationRegenerationReadinessRecord:
    """Read and strictly revalidate one immutable canonical readiness sidecar."""
    _validate_record_load_path(path)
    try:
        contents = path.read_bytes()
    except OSError:
        _raise_load("target")
    try:
        value = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_record_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
        record = _parse_publication_regeneration_readiness_record(value)
        if (
            publication_regeneration_readiness_record_canonical_bytes(record)
            != contents
        ):
            _raise_load("noncanonical")
        return record
    except PublicationRegenerationReadinessRecordLoadError:
        raise
    except (
        UnicodeError,
        json.JSONDecodeError,
        _DuplicateRecordKeyError,
    ):
        _raise_load("parse")
    except (
        PublicationRegenerationReadinessError,
        PublicationRegenerationReadinessRecordError,
        KeyError,
        TypeError,
        ValueError,
    ):
        _raise_load("record")


def _validate_publication_regeneration_readiness_record(
    record: PublicationRegenerationReadinessRecord,
) -> None:
    if type(record) is not PublicationRegenerationReadinessRecord:
        _raise_record("record_type")
    if (
        type(record.schema_version) is not str
        or record.schema_version != _RECORD_SCHEMA_VERSION
    ):
        _raise_record("schema_version")
    if type(record.assessment) is not PublicationRegenerationReadinessAssessment:
        _raise_record("assessment_type")

    # This exact Phase 268 public digest call revalidates every embedded
    # assessment invariant before any wrapper field is accepted.
    try:
        assessment_digest = publication_regeneration_readiness_assessment_digest(
            record.assessment
        )
    except (
        PublicationRegenerationReadinessError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        _raise_record("assessment")
    _validate_digest(record.assessment_sha256, "assessment_digest")
    if record.assessment_sha256 != assessment_digest:
        _raise_record("assessment_binding")

    if type(record.regeneration_id) is not str or not record.regeneration_id:
        _raise_record("regeneration_id")
    for value, classification in (
        (record.result_record_sha256, "result_record_digest"),
        (record.source_audit_sha256, "source_audit_digest"),
    ):
        _validate_digest(value, classification)
    if type(record.readiness) is not str or record.readiness not in _READINESS_VALUES:
        _raise_record("readiness")
    if type(record.reason_codes) is not tuple or any(
        type(reason) is not str or not reason for reason in record.reason_codes
    ):
        _raise_record("reason_codes")

    assessment = record.assessment
    if (
        record.regeneration_id != assessment.regeneration_id
        or record.result_record_sha256 != assessment.result_record_sha256
        or record.source_audit_sha256 != assessment.source_audit_sha256
        or record.readiness != assessment.readiness
        or record.reason_codes != assessment.reason_codes
    ):
        _raise_record("assessment_binding")


def _persist_existing_record(path: Path, contents: bytes) -> None:
    if path.is_symlink():
        _raise_conflict()
    try:
        existing = path.read_bytes()
    except OSError:
        _raise_persistence("target")
    if existing != contents:
        _raise_conflict()
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
        _fsync_record_directory(path.parent)
    except OSError:
        _raise_persistence("ambiguous")


def _validate_record_persistence_path(path: object) -> None:
    if type(path) is not type(Path()):
        _raise_persistence("path_type")
    try:
        if not path.parent.exists() or not path.parent.is_dir():
            _raise_persistence("parent")
        if path.is_dir() or path.is_symlink():
            _raise_persistence("target")
    except OSError:
        _raise_persistence("target")


def _validate_record_load_path(path: object) -> None:
    if type(path) is not type(Path()):
        _raise_load("path_type")
    try:
        if path.is_dir() or path.is_symlink():
            _raise_load("target")
    except OSError:
        _raise_load("target")


def _fsync_record_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_publication_regeneration_readiness_record(
    value: object,
) -> PublicationRegenerationReadinessRecord:
    if type(value) is not dict or frozenset(value) != _RECORD_KEYS:
        _raise_load("keys")
    assessment = _parse_assessment(value["assessment"])
    reason_codes = value["reason_codes"]
    if type(reason_codes) is not list:
        _raise_load("reason_codes")
    try:
        return PublicationRegenerationReadinessRecord(
            schema_version=value["schema_version"],
            assessment=assessment,
            assessment_sha256=value["assessment_sha256"],
            regeneration_id=value["regeneration_id"],
            result_record_sha256=value["result_record_sha256"],
            source_audit_sha256=value["source_audit_sha256"],
            readiness=value["readiness"],
            reason_codes=tuple(reason_codes),
        )
    except (
        PublicationRegenerationReadinessError,
        PublicationRegenerationReadinessRecordError,
        KeyError,
        TypeError,
        ValueError,
    ):
        _raise_load("record")


def _parse_assessment(value: object) -> PublicationRegenerationReadinessAssessment:
    if type(value) is not dict or frozenset(value) != _ASSESSMENT_KEYS:
        _raise_load("assessment_keys")
    facts = _parse_facts(value["source_post_terminal_facts"])
    claim_value = value["evaluated_claim_contract"]
    if claim_value is None:
        claim = None
    else:
        claim = _parse_claim(claim_value)
    reason_codes = value["reason_codes"]
    if type(reason_codes) is not list:
        _raise_load("assessment_reason_codes")
    try:
        return PublicationRegenerationReadinessAssessment(
            schema_version=value["schema_version"],
            regeneration_id=value["regeneration_id"],
            result_record_sha256=value["result_record_sha256"],
            source_audit_sha256=value["source_audit_sha256"],
            source_post_terminal_facts=facts,
            source_post_terminal_facts_sha256=value[
                "source_post_terminal_facts_sha256"
            ],
            outcome=value["outcome"],
            business_output_sha256=value["business_output_sha256"],
            evaluated_claim_contract=claim,
            claim_contract_sha256=value["claim_contract_sha256"],
            readiness=value["readiness"],
            reason_codes=tuple(reason_codes),
        )
    except (
        PublicationRegenerationReadinessError,
        KeyError,
        TypeError,
        ValueError,
    ):
        _raise_load("assessment")


def _parse_facts(value: object) -> PostTerminalFacts:
    if type(value) is not dict or frozenset(value) != _FACTS_KEYS:
        _raise_load("facts_keys")
    completed_step_ids = value["completed_step_ids"]
    if type(completed_step_ids) is not list:
        _raise_load("facts_completed_steps")
    try:
        return PostTerminalFacts(
            schema_version=value["schema_version"],
            workflow_id=value["workflow_id"],
            terminal_status=value["terminal_status"],
            terminal_reason=value["terminal_reason"],
            terminal_step_id=value["terminal_step_id"],
            terminal_step_index=value["terminal_step_index"],
            terminal_employee_id=value["terminal_employee_id"],
            terminal_provider=value["terminal_provider"],
            completed_step_ids=tuple(completed_step_ids),
            state_sha256=value["state_sha256"],
            events_sha256=value["events_sha256"],
            final_output_sha256=value["final_output_sha256"],
        )
    except (KeyError, TypeError, ValueError):
        _raise_load("facts")


def _parse_claim(value: object) -> PublicationClaimContract:
    if type(value) is not dict or frozenset(value) != _CLAIM_KEYS:
        _raise_load("claim_keys")
    try:
        return PublicationClaimContract(
            schema_version=value["schema_version"],
            scope=value["scope"],
            workflow_id=value["workflow_id"],
            business_output_sha256=value["business_output_sha256"],
            post_terminal_facts_sha256=value["post_terminal_facts_sha256"],
            asserted_terminal_status=value["asserted_terminal_status"],
        )
    except (KeyError, TypeError, ValueError):
        _raise_load("claim")


def _validate_digest(value: object, classification: str) -> None:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        _raise_record(classification)


class _DuplicateRecordKeyError(ValueError):
    pass


def _reject_duplicate_record_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateRecordKeyError
        result[key] = value
    return result


def _reject_nonstandard_json_constant(constant: str) -> NoReturn:
    raise ValueError(constant)


def _raise_record(classification: str) -> NoReturn:
    raise PublicationRegenerationReadinessRecordError(classification) from None


def _raise_persistence(classification: str) -> NoReturn:
    raise PublicationRegenerationReadinessRecordPersistenceError(
        classification
    ) from None


def _raise_conflict() -> NoReturn:
    raise PublicationRegenerationReadinessRecordConflictError("conflict") from None


def _raise_load(classification: str) -> NoReturn:
    raise PublicationRegenerationReadinessRecordLoadError(classification) from None


__all__ = [
    "PublicationRegenerationReadinessRecord",
    "PublicationRegenerationReadinessRecordConflictError",
    "PublicationRegenerationReadinessRecordError",
    "PublicationRegenerationReadinessRecordFailureDetail",
    "PublicationRegenerationReadinessRecordLoadError",
    "PublicationRegenerationReadinessRecordPersistenceError",
    "build_publication_regeneration_readiness_record",
    "load_publication_regeneration_readiness_record",
    "persist_publication_regeneration_readiness_record",
    "publication_regeneration_readiness_record_canonical_bytes",
    "publication_regeneration_readiness_record_digest",
    "serialize_publication_regeneration_readiness_record_canonical",
]
