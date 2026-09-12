"""Provider-free publication projection from immutable regeneration evidence.

Phase 270 reads one strict Phase 269 readiness record and one strict Phase 267
result record.  It exposes the exact regenerated success text only when the
readiness evidence is ``ready`` and every cross-lineage identity is exact.  It
never reassesses readiness, writes artifacts, invokes a provider, or mutates
any workflow or evidence lineage.
"""

from __future__ import annotations

import json
import re
from dataclasses import InitVar, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from ai_office.engine.publication_regeneration_readiness_record import (
    PublicationRegenerationReadinessRecord,
    load_publication_regeneration_readiness_record,
)
from ai_office.engine.publication_regeneration_result import (
    PublicationRegenerationResultRecord,
    load_publication_regeneration_result,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess

_PROJECTION_ERROR_MESSAGE = "publication regeneration projection is invalid"
_PROJECTION_SCHEMA_VERSION = "publication-regeneration-projection.v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_READINESS_VALUES = frozenset(
    {
        "ready",
        "insufficient_evidence",
        "stale_or_inconsistent",
        "result_failure",
    }
)
_EXPECTED_REASON_CODES = {
    "ready": (),
    "insufficient_evidence": ("claim_contract_missing",),
    "stale_or_inconsistent": ("claim_contract_mismatch",),
    "result_failure": ("regeneration_result_failure",),
}

_PROJECTION_CONSTRUCTION_TOKEN = object()


@dataclass(frozen=True)
class PublicationRegenerationProjectionFailureDetail:
    """Safe classification for an invalid publication projection."""

    classification: str


class PublicationRegenerationProjectionError(ValueError):
    """Raised when immutable evidence cannot form an exact projection."""

    def __init__(self, classification: str = "projection") -> None:
        super().__init__(_PROJECTION_ERROR_MESSAGE)
        self.detail = PublicationRegenerationProjectionFailureDetail(
            classification
        )


@dataclass(frozen=True)
class PublicationRegenerationProjection:
    """Exact read-side publication projection for one regeneration lineage."""

    schema_version: Literal["publication-regeneration-projection.v1"]
    regeneration_id: str
    readiness_record_sha256: str
    readiness: Literal[
        "ready",
        "insufficient_evidence",
        "stale_or_inconsistent",
        "result_failure",
    ]
    reason_codes: tuple[str, ...]
    result_record_sha256: str
    source_audit_sha256: str
    publishable: bool
    business_output_sha256: str | None
    business_output_text: str | None
    _construction_token: InitVar[object | None] = None

    def __post_init__(self, _construction_token: object) -> None:
        if (
            _construction_token is not _PROJECTION_CONSTRUCTION_TOKEN
            and self.readiness == "ready"
        ):
            _raise_projection("construction")
        _validate_projection(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical projection JSON."""
        return publication_regeneration_projection_digest(self)


def project_publication_regeneration_output(
    *,
    readiness_record_path: Path,
    result_path: Path,
) -> PublicationRegenerationProjection:
    """Project exact regenerated output from two strict immutable sidecars.

    The Phase 269 readiness record is loaded first, followed by the Phase 267
    result record.  The two records are then cross-bound without invoking the
    Phase 268 assessor or reading any mutable workflow artifact.  A non-ready
    record produces a projection with no output text or digest.
    """
    readiness_record = _load_readiness_record(readiness_record_path)
    result_record = _load_result_record(result_path)
    _validate_cross_binding(readiness_record, result_record)

    readiness = readiness_record.readiness
    if readiness == "ready":
        result = result_record.result
        if type(result) is not ModelInvocationSuccess:
            _raise_projection("outcome_binding")
        business_output_sha256 = _text_digest(result.text)
        if (
            business_output_sha256 != result_record.business_output_sha256
            or business_output_sha256
            != readiness_record.assessment.business_output_sha256
        ):
            _raise_projection("business_output_binding")
        business_output_text = result.text
        publishable = True
    else:
        business_output_sha256 = None
        business_output_text = None
        publishable = False

    try:
        return PublicationRegenerationProjection(
            schema_version=_PROJECTION_SCHEMA_VERSION,
            regeneration_id=readiness_record.regeneration_id,
            readiness_record_sha256=readiness_record.digest,
            readiness=readiness,
            reason_codes=readiness_record.reason_codes,
            result_record_sha256=result_record.digest,
            source_audit_sha256=readiness_record.source_audit_sha256,
            publishable=publishable,
            business_output_sha256=business_output_sha256,
            business_output_text=business_output_text,
            _construction_token=_PROJECTION_CONSTRUCTION_TOKEN,
        )
    except PublicationRegenerationProjectionError:
        raise
    except (TypeError, UnicodeError, ValueError):
        _raise_projection("projection")


def serialize_publication_regeneration_projection_canonical(
    projection: PublicationRegenerationProjection,
) -> str:
    """Serialize one projection as compact deterministic JSON."""
    _validate_projection(projection)
    try:
        return json.dumps(
            {
                "business_output_sha256": projection.business_output_sha256,
                "business_output_text": projection.business_output_text,
                "publishable": projection.publishable,
                "readiness": projection.readiness,
                "readiness_record_sha256": projection.readiness_record_sha256,
                "reason_codes": list(projection.reason_codes),
                "regeneration_id": projection.regeneration_id,
                "result_record_sha256": projection.result_record_sha256,
                "schema_version": projection.schema_version,
                "source_audit_sha256": projection.source_audit_sha256,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, UnicodeError, ValueError):
        _raise_projection("serialization")


def publication_regeneration_projection_canonical_bytes(
    projection: PublicationRegenerationProjection,
) -> bytes:
    """Return canonical projection JSON encoded as exact UTF-8 bytes."""
    try:
        return serialize_publication_regeneration_projection_canonical(
            projection
        ).encode("utf-8")
    except UnicodeError:
        _raise_projection("encoding")


def publication_regeneration_projection_digest(
    projection: PublicationRegenerationProjection,
) -> str:
    """Return the SHA-256 identity of canonical projection JSON."""
    return sha256(
        publication_regeneration_projection_canonical_bytes(projection)
    ).hexdigest()


def _load_readiness_record(path: Path) -> PublicationRegenerationReadinessRecord:
    try:
        record = load_publication_regeneration_readiness_record(path)
    except Exception:
        _raise_projection("readiness_load")
    if type(record) is not PublicationRegenerationReadinessRecord:
        _raise_projection("readiness_type")
    return record


def _load_result_record(path: Path) -> PublicationRegenerationResultRecord:
    try:
        record = load_publication_regeneration_result(path)
    except Exception:
        _raise_projection("result_load")
    if type(record) is not PublicationRegenerationResultRecord:
        _raise_projection("result_type")
    return record


def _validate_cross_binding(
    readiness_record: PublicationRegenerationReadinessRecord,
    result_record: PublicationRegenerationResultRecord,
) -> None:
    assessment = readiness_record.assessment
    if (
        readiness_record.regeneration_id != result_record.regeneration_id
        or assessment.regeneration_id != result_record.regeneration_id
    ):
        _raise_projection("regeneration_id_binding")

    result_digest = result_record.digest
    if (
        readiness_record.result_record_sha256 != result_digest
        or assessment.result_record_sha256 != result_digest
    ):
        _raise_projection("result_record_binding")

    if (
        readiness_record.source_audit_sha256 != result_record.source_audit_sha256
        or assessment.source_audit_sha256 != result_record.source_audit_sha256
    ):
        _raise_projection("source_audit_binding")

    expected_outcome = (
        "success"
        if type(result_record.result) is ModelInvocationSuccess
        else "failure"
        if type(result_record.result) is ModelInvocationFailure
        else None
    )
    if (
        expected_outcome is None
        or result_record.outcome != expected_outcome
        or assessment.outcome != expected_outcome
    ):
        _raise_projection("outcome_binding")

    if expected_outcome == "failure":
        if (
            readiness_record.readiness != "result_failure"
            or assessment.business_output_sha256 is not None
            or result_record.business_output_sha256 is not None
        ):
            _raise_projection("outcome_binding")
        return

    if (
        assessment.business_output_sha256 is None
        or result_record.business_output_sha256 is None
        or assessment.business_output_sha256 != result_record.business_output_sha256
    ):
        _raise_projection("business_output_binding")


def _validate_projection(
    projection: PublicationRegenerationProjection,
) -> None:
    if type(projection) is not PublicationRegenerationProjection:
        _raise_projection("projection_type")
    if (
        type(projection.schema_version) is not str
        or projection.schema_version != _PROJECTION_SCHEMA_VERSION
    ):
        _raise_projection("schema_version")
    if type(projection.regeneration_id) is not str or not projection.regeneration_id:
        _raise_projection("regeneration_id")
    for value, classification in (
        (projection.readiness_record_sha256, "readiness_record_digest"),
        (projection.result_record_sha256, "result_record_digest"),
        (projection.source_audit_sha256, "source_audit_digest"),
    ):
        _validate_digest(value, classification)
    if (
        type(projection.readiness) is not str
        or projection.readiness not in _READINESS_VALUES
    ):
        _raise_projection("readiness")
    if type(projection.reason_codes) is not tuple or any(
        type(reason) is not str or not reason for reason in projection.reason_codes
    ):
        _raise_projection("reason_codes")
    if projection.reason_codes != _EXPECTED_REASON_CODES[projection.readiness]:
        _raise_projection("reason_codes")
    if type(projection.publishable) is not bool:
        _raise_projection("publishable_type")
    if projection.publishable is not (projection.readiness == "ready"):
        _raise_projection("publishable_binding")

    if projection.readiness == "ready":
        if type(projection.business_output_text) is not str:
            _raise_projection("business_output_text")
        _validate_digest(
            projection.business_output_sha256,
            "business_output_digest",
        )
        if _text_digest(projection.business_output_text) != (
            projection.business_output_sha256
        ):
            _raise_projection("business_output_binding")
    elif (
        projection.business_output_text is not None
        or projection.business_output_sha256 is not None
    ):
        _raise_projection("non_publishable_output")


def _validate_digest(value: object, classification: str) -> None:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        _raise_projection(classification)


def _text_digest(value: str) -> str:
    if type(value) is not str:
        _raise_projection("business_output_type")
    try:
        return sha256(value.encode("utf-8")).hexdigest()
    except UnicodeError:
        _raise_projection("business_output_encoding")


def _raise_projection(classification: str) -> NoReturn:
    raise PublicationRegenerationProjectionError(classification) from None


__all__ = [
    "PublicationRegenerationProjection",
    "PublicationRegenerationProjectionError",
    "PublicationRegenerationProjectionFailureDetail",
    "project_publication_regeneration_output",
    "publication_regeneration_projection_canonical_bytes",
    "publication_regeneration_projection_digest",
    "serialize_publication_regeneration_projection_canonical",
]
