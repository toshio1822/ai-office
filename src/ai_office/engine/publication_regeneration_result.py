"""Immutable, durable result evidence for one regeneration attempt.

Phase 267 persists the exact normalized ``ModelInvocationResult`` returned by
Phase 266 as a separate new-lineage artifact.  This module owns only the pure
result/record contracts and explicit result-sidecar persistence; it never calls
a provider and never changes the original workflow artifacts.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn, get_args

from ai_office.engine.publication_regeneration import (
    PublicationRegenerationAttemptClaim,
    PublicationRegenerationAttemptClaimError,
    publication_regeneration_attempt_claim_canonical_bytes,
    publication_regeneration_attempt_claim_digest,
)
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationFailureCategory,
    ModelInvocationFailureDiagnostics,
    ModelInvocationResponseBodyKind,
    ModelInvocationResult,
    ModelInvocationSuccess,
)

_RESULT_ERROR_MESSAGE = "publication regeneration result is invalid"
_RESULT_PERSISTENCE_ERROR_MESSAGE = "publication regeneration result persistence failed"
_RESULT_LOAD_ERROR_MESSAGE = "publication regeneration result could not be loaded"
_RESULT_SCHEMA_VERSION = "publication-regeneration-result.v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_RESPONSE_BODY_KINDS = frozenset(get_args(ModelInvocationResponseBodyKind))
_RESULT_RECORD_KEYS = frozenset(
    {
        "attempt_claim",
        "attempt_claim_sha256",
        "business_output_sha256",
        "execution_target_sha256",
        "invocation_request_sha256",
        "outcome",
        "provider",
        "regeneration_id",
        "regeneration_plan_sha256",
        "result",
        "result_sha256",
        "schema_version",
        "source_audit_sha256",
    }
)
_SUCCESS_RESULT_KEYS = frozenset(
    {
        "kind",
        "provider",
        "request_id",
        "response_id",
        "status",
        "text",
        "text_parts",
    }
)
_FAILURE_RESULT_KEYS = frozenset(
    {
        "category",
        "kind",
        "message",
        "provider",
        "provider_error_code",
        "provider_error_type",
        "request_id",
        "response_diagnostics",
        "status_code",
    }
)
_DIAGNOSTICS_KEYS = frozenset(
    {"body_kind", "body_length", "content_type", "status_code"}
)
_CLAIM_KEYS = frozenset(
    {
        "approval_id",
        "approved_by",
        "consumption_key",
        "execution_target_sha256",
        "invocation_request_sha256",
        "provider",
        "regeneration_approval_sha256",
        "regeneration_id",
        "regeneration_plan_sha256",
        "schema_version",
        "source_audit_sha256",
        "state",
    }
)


@dataclass(frozen=True)
class PublicationRegenerationResultFailureDetail:
    """Safe classification for invalid result evidence."""

    classification: str


class PublicationRegenerationResultError(ValueError):
    """Base error for exact result evidence contracts."""

    def __init__(self, classification: str = "result") -> None:
        super().__init__(_RESULT_ERROR_MESSAGE)
        self.detail = PublicationRegenerationResultFailureDetail(classification)


class PublicationRegenerationResultPersistenceError(PublicationRegenerationResultError):
    """Raised when a result sidecar cannot be durably committed."""

    def __init__(self, classification: str = "persistence") -> None:
        ValueError.__init__(self, _RESULT_PERSISTENCE_ERROR_MESSAGE)
        self.detail = PublicationRegenerationResultFailureDetail(classification)


class PublicationRegenerationResultConflictError(
    PublicationRegenerationResultPersistenceError
):
    """Raised when an existing result path is not identical evidence."""


class PublicationRegenerationResultLoadError(PublicationRegenerationResultError):
    """Raised when a result sidecar is not exact canonical evidence."""

    def __init__(self, classification: str = "load") -> None:
        ValueError.__init__(self, _RESULT_LOAD_ERROR_MESSAGE)
        self.detail = PublicationRegenerationResultFailureDetail(classification)


@dataclass(frozen=True)
class PublicationRegenerationResultRecord:
    """Immutable result evidence bound to one exact Phase 265 claim."""

    schema_version: Literal["publication-regeneration-result.v1"]
    attempt_claim: PublicationRegenerationAttemptClaim
    attempt_claim_sha256: str
    regeneration_id: str
    regeneration_plan_sha256: str
    source_audit_sha256: str
    provider: str
    execution_target_sha256: str
    invocation_request_sha256: str
    outcome: Literal["success", "failure"]
    result: ModelInvocationResult
    result_sha256: str
    business_output_sha256: str | None

    def __post_init__(self) -> None:
        _validate_publication_regeneration_result_record(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical result-record JSON."""
        return publication_regeneration_result_record_digest(self)


def serialize_publication_regeneration_result_canonical(
    result: ModelInvocationResult,
) -> str:
    """Serialize one exact normalized invocation result as canonical JSON."""
    _validate_model_invocation_result(result)
    if type(result) is ModelInvocationSuccess:
        value = {
            "kind": "success",
            "provider": result.provider,
            "request_id": result.request_id,
            "response_id": result.response_id,
            "status": result.status,
            "text": result.text,
            "text_parts": list(result.text_parts),
        }
    else:
        diagnostics = result.response_diagnostics
        value = {
            "category": result.category,
            "kind": "failure",
            "message": result.message,
            "provider": result.provider,
            "provider_error_code": result.provider_error_code,
            "provider_error_type": result.provider_error_type,
            "request_id": result.request_id,
            "response_diagnostics": (
                {
                    "body_kind": diagnostics.body_kind,
                    "body_length": diagnostics.body_length,
                    "content_type": diagnostics.content_type,
                    "status_code": diagnostics.status_code,
                }
                if diagnostics is not None
                else None
            ),
            "status_code": result.status_code,
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
        _raise_result("serialization")


def publication_regeneration_result_canonical_bytes(
    result: ModelInvocationResult,
) -> bytes:
    """Return canonical normalized-result JSON as UTF-8 bytes."""
    return serialize_publication_regeneration_result_canonical(result).encode("utf-8")


def publication_regeneration_result_digest(result: ModelInvocationResult) -> str:
    """Return the SHA-256 identity of one normalized invocation result."""
    return sha256(publication_regeneration_result_canonical_bytes(result)).hexdigest()


def build_publication_regeneration_result_record(
    claim: PublicationRegenerationAttemptClaim,
    result: ModelInvocationResult,
) -> PublicationRegenerationResultRecord:
    """Build pure immutable evidence from one exact claim and result."""
    _validate_attempt_claim_for_result(claim)
    _validate_model_invocation_result(result)
    claim_digest = publication_regeneration_attempt_claim_digest(claim)
    result_digest = publication_regeneration_result_digest(result)
    if type(result) is ModelInvocationSuccess:
        outcome: Literal["success", "failure"] = "success"
        business_output_sha256 = sha256(result.text.encode("utf-8")).hexdigest()
    else:
        outcome = "failure"
        business_output_sha256 = None
    return PublicationRegenerationResultRecord(
        schema_version=_RESULT_SCHEMA_VERSION,
        attempt_claim=claim,
        attempt_claim_sha256=claim_digest,
        regeneration_id=claim.regeneration_id,
        regeneration_plan_sha256=claim.regeneration_plan_sha256,
        source_audit_sha256=claim.source_audit_sha256,
        provider=claim.provider,
        execution_target_sha256=claim.execution_target_sha256,
        invocation_request_sha256=claim.invocation_request_sha256,
        outcome=outcome,
        result=result,
        result_sha256=result_digest,
        business_output_sha256=business_output_sha256,
    )


def serialize_publication_regeneration_result_record_canonical(
    record: PublicationRegenerationResultRecord,
) -> str:
    """Serialize one result record as compact deterministic JSON."""
    _validate_publication_regeneration_result_record(record)
    try:
        value = {
            "attempt_claim": json.loads(
                publication_regeneration_attempt_claim_canonical_bytes(
                    record.attempt_claim
                )
            ),
            "attempt_claim_sha256": record.attempt_claim_sha256,
            "business_output_sha256": record.business_output_sha256,
            "execution_target_sha256": record.execution_target_sha256,
            "invocation_request_sha256": record.invocation_request_sha256,
            "outcome": record.outcome,
            "provider": record.provider,
            "regeneration_id": record.regeneration_id,
            "regeneration_plan_sha256": record.regeneration_plan_sha256,
            "result": json.loads(
                serialize_publication_regeneration_result_canonical(record.result)
            ),
            "result_sha256": record.result_sha256,
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
        _raise_result("record_serialization")


def publication_regeneration_result_record_canonical_bytes(
    record: PublicationRegenerationResultRecord,
) -> bytes:
    """Return canonical result-record JSON as UTF-8 bytes."""
    return serialize_publication_regeneration_result_record_canonical(record).encode(
        "utf-8"
    )


def publication_regeneration_result_record_digest(
    record: PublicationRegenerationResultRecord,
) -> str:
    """Return the SHA-256 identity of canonical result-record JSON."""
    return sha256(
        publication_regeneration_result_record_canonical_bytes(record)
    ).hexdigest()


def persist_publication_regeneration_result(
    path: Path,
    record: PublicationRegenerationResultRecord,
    *,
    allow_idempotent: bool = True,
) -> PublicationRegenerationResultRecord:
    """Create or idempotently re-persist one exact result evidence sidecar."""
    if type(allow_idempotent) is not bool:
        _raise_persistence("idempotent_option")
    _validate_result_persistence_path(path)
    if type(record) is not PublicationRegenerationResultRecord:
        _raise_persistence("record_type")
    contents = publication_regeneration_result_record_canonical_bytes(record)
    try:
        handle = path.open("xb")
    except FileExistsError:
        if not allow_idempotent:
            _raise_conflict()
        return _persist_existing_result(path, record, contents)
    except OSError:
        _raise_persistence("create")

    try:
        with handle:
            written = handle.write(contents)
            if written != len(contents):
                raise OSError("short result write")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_result_directory(path.parent)
    except OSError:
        # The result path is never deleted or reset after exclusive creation.
        _raise_persistence("ambiguous")
    return record


def preflight_publication_regeneration_result_path(path: Path) -> None:
    """Validate a new result target before any provider attempt."""
    _validate_result_persistence_path(path)
    try:
        if path.exists() or path.is_symlink():
            _raise_persistence("target_exists")
    except OSError:
        _raise_persistence("target")


def load_publication_regeneration_result(
    path: Path,
) -> PublicationRegenerationResultRecord:
    """Read and strictly revalidate one immutable canonical result sidecar."""
    _validate_result_load_path(path)
    try:
        contents = path.read_bytes()
    except OSError:
        _raise_load("target")
    try:
        value = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_result_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
        record = _parse_publication_regeneration_result_record(value)
        if publication_regeneration_result_record_canonical_bytes(record) != contents:
            _raise_load("noncanonical")
        return record
    except PublicationRegenerationResultLoadError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateResultKeyError,
    ):
        _raise_load("parse")
    except PublicationRegenerationResultError:
        _raise_load("record")
    except (PublicationRegenerationAttemptClaimError, TypeError, ValueError):
        _raise_load("record")


def _validate_model_invocation_result(result: object) -> None:
    if type(result) is ModelInvocationSuccess:
        if not all(
            type(getattr(result, name)) is str
            for name in ("provider", "response_id", "status", "text")
        ):
            _raise_result("success_shape")
        if result.request_id is not None and type(result.request_id) is not str:
            _raise_result("success_shape")
        if type(result.text_parts) is not tuple or any(
            type(part) is not str for part in result.text_parts
        ):
            _raise_result("success_shape")
        return
    if type(result) is ModelInvocationFailure:
        if type(result.provider) is not str or type(result.message) is not str:
            _raise_result("failure_shape")
        if (
            type(result.category) is not str
            or result.category not in _FAILURE_CATEGORIES
        ):
            _raise_result("failure_category")
        if result.request_id is not None and type(result.request_id) is not str:
            _raise_result("failure_shape")
        if result.status_code is not None and type(result.status_code) is not int:
            _raise_result("failure_shape")
        for value in (result.provider_error_type, result.provider_error_code):
            if value is not None and type(value) is not str:
                _raise_result("failure_shape")
        _validate_diagnostics(result.response_diagnostics)
        return
    _raise_result("result_type")


def _validate_diagnostics(value: object) -> None:
    if value is None:
        return
    if type(value) is not ModelInvocationFailureDiagnostics:
        _raise_result("diagnostics_type")
    if (
        type(value.status_code) is not int
        or type(value.body_length) is not int
        or value.body_length < 0
        or (value.content_type is not None and type(value.content_type) is not str)
        or type(value.body_kind) is not str
        or value.body_kind not in _RESPONSE_BODY_KINDS
    ):
        _raise_result("diagnostics_shape")


def _validate_attempt_claim_for_result(value: object) -> None:
    if type(value) is not PublicationRegenerationAttemptClaim:
        _raise_result("claim_type")
    try:
        publication_regeneration_attempt_claim_canonical_bytes(value)
    except (PublicationRegenerationAttemptClaimError, TypeError, ValueError):
        _raise_result("claim")


def _validate_publication_regeneration_result_record(
    record: PublicationRegenerationResultRecord,
) -> None:
    if type(record) is not PublicationRegenerationResultRecord:
        _raise_result("record_type")
    if (
        type(record.schema_version) is not str
        or record.schema_version != _RESULT_SCHEMA_VERSION
    ):
        _raise_result("schema_version")
    _validate_attempt_claim_for_result(record.attempt_claim)
    for value, classification in (
        (record.attempt_claim_sha256, "claim_digest"),
        (record.regeneration_plan_sha256, "plan_digest"),
        (record.source_audit_sha256, "source_audit_digest"),
        (record.execution_target_sha256, "target_digest"),
        (record.invocation_request_sha256, "request_digest"),
        (record.result_sha256, "result_digest"),
    ):
        _validate_digest(value, classification)
    if type(record.regeneration_id) is not str or not record.regeneration_id:
        _raise_result("regeneration_id")
    if type(record.provider) is not str or not record.provider:
        _raise_result("provider")
    if type(record.outcome) is not str or record.outcome not in {"success", "failure"}:
        _raise_result("outcome")
    if (
        record.business_output_sha256 is not None
        and type(record.business_output_sha256) is not str
    ):
        _raise_result("business_output_digest")
    _validate_model_invocation_result(record.result)
    claim_digest = publication_regeneration_attempt_claim_digest(record.attempt_claim)
    if record.attempt_claim_sha256 != claim_digest:
        _raise_result("claim_binding")
    if (
        record.regeneration_id != record.attempt_claim.regeneration_id
        or record.regeneration_plan_sha256
        != record.attempt_claim.regeneration_plan_sha256
        or record.source_audit_sha256 != record.attempt_claim.source_audit_sha256
        or record.provider != record.attempt_claim.provider
        or record.execution_target_sha256
        != record.attempt_claim.execution_target_sha256
        or record.invocation_request_sha256
        != record.attempt_claim.invocation_request_sha256
    ):
        _raise_result("claim_binding")
    expected_outcome = (
        "success" if type(record.result) is ModelInvocationSuccess else "failure"
    )
    if record.outcome != expected_outcome:
        _raise_result("outcome")
    if record.result.provider != record.attempt_claim.provider:
        _raise_result("provider_binding")
    result_digest = publication_regeneration_result_digest(record.result)
    if record.result_sha256 != result_digest:
        _raise_result("result_binding")
    if type(record.result) is ModelInvocationSuccess:
        if type(record.business_output_sha256) is not str:
            _raise_result("business_output_digest")
        _validate_digest(record.business_output_sha256, "business_output_digest")
        expected_output_digest = sha256(record.result.text.encode("utf-8")).hexdigest()
        if record.business_output_sha256 != expected_output_digest:
            _raise_result("business_output_binding")
    elif record.business_output_sha256 is not None:
        _raise_result("business_output_binding")


def _validate_digest(value: object, classification: str) -> None:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        _raise_result(classification)


def _validate_result_persistence_path(path: object) -> None:
    if type(path) is not type(Path()):
        _raise_persistence("path_type")
    try:
        parent = path.parent
        if not parent.exists() or not parent.is_dir():
            _raise_persistence("parent")
        if path.is_dir() or path.is_symlink():
            _raise_persistence("target")
    except OSError:
        _raise_persistence("target")


def _validate_result_load_path(path: object) -> None:
    if type(path) is not type(Path()):
        _raise_load("path_type")
    try:
        if path.is_dir() or path.is_symlink():
            _raise_load("target")
    except OSError:
        _raise_load("target")


def _persist_existing_result(
    path: Path,
    record: PublicationRegenerationResultRecord,
    contents: bytes,
) -> PublicationRegenerationResultRecord:
    try:
        existing = load_publication_regeneration_result(path)
    except Exception:
        _raise_conflict()
    if existing != record:
        _raise_conflict()
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
        _fsync_result_directory(path.parent)
    except OSError:
        _raise_persistence("ambiguous")
    return record


def _fsync_result_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _reject_duplicate_result_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateResultKeyError
        result[key] = value
    return result


class _DuplicateResultKeyError(ValueError):
    pass


def _reject_nonstandard_json_constant(constant: str) -> NoReturn:
    raise ValueError(constant)


def _parse_publication_regeneration_result_record(
    value: object,
) -> PublicationRegenerationResultRecord:
    if type(value) is not dict or frozenset(value) != _RESULT_RECORD_KEYS:
        _raise_load("keys")
    claim = _parse_claim(value["attempt_claim"])
    result = _parse_result(value["result"])
    try:
        return PublicationRegenerationResultRecord(
            schema_version=value["schema_version"],
            attempt_claim=claim,
            attempt_claim_sha256=value["attempt_claim_sha256"],
            regeneration_id=value["regeneration_id"],
            regeneration_plan_sha256=value["regeneration_plan_sha256"],
            source_audit_sha256=value["source_audit_sha256"],
            provider=value["provider"],
            execution_target_sha256=value["execution_target_sha256"],
            invocation_request_sha256=value["invocation_request_sha256"],
            outcome=value["outcome"],
            result=result,
            result_sha256=value["result_sha256"],
            business_output_sha256=value["business_output_sha256"],
        )
    except PublicationRegenerationResultError:
        _raise_load("record")
    except (KeyError, TypeError, ValueError):
        _raise_load("record")


def _parse_claim(value: object) -> PublicationRegenerationAttemptClaim:
    if type(value) is not dict or frozenset(value) != _CLAIM_KEYS:
        _raise_load("claim_keys")
    try:
        claim = PublicationRegenerationAttemptClaim(**value)
        publication_regeneration_attempt_claim_canonical_bytes(claim)
        return claim
    except (PublicationRegenerationAttemptClaimError, TypeError, ValueError):
        _raise_load("claim")


def _parse_result(value: object) -> ModelInvocationResult:
    if type(value) is not dict or type(value.get("kind")) is not str:
        _raise_load("result")
    kind = value["kind"]
    if kind == "success":
        if frozenset(value) != _SUCCESS_RESULT_KEYS:
            _raise_load("result_keys")
        text_parts = value["text_parts"]
        if type(text_parts) is not list or any(
            type(part) is not str for part in text_parts
        ):
            _raise_load("result")
        try:
            result = ModelInvocationSuccess(
                provider=value["provider"],
                response_id=value["response_id"],
                request_id=value["request_id"],
                status=value["status"],
                text_parts=tuple(text_parts),
                text=value["text"],
            )
            _validate_model_invocation_result(result)
            return result
        except (PublicationRegenerationResultError, KeyError, TypeError, ValueError):
            _raise_load("result")
    if kind == "failure":
        if frozenset(value) != _FAILURE_RESULT_KEYS:
            _raise_load("result_keys")
        diagnostics = _parse_diagnostics(value["response_diagnostics"])
        try:
            result = ModelInvocationFailure(
                provider=value["provider"],
                category=value["category"],
                message=value["message"],
                request_id=value["request_id"],
                status_code=value["status_code"],
                provider_error_type=value["provider_error_type"],
                provider_error_code=value["provider_error_code"],
                response_diagnostics=diagnostics,
            )
            _validate_model_invocation_result(result)
            return result
        except (PublicationRegenerationResultError, KeyError, TypeError, ValueError):
            _raise_load("result")
    _raise_load("result_kind")


def _parse_diagnostics(value: object) -> ModelInvocationFailureDiagnostics | None:
    if value is None:
        return None
    if type(value) is not dict or frozenset(value) != _DIAGNOSTICS_KEYS:
        _raise_load("diagnostics")
    try:
        diagnostics = ModelInvocationFailureDiagnostics(
            status_code=value["status_code"],
            content_type=value["content_type"],
            body_length=value["body_length"],
            body_kind=value["body_kind"],
        )
        _validate_diagnostics(diagnostics)
        return diagnostics
    except (PublicationRegenerationResultError, KeyError, TypeError, ValueError):
        _raise_load("diagnostics")


def _raise_result(classification: str) -> NoReturn:
    raise PublicationRegenerationResultError(classification) from None


def _raise_persistence(classification: str) -> NoReturn:
    raise PublicationRegenerationResultPersistenceError(classification) from None


def _raise_conflict() -> NoReturn:
    raise PublicationRegenerationResultConflictError("conflict") from None


def _raise_load(classification: str) -> NoReturn:
    raise PublicationRegenerationResultLoadError(classification) from None


__all__ = [
    "PublicationRegenerationResultConflictError",
    "PublicationRegenerationResultError",
    "PublicationRegenerationResultFailureDetail",
    "PublicationRegenerationResultLoadError",
    "PublicationRegenerationResultPersistenceError",
    "PublicationRegenerationResultRecord",
    "build_publication_regeneration_result_record",
    "load_publication_regeneration_result",
    "persist_publication_regeneration_result",
    "preflight_publication_regeneration_result_path",
    "publication_regeneration_result_canonical_bytes",
    "publication_regeneration_result_digest",
    "publication_regeneration_result_record_canonical_bytes",
    "publication_regeneration_result_record_digest",
    "serialize_publication_regeneration_result_canonical",
    "serialize_publication_regeneration_result_record_canonical",
]
