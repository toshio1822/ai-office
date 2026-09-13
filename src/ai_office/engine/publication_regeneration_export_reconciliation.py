"""Read-only reconciliation of one durable export receipt and one file.

Phase 275 consumes the canonical Phase 274 receipt evidence and one explicit
caller-supplied output path.  It does not export, repair, adopt, persist, or
publish anything.  The result is an in-memory observation only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from ai_office.engine.publication_regeneration_export import (
    PublicationRegenerationExportReceipt,
)
from ai_office.engine.publication_regeneration_export_receipt import (
    load_publication_regeneration_export_receipt,
    publication_regeneration_export_receipt_digest,
)

_RECONCILIATION_ERROR_MESSAGE = (
    "publication regeneration export reconciliation failed"
)
_RECONCILIATION_SCHEMA_VERSION = (
    "publication-regeneration-export-reconciliation.v1"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_STATUS_VALUES = frozenset(
    {"matched", "missing", "content_mismatch"}
)

Classification = Literal["path_type", "receipt", "target", "read", "result"]
ReconciliationStatus = Literal["matched", "missing", "content_mismatch"]


@dataclass(frozen=True)
class PublicationRegenerationExportReconciliationFailureDetail:
    """Safe classification for one rejected reconciliation observation."""

    classification: Classification


class PublicationRegenerationExportReconciliationError(ValueError):
    """Raised when a reconciliation observation cannot be safely completed."""

    def __init__(self, classification: Classification = "result") -> None:
        super().__init__(_RECONCILIATION_ERROR_MESSAGE)
        self.detail = PublicationRegenerationExportReconciliationFailureDetail(
            classification
        )


@dataclass(frozen=True)
class PublicationRegenerationExportReconciliation:
    """One immutable in-memory comparison of receipt evidence and file bytes."""

    schema_version: Literal[
        "publication-regeneration-export-reconciliation.v1"
    ]
    regeneration_id: str
    receipt_sha256: str
    status: ReconciliationStatus
    expected_business_output_sha256: str
    expected_output_byte_length: int
    observed_business_output_sha256: str | None
    observed_output_byte_length: int | None

    def __post_init__(self) -> None:
        _validate_result(self)


def reconcile_publication_regeneration_export(
    *,
    receipt_path: Path,
    output_path: Path,
) -> PublicationRegenerationExportReconciliation:
    """Compare one exact Phase 274 receipt with one explicit output file.

    The receipt loader and receipt digest helper are each called once.  The
    output file is never written and its bytes are read at most once.  An
    absent output is a normal ``missing`` result; all other unsafe observation
    failures use the fixed reconciliation error surface.
    """
    _validate_output_path_type(output_path)

    try:
        receipt = load_publication_regeneration_export_receipt(receipt_path)
    except Exception:
        _raise_reconciliation("receipt")
    if type(receipt) is not PublicationRegenerationExportReceipt:
        _raise_reconciliation("receipt")

    try:
        receipt_sha256 = publication_regeneration_export_receipt_digest(receipt)
        regeneration_id = receipt.regeneration_id
        expected_business_output_sha256 = receipt.business_output_sha256
        expected_output_byte_length = receipt.output_byte_length
    except Exception:
        _raise_reconciliation("receipt")

    try:
        output_bytes = _read_output_bytes(output_path)
    except PublicationRegenerationExportReconciliationError:
        raise
    except Exception:
        _raise_reconciliation("read")
    if output_bytes is None:
        return PublicationRegenerationExportReconciliation(
            schema_version=_RECONCILIATION_SCHEMA_VERSION,
            regeneration_id=regeneration_id,
            receipt_sha256=receipt_sha256,
            status="missing",
            expected_business_output_sha256=expected_business_output_sha256,
            expected_output_byte_length=expected_output_byte_length,
            observed_business_output_sha256=None,
            observed_output_byte_length=None,
        )

    try:
        observed_business_output_sha256 = sha256(output_bytes).hexdigest()
        observed_output_byte_length = len(output_bytes)
        status: ReconciliationStatus = (
            "matched"
            if (
                observed_business_output_sha256
                == expected_business_output_sha256
                and observed_output_byte_length == expected_output_byte_length
            )
            else "content_mismatch"
        )
        return PublicationRegenerationExportReconciliation(
            schema_version=_RECONCILIATION_SCHEMA_VERSION,
            regeneration_id=regeneration_id,
            receipt_sha256=receipt_sha256,
            status=status,
            expected_business_output_sha256=expected_business_output_sha256,
            expected_output_byte_length=expected_output_byte_length,
            observed_business_output_sha256=observed_business_output_sha256,
            observed_output_byte_length=observed_output_byte_length,
        )
    except PublicationRegenerationExportReconciliationError:
        raise
    except Exception:
        _raise_reconciliation("result")


def _validate_output_path_type(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_reconciliation("path_type")


def _read_output_bytes(path: Path) -> bytes | None:
    """Read the target once after rejecting directories, links, and specials."""
    try:
        if path.is_symlink() or path.is_dir():
            _raise_reconciliation("target")
        if path.exists() and not path.is_file():
            _raise_reconciliation("target")
    except PublicationRegenerationExportReconciliationError:
        raise
    except Exception:
        _raise_reconciliation("target")

    try:
        contents = path.read_bytes()
    except FileNotFoundError:
        return None
    except Exception:
        _raise_reconciliation("read")

    if type(contents) is not bytes:
        _raise_reconciliation("read")
    return contents


def _validate_result(
    result: PublicationRegenerationExportReconciliation,
) -> None:
    if (
        type(result.schema_version) is not str
        or result.schema_version != _RECONCILIATION_SCHEMA_VERSION
    ):
        _raise_reconciliation("result")
    if type(result.regeneration_id) is not str or not result.regeneration_id:
        _raise_reconciliation("result")
    if not _is_sha256(result.receipt_sha256):
        _raise_reconciliation("result")
    if type(result.status) is not str or result.status not in _STATUS_VALUES:
        _raise_reconciliation("result")
    if not _is_sha256(result.expected_business_output_sha256):
        _raise_reconciliation("result")
    if not _is_non_negative_int(result.expected_output_byte_length):
        _raise_reconciliation("result")

    if result.status == "missing":
        if (
            result.observed_business_output_sha256 is not None
            or result.observed_output_byte_length is not None
        ):
            _raise_reconciliation("result")
        return

    if not _is_sha256(result.observed_business_output_sha256):
        _raise_reconciliation("result")
    if not _is_non_negative_int(result.observed_output_byte_length):
        _raise_reconciliation("result")

    if result.status == "matched":
        if (
            result.observed_business_output_sha256
            != result.expected_business_output_sha256
            or result.observed_output_byte_length
            != result.expected_output_byte_length
        ):
            _raise_reconciliation("result")
        return

    if (
        result.observed_business_output_sha256
        == result.expected_business_output_sha256
        and result.observed_output_byte_length
        == result.expected_output_byte_length
    ):
        _raise_reconciliation("result")


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _is_non_negative_int(value: object) -> bool:
    return type(value) is int and type(value) is not bool and value >= 0


def _raise_reconciliation(classification: Classification) -> NoReturn:
    raise PublicationRegenerationExportReconciliationError(
        classification
    ) from None


__all__ = [
    "PublicationRegenerationExportReconciliation",
    "PublicationRegenerationExportReconciliationError",
    "PublicationRegenerationExportReconciliationFailureDetail",
    "reconcile_publication_regeneration_export",
]
