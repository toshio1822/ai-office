"""Provider-free durable export of one ready Phase 270 projection.

Phase 272 creates only the caller-supplied export target.  It never loads
predecessor evidence directly, changes the regeneration lineage, or performs
an external publication action.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from ai_office.engine.publication_regeneration_projection import (
    PublicationRegenerationProjection,
    project_publication_regeneration_output,
    publication_regeneration_projection_digest,
)

_EXPORT_ERROR_MESSAGE = "publication regeneration export is invalid"
_EXPORT_SCHEMA_VERSION = "publication-regeneration-export-receipt.v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class PublicationRegenerationExportFailureDetail:
    """Safe classification for one rejected or ambiguous export."""

    classification: str


class PublicationRegenerationExportError(ValueError):
    """Raised when a ready projection cannot be exported safely."""

    def __init__(self, classification: str = "export") -> None:
        super().__init__(_EXPORT_ERROR_MESSAGE)
        self.detail = PublicationRegenerationExportFailureDetail(classification)


@dataclass(frozen=True)
class PublicationRegenerationExportReceipt:
    """In-memory receipt for one exact, durable local export."""

    schema_version: Literal["publication-regeneration-export-receipt.v1"]
    regeneration_id: str
    projection_sha256: str
    readiness_record_sha256: str
    result_record_sha256: str
    source_audit_sha256: str
    business_output_sha256: str
    output_byte_length: int

    def __post_init__(self) -> None:
        _validate_receipt(self)


def export_publication_regeneration_output(
    *,
    output_path: Path,
    readiness_record_path: Path,
    result_path: Path,
) -> PublicationRegenerationExportReceipt:
    """Durably create one explicit file from one ready Phase 270 projection.

    The output path is fully preflighted before Phase 270 is called.  The
    exclusive create and both fsync boundaries are intentionally conservative:
    once creation succeeds, any later failure leaves the artifact in place and
    raises the fixed export error without retrying or returning a receipt.
    """
    _preflight_output_path(output_path)

    try:
        projection = project_publication_regeneration_output(
            readiness_record_path=readiness_record_path,
            result_path=result_path,
        )
    except Exception:
        _raise_export("projection")

    output_bytes, projection_sha256 = _validate_exportable_projection(projection)

    try:
        handle = output_path.open("xb")
    except FileExistsError:
        _raise_export("target_exists")
    except OSError:
        _raise_export("create")

    try:
        with handle:
            written = handle.write(output_bytes)
            if written != len(output_bytes):
                raise OSError("short export write")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_export_directory(output_path.parent)
    except Exception:
        _raise_export("ambiguous")

    return PublicationRegenerationExportReceipt(
        schema_version=_EXPORT_SCHEMA_VERSION,
        regeneration_id=projection.regeneration_id,
        projection_sha256=projection_sha256,
        readiness_record_sha256=projection.readiness_record_sha256,
        result_record_sha256=projection.result_record_sha256,
        source_audit_sha256=projection.source_audit_sha256,
        business_output_sha256=projection.business_output_sha256,
        output_byte_length=len(output_bytes),
    )


def _preflight_output_path(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_export("path_type")
    try:
        if not path.parent.exists() or not path.parent.is_dir():  # type: ignore[union-attr]
            _raise_export("parent")
        if path.is_dir() or path.is_symlink():  # type: ignore[union-attr]
            _raise_export("target")
        if path.exists():  # type: ignore[union-attr]
            _raise_export("target_exists")
    except OSError:
        _raise_export("target")


def _validate_exportable_projection(
    projection: object,
) -> tuple[bytes, str]:
    if type(projection) is not PublicationRegenerationProjection:
        _raise_export("projection_type")
    if projection.readiness != "ready" or projection.publishable is not True:
        _raise_export("not_publishable")
    try:
        if type(projection.business_output_text) is not str:
            _raise_export("projection")
        output_bytes = projection.business_output_text.encode("utf-8")
        business_output_sha256 = sha256(output_bytes).hexdigest()
        if business_output_sha256 != projection.business_output_sha256:
            _raise_export("projection")
        projection_sha256 = publication_regeneration_projection_digest(projection)
    except Exception:
        _raise_export("projection")
    return output_bytes, projection_sha256


def _validate_receipt(receipt: PublicationRegenerationExportReceipt) -> None:
    if type(receipt) is not PublicationRegenerationExportReceipt:
        _raise_export("receipt_type")
    if (
        type(receipt.schema_version) is not str
        or receipt.schema_version != _EXPORT_SCHEMA_VERSION
    ):
        _raise_export("receipt_schema_version")
    if type(receipt.regeneration_id) is not str or not receipt.regeneration_id:
        _raise_export("receipt_regeneration_id")
    for value, classification in (
        (receipt.projection_sha256, "receipt_projection_digest"),
        (receipt.readiness_record_sha256, "receipt_readiness_digest"),
        (receipt.result_record_sha256, "receipt_result_digest"),
        (receipt.source_audit_sha256, "receipt_source_audit_digest"),
        (receipt.business_output_sha256, "receipt_business_output_digest"),
    ):
        if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
            _raise_export(classification)
    if (
        type(receipt.output_byte_length) is not int
        or type(receipt.output_byte_length) is bool
        or receipt.output_byte_length < 0
    ):
        _raise_export("receipt_output_length")


def _fsync_export_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _raise_export(classification: str) -> NoReturn:
    raise PublicationRegenerationExportError(classification) from None


__all__ = [
    "PublicationRegenerationExportError",
    "PublicationRegenerationExportFailureDetail",
    "PublicationRegenerationExportReceipt",
    "export_publication_regeneration_output",
]
