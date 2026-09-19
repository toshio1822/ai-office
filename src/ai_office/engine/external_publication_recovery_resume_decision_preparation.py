"""Durable decision-bound preparation for recovery-resume lineage (Phase 301).

Phase 301 is a read-only Phase 299 routing consumer plus one append-only,
immutable preparation boundary above the exact Phase 300 recovery-resume
operator decision.  The caller supplies only the exact Phase 295 binding path.
Phase 299 supplies the current recovery provenance; Phase 300 supplies the
operator authority and is consumed only through its strict loader and digest
helper.

A completed Phase 298 route is terminal and cannot produce a preparation.  A
Phase 300 ``stop`` decision is also terminal and cannot produce a preparation.
Only an exact ``authorize_resume_preparation`` decision can create the new
record.  The record is bound directly to that decision digest and does not
create an operation intent, binding, start authorization, start marker, or
execution artifact.
"""

from __future__ import annotations

import errno
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from .external_publication_operation_start import ExternalPublicationOperationStartError
from .external_publication_recovery_resume_decision import (
    ExternalPublicationRecoveryResumeDecision,
    ExternalPublicationRecoveryResumeDecisionError,
    external_publication_recovery_resume_decision_digest,
    load_external_publication_recovery_resume_decision,
)
from .external_publication_recovery_resume_intent_binding import (
    ExternalPublicationRecoveryResumeIntentBindingError,
)
from .external_publication_recovery_resume_outcome import (
    ExternalPublicationRecoveryResumeOutcome,
    ExternalPublicationRecoveryResumeOutcomeError,
)
from .external_publication_recovery_resume_outcome_routing import (
    ExternalPublicationRecoveryResumeDecisionRequired,
    ExternalPublicationRecoveryResumeOutcomeRoutingError,
    route_external_publication_recovery_resume_outcome,
)
from .external_publication_recovery_resume_start_authorization import (
    ExternalPublicationRecoveryResumeStartAuthorizationError,
)

_PREPARATION_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation is blocked"
)
_PERSISTENCE_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation persistence failed"
)
_LOAD_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation could not be loaded"
)
_PREPARATION_SCHEMA_VERSION = (
    "external-publication-recovery-resume-decision-preparation.v1"
)
_PREPARATION_KEYS = frozenset(
    {
        "decision",
        "operation_intent_sha256",
        "operation_start_sha256",
        "previous_recovery_kind",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "recovery_kind",
        "recovery_resume_decision_sha256",
        "recovery_resume_outcome_sha256",
        "result_kind",
        "result_sha256",
        "resume_intent_binding_sha256",
        "resume_start_authorization_sha256",
        "schema_version",
        "source_operation",
        "state",
        "target_operation",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_MAX_PREPARATION_BYTES = 4096
_SOURCE_OPERATIONS = frozenset({"fresh", "resume"})
_RECOVERY_KINDS = frozenset({"already_acquired", "reconciliation_mismatch"})
_RESULT_KINDS = frozenset({"reconciliation", "none"})
_DECISION = "authorize_resume_preparation"
_OPERATION = "resume"
_STATE = "prepared"
_DECIDED_STATE = "decided"
_NONE_RESULT_KIND = "none"
_RECONCILIATION_RESULT_KIND = "reconciliation"
_ALREADY_ACQUIRED_RECOVERY_KIND = "already_acquired"
_MISMATCH_RECOVERY_KIND = "reconciliation_mismatch"
_DECISION_FILENAME_PREFIX = "external-publication-recovery-resume-decision-"
_PREPARATION_FILENAME_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-"
)
_FILENAME_SUFFIX = ".json"

Classification = Literal[
    "configuration",
    "path_type",
    "dependency_error",
    "predecessor_contract",
    "predecessor_lineage",
    "preparation_not_required",
    "preparation_not_authorized",
    "decision_digest",
    "parent",
    "target",
    "create",
    "ambiguous",
    "conflict",
    "load",
    "parse",
    "size",
    "keys",
    "noncanonical",
    "serialization",
    "encoding",
]

Phase299Function = Callable[..., object]
DecisionLoader = Callable[[Path], object]
DecisionDigestFunction = Callable[[ExternalPublicationRecoveryResumeDecision], object]

_KNOWN_PHASE299_ERRORS = (
    ExternalPublicationRecoveryResumeOutcomeRoutingError,
    ExternalPublicationRecoveryResumeIntentBindingError,
    ExternalPublicationRecoveryResumeStartAuthorizationError,
    ExternalPublicationRecoveryResumeOutcomeError,
    ExternalPublicationOperationStartError,
)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionPreparationFailureDetail:
    """Detail-safe classification for one Phase 301 failure."""

    classification: Classification


class ExternalPublicationRecoveryResumeDecisionPreparationError(ValueError):
    """Raised when a Phase 301 preparation request or record is not exact."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_PREPARATION_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionPreparationFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError(
    ExternalPublicationRecoveryResumeDecisionPreparationError
):
    """Raised when a path, dependency, decision, or lineage is incompatible."""


class ExternalPublicationRecoveryResumeDecisionPreparationPersistenceError(
    ExternalPublicationRecoveryResumeDecisionPreparationError
):
    """Raised when Phase 301 durability cannot be proven."""

    def __init__(self, classification: Classification = "target") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionPreparationFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeDecisionPreparationConflictError(
    ExternalPublicationRecoveryResumeDecisionPreparationPersistenceError
):
    """Raised when an occupied target is not the exact canonical record."""


class ExternalPublicationRecoveryResumeDecisionPreparationLoadError(
    ExternalPublicationRecoveryResumeDecisionPreparationError
):
    """Raised when a preparation sidecar is not an exact canonical record."""

    def __init__(self, classification: Classification = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionPreparationFailureDetail(
            classification
        )


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionPreparation:
    """Immutable, secret-free Phase 301 preparation evidence."""

    schema_version: Literal[
        "external-publication-recovery-resume-decision-preparation.v1"
    ]
    recovery_resume_decision_sha256: str
    recovery_resume_outcome_sha256: str
    resume_start_authorization_sha256: str
    resume_intent_binding_sha256: str
    operation_intent_sha256: str
    operation_start_sha256: str
    publication_approval_sha256: str
    publication_plan_sha256: str
    source_operation: Literal["fresh", "resume"]
    previous_recovery_kind: Literal["already_acquired", "reconciliation_mismatch"]
    recovery_kind: Literal["already_acquired", "reconciliation_mismatch"]
    result_kind: Literal["reconciliation", "none"]
    result_sha256: str | None
    decision: Literal["authorize_resume_preparation"]
    target_operation: Literal["resume"]
    state: Literal["prepared"]

    def __post_init__(self) -> None:
        _validate_preparation(self)


def serialize_external_publication_recovery_resume_decision_preparation_canonical(
    preparation: ExternalPublicationRecoveryResumeDecisionPreparation,
) -> str:
    """Serialize one exact Phase 301 preparation as canonical JSON."""
    _validate_preparation(preparation)
    try:
        return json.dumps(
            {
                "decision": preparation.decision,
                "operation_intent_sha256": preparation.operation_intent_sha256,
                "operation_start_sha256": preparation.operation_start_sha256,
                "previous_recovery_kind": preparation.previous_recovery_kind,
                "publication_approval_sha256": preparation.publication_approval_sha256,
                "publication_plan_sha256": preparation.publication_plan_sha256,
                "recovery_kind": preparation.recovery_kind,
                "recovery_resume_decision_sha256": (
                    preparation.recovery_resume_decision_sha256
                ),
                "recovery_resume_outcome_sha256": (
                    preparation.recovery_resume_outcome_sha256
                ),
                "result_kind": preparation.result_kind,
                "result_sha256": preparation.result_sha256,
                "resume_intent_binding_sha256": (
                    preparation.resume_intent_binding_sha256
                ),
                "resume_start_authorization_sha256": (
                    preparation.resume_start_authorization_sha256
                ),
                "schema_version": preparation.schema_version,
                "source_operation": preparation.source_operation,
                "state": preparation.state,
                "target_operation": preparation.target_operation,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationError:
        raise
    except Exception:
        _raise_preparation("serialization")


def external_publication_recovery_resume_decision_preparation_canonical_bytes(
    preparation: ExternalPublicationRecoveryResumeDecisionPreparation,
) -> bytes:
    """Return exact canonical Phase 301 JSON encoded as UTF-8 bytes."""
    try:
        canonical = serialize_external_publication_recovery_resume_decision_preparation_canonical(  # noqa: E501
            preparation
        )
        return canonical.encode("utf-8")
    except ExternalPublicationRecoveryResumeDecisionPreparationError:
        raise
    except UnicodeError:
        _raise_preparation("encoding")
    except Exception:
        _raise_preparation("encoding")


def external_publication_recovery_resume_decision_preparation_digest(
    preparation: ExternalPublicationRecoveryResumeDecisionPreparation,
) -> str:
    """Return SHA-256 over exact canonical Phase 301 UTF-8 bytes."""
    return sha256(
        external_publication_recovery_resume_decision_preparation_canonical_bytes(
            preparation
        )
    ).hexdigest()


def load_external_publication_recovery_resume_decision_preparation(
    path: Path,
) -> ExternalPublicationRecoveryResumeDecisionPreparation:
    """Strict-load one immutable canonical Phase 301 preparation."""
    _validate_load_path(path)
    try:
        with path.open("rb") as handle:
            contents = handle.read(_MAX_PREPARATION_BYTES + 1)
    except Exception:
        _raise_load("target")
    if type(contents) is not bytes:
        _raise_load("target")
    if len(contents) > _MAX_PREPARATION_BYTES:
        _raise_load("size")

    try:
        value = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKeyError,
        _NonStandardJSONConstantError,
    ):
        _raise_load("parse")
    except Exception:
        _raise_load("parse")

    preparation = _parse_preparation(value)
    try:
        canonical = (
            external_publication_recovery_resume_decision_preparation_canonical_bytes(
                preparation
            )
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationError:
        _raise_load("load")
    except Exception:
        _raise_load("load")
    if canonical != contents:
        _raise_load("noncanonical")
    return preparation


def persist_external_publication_recovery_resume_decision_preparation(
    path: Path,
    preparation: ExternalPublicationRecoveryResumeDecisionPreparation,
) -> None:
    """Durably append-only persist one exact Phase 301 preparation.

    A new target uses exclusive create, full write, flush, file fsync, safe
    close, and parent-directory fsync.  An identical occupied target is a
    durable idempotent success.  Every other occupied byte sequence is a fixed
    conflict and is never rewritten.  Uncertain post-create failures retain
    the artifact and are never retried or cleaned up.
    """
    _validate_persistence_target(path)
    _validate_preparation(preparation)
    try:
        contents = (
            external_publication_recovery_resume_decision_preparation_canonical_bytes(
                preparation
            )
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationError:
        raise
    except Exception:
        _raise_persistence("serialization")
    _write_preparation(path, contents)


def prepare_and_persist_external_publication_recovery_resume_decision_lineage(
    *,
    resume_intent_binding_path: Path,
    phase299_function: Phase299Function = (
        route_external_publication_recovery_resume_outcome
    ),
    decision_loader: DecisionLoader = (
        load_external_publication_recovery_resume_decision
    ),
    decision_digest_function: DecisionDigestFunction = (
        external_publication_recovery_resume_decision_digest
    ),
) -> ExternalPublicationRecoveryResumeDecisionPreparation:
    """Create the exact Phase 301 preparation authorized by Phase 300.

    Only the exact Phase 295 binding path is caller-owned.  Phase 299 is called
    once with only that path.  A completed route and a Phase 300 ``stop`` are
    terminal with zero Phase 301 persistence.  The Phase 300 decision is
    strict-loaded once from the internally derived outcome-digest path,
    revalidated locally, and its original loader-returned identity is passed to
    the decision digest helper exactly once on the authorize route.
    """
    _preflight(
        resume_intent_binding_path=resume_intent_binding_path,
        phase299_function=phase299_function,
        decision_loader=decision_loader,
        decision_digest_function=decision_digest_function,
    )

    routed = _call_phase299(
        phase299_function,
        resume_intent_binding_path=resume_intent_binding_path,
    )

    if type(routed) is ExternalPublicationRecoveryResumeOutcome:
        _revalidate_completed_outcome(routed)
        if routed.state != "completed":
            _raise_preparation("predecessor_contract")
        _raise_preparation("preparation_not_required")

    if type(routed) is not ExternalPublicationRecoveryResumeDecisionRequired:
        _raise_preparation("predecessor_contract")
    _revalidate_decision_required(routed)

    decision_path = _derive_decision_path(
        resume_intent_binding_path,
        routed.recovery_resume_outcome_sha256,
    )
    loaded_decision = _strict_load_decision(decision_loader, decision_path)
    if type(loaded_decision) is not ExternalPublicationRecoveryResumeDecision:
        _raise_preparation("predecessor_contract")
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecision,
        loaded_decision,
        "predecessor_contract",
    )
    _validate_decision_lineage(routed, loaded_decision)

    if loaded_decision.decision == "stop":
        _raise_preparation("preparation_not_authorized")
    if loaded_decision.decision != _DECISION:
        _raise_preparation("predecessor_contract")

    decision_digest = _decision_digest_once(
        decision_digest_function,
        loaded_decision,
    )
    preparation_path = _derive_preparation_path(
        resume_intent_binding_path,
        decision_digest,
    )
    _validate_persistence_target(preparation_path)

    constructed = ExternalPublicationRecoveryResumeDecisionPreparation(
        schema_version=_PREPARATION_SCHEMA_VERSION,
        recovery_resume_decision_sha256=decision_digest,
        recovery_resume_outcome_sha256=loaded_decision.recovery_resume_outcome_sha256,
        resume_start_authorization_sha256=(
            loaded_decision.resume_start_authorization_sha256
        ),
        resume_intent_binding_sha256=loaded_decision.resume_intent_binding_sha256,
        operation_intent_sha256=loaded_decision.operation_intent_sha256,
        operation_start_sha256=loaded_decision.operation_start_sha256,
        publication_approval_sha256=loaded_decision.publication_approval_sha256,
        publication_plan_sha256=loaded_decision.publication_plan_sha256,
        source_operation=loaded_decision.source_operation,
        previous_recovery_kind=loaded_decision.previous_recovery_kind,
        recovery_kind=loaded_decision.recovery_kind,
        result_kind=loaded_decision.result_kind,
        result_sha256=loaded_decision.result_sha256,
        decision=_DECISION,
        target_operation=_OPERATION,
        state=_STATE,
    )

    if _preparation_target_present(preparation_path):
        return _resolve_existing_preparation(preparation_path, constructed)

    try:
        persist_external_publication_recovery_resume_decision_preparation(
            preparation_path,
            constructed,
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationError:
        raise
    except Exception:
        _raise_persistence("dependency_error")
    return constructed


def _preflight(
    *,
    resume_intent_binding_path: object,
    phase299_function: object,
    decision_loader: object,
    decision_digest_function: object,
) -> None:
    if type(resume_intent_binding_path) is not _PATH_TYPE:
        _raise_preparation("path_type")
    if not callable(phase299_function):
        _raise_preparation("configuration")
    if not callable(decision_loader):
        _raise_preparation("configuration")
    if not callable(decision_digest_function):
        _raise_preparation("configuration")


def _call_phase299(
    phase299_function: Phase299Function,
    *,
    resume_intent_binding_path: Path,
) -> object:
    try:
        return phase299_function(
            resume_intent_binding_path=resume_intent_binding_path,
        )
    except _KNOWN_PHASE299_ERRORS:
        raise
    except Exception:
        _raise_preparation("dependency_error")


def _revalidate_completed_outcome(
    outcome: ExternalPublicationRecoveryResumeOutcome,
) -> None:
    _reconstruct_model(
        ExternalPublicationRecoveryResumeOutcome,
        outcome,
        "predecessor_contract",
    )


def _revalidate_decision_required(
    decision_required: ExternalPublicationRecoveryResumeDecisionRequired,
) -> None:
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecisionRequired,
        decision_required,
        "predecessor_contract",
    )


def _strict_load_decision(
    loader: DecisionLoader,
    path: Path,
) -> object:
    try:
        return loader(path)
    except ExternalPublicationRecoveryResumeDecisionError:
        raise
    except Exception:
        _raise_preparation("dependency_error")


def _reconstruct_model(
    model_type: type[object],
    instance: object,
    classification: Classification,
) -> None:
    if type(instance) is not model_type:
        _raise_preparation(classification)
    try:
        values = {
            field.name: getattr(instance, field.name)
            for field in fields(model_type)  # type: ignore[arg-type]
        }
        model_type(**values)  # type: ignore[operator]
    except Exception:
        _raise_preparation(classification)


def _validate_decision_lineage(
    decision_required: ExternalPublicationRecoveryResumeDecisionRequired,
    decision: ExternalPublicationRecoveryResumeDecision,
) -> None:
    for field_name in (
        "recovery_resume_outcome_sha256",
        "resume_start_authorization_sha256",
        "resume_intent_binding_sha256",
        "operation_intent_sha256",
        "operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "source_operation",
        "previous_recovery_kind",
        "recovery_kind",
        "result_kind",
        "result_sha256",
    ):
        if getattr(decision, field_name) != getattr(decision_required, field_name):
            _raise_preparation("predecessor_lineage")
    if (
        decision.operation != decision_required.operation
        or decision.operation != _OPERATION
    ):
        _raise_preparation("predecessor_lineage")
    if decision.state != _DECIDED_STATE:
        _raise_preparation("predecessor_lineage")


def _decision_digest_once(
    digest_function: DecisionDigestFunction,
    decision: object,
) -> str:
    try:
        digest = digest_function(decision)  # type: ignore[arg-type]
    except ExternalPublicationRecoveryResumeDecisionError:
        raise
    except Exception:
        _raise_preparation("dependency_error")
    if not _is_sha256(digest):
        _raise_preparation("decision_digest")
    return digest


def _derive_decision_path(
    resume_intent_binding_path: Path,
    outcome_digest: str,
) -> Path:
    return resume_intent_binding_path.parent / (
        f"{_DECISION_FILENAME_PREFIX}{outcome_digest}{_FILENAME_SUFFIX}"
    )


def _derive_preparation_path(
    resume_intent_binding_path: Path,
    decision_digest: str,
) -> Path:
    return resume_intent_binding_path.parent / (
        f"{_PREPARATION_FILENAME_PREFIX}{decision_digest}{_FILENAME_SUFFIX}"
    )


def _preparation_target_present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except Exception:
        _raise_load("target")


def _resolve_existing_preparation(
    path: Path,
    constructed: ExternalPublicationRecoveryResumeDecisionPreparation,
) -> ExternalPublicationRecoveryResumeDecisionPreparation:
    try:
        loaded = load_external_publication_recovery_resume_decision_preparation(path)
    except ExternalPublicationRecoveryResumeDecisionPreparationError:
        raise
    except Exception:
        _raise_load("load")
    if type(loaded) is not ExternalPublicationRecoveryResumeDecisionPreparation:
        _raise_load("load")
    _reconstruct_loaded_preparation(loaded)
    if loaded != constructed:
        _raise_conflict()
    return loaded


def _reconstruct_loaded_preparation(
    preparation: object,
) -> None:
    if type(preparation) is not ExternalPublicationRecoveryResumeDecisionPreparation:
        _raise_load("load")
    try:
        values = {
            field.name: getattr(preparation, field.name)
            for field in fields(ExternalPublicationRecoveryResumeDecisionPreparation)
        }
        ExternalPublicationRecoveryResumeDecisionPreparation(**values)
    except Exception:
        _raise_load("load")


def _validate_preparation(preparation: object) -> None:
    if type(preparation) is not ExternalPublicationRecoveryResumeDecisionPreparation:
        _raise_preparation("configuration")
    try:
        _check_preparation(preparation)
    except ExternalPublicationRecoveryResumeDecisionPreparationError:
        raise
    except Exception:
        _raise_preparation("configuration")


def _check_preparation(
    preparation: ExternalPublicationRecoveryResumeDecisionPreparation,
) -> None:
    schema_version = preparation.schema_version
    decision_digest = preparation.recovery_resume_decision_sha256
    outcome_digest = preparation.recovery_resume_outcome_sha256
    authorization_digest = preparation.resume_start_authorization_sha256
    binding_digest = preparation.resume_intent_binding_sha256
    intent_digest = preparation.operation_intent_sha256
    start_digest = preparation.operation_start_sha256
    approval_digest = preparation.publication_approval_sha256
    plan_digest = preparation.publication_plan_sha256
    source_operation = preparation.source_operation
    previous_recovery_kind = preparation.previous_recovery_kind
    recovery_kind = preparation.recovery_kind
    result_kind = preparation.result_kind
    result_digest = preparation.result_sha256
    decision = preparation.decision
    target_operation = preparation.target_operation
    state = preparation.state

    if type(schema_version) is not str or schema_version != _PREPARATION_SCHEMA_VERSION:
        _raise_preparation("configuration")
    for digest in (
        decision_digest,
        outcome_digest,
        authorization_digest,
        binding_digest,
        intent_digest,
        start_digest,
        approval_digest,
        plan_digest,
    ):
        if not _is_sha256(digest):
            _raise_preparation("configuration")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_preparation("configuration")
    if (
        type(previous_recovery_kind) is not str
        or previous_recovery_kind not in _RECOVERY_KINDS
    ):
        _raise_preparation("configuration")
    if (
        previous_recovery_kind == _MISMATCH_RECOVERY_KIND
        and source_operation != _OPERATION
    ):
        _raise_preparation("configuration")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_preparation("configuration")
    if type(result_kind) is not str or result_kind not in _RESULT_KINDS:
        _raise_preparation("configuration")
    if type(decision) is not str or decision != _DECISION:
        _raise_preparation("configuration")
    if type(target_operation) is not str or target_operation != _OPERATION:
        _raise_preparation("configuration")
    if type(state) is not str or state != _STATE:
        _raise_preparation("configuration")

    if result_kind == _NONE_RESULT_KIND:
        if result_digest is not None:
            _raise_preparation("configuration")
        if recovery_kind != _ALREADY_ACQUIRED_RECOVERY_KIND:
            _raise_preparation("configuration")
        return
    if not _is_sha256(result_digest):
        _raise_preparation("configuration")
    if recovery_kind != _MISMATCH_RECOVERY_KIND:
        _raise_preparation("configuration")


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _validate_persistence_target(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_persistence("path_type")
    try:
        parent = path.parent
        if not parent.exists() or not parent.is_dir():
            _raise_persistence("parent")
        if (
            path.is_symlink()  # type: ignore[union-attr]
            or path.is_dir()  # type: ignore[union-attr]
            or (path.exists() and not path.is_file())  # type: ignore[union-attr]
        ):
            _raise_persistence("target")
    except ExternalPublicationRecoveryResumeDecisionPreparationPersistenceError:
        raise
    except Exception:
        _raise_persistence("target")


def _validate_load_path(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_load("path_type")
    try:
        if (
            path.is_symlink()  # type: ignore[union-attr]
            or not path.exists()  # type: ignore[union-attr]
            or path.is_dir()  # type: ignore[union-attr]
            or not path.is_file()  # type: ignore[union-attr]
        ):
            _raise_load("target")
    except ExternalPublicationRecoveryResumeDecisionPreparationLoadError:
        raise
    except Exception:
        _raise_load("target")


def _write_preparation(path: Path, contents: bytes) -> None:
    try:
        handle = path.open("xb")
    except FileExistsError:
        _verify_existing_preparation(path, contents)
        return
    except OSError as error:
        if error.errno == errno.EEXIST:
            _verify_existing_preparation(path, contents)
            return
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    _persist_new_preparation(handle, path.parent, contents)


def _persist_new_preparation(
    handle: object,
    directory: Path,
    contents: bytes,
) -> None:
    try:
        with _preparation_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short recovery resume decision preparation write")
            active_handle.flush()  # type: ignore[attr-defined]
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_preparation_directory(directory)
    except Exception:
        _raise_persistence("ambiguous")


def _verify_existing_preparation(path: Path, contents: bytes) -> None:
    try:
        if (
            path.is_symlink()
            or not path.exists()
            or path.is_dir()
            or not path.is_file()
        ):
            _raise_persistence("target")
        with path.open("rb") as handle:
            existing = handle.read(_MAX_PREPARATION_BYTES + 1)
        if type(existing) is not bytes:
            _raise_persistence("target")
    except ExternalPublicationRecoveryResumeDecisionPreparationPersistenceError:
        raise
    except Exception:
        _raise_persistence("target")

    if existing != contents:
        _raise_conflict()

    try:
        handle = path.open("rb")
    except Exception:
        _raise_persistence("ambiguous")
    try:
        with _preparation_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")
    try:
        _fsync_preparation_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


@contextmanager
def _preparation_handle_scope(handle: object) -> Iterator[object]:
    enter = getattr(handle, "__enter__", None)
    exit_ = getattr(handle, "__exit__", None)
    if callable(enter) and callable(exit_):
        with handle as active_handle:  # type: ignore[union-attr]
            yield active_handle
        return

    try:
        yield handle
    finally:
        close = getattr(handle, "close", None)
        if not callable(close):
            raise OSError("recovery resume decision preparation handle cannot close")
        close()


def _fsync_preparation_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_preparation(
    value: object,
) -> ExternalPublicationRecoveryResumeDecisionPreparation:
    if type(value) is not dict:
        _raise_load("parse")
    if frozenset(value) != _PREPARATION_KEYS:
        _raise_load("keys")
    try:
        return ExternalPublicationRecoveryResumeDecisionPreparation(
            schema_version=value["schema_version"],
            recovery_resume_decision_sha256=value["recovery_resume_decision_sha256"],
            recovery_resume_outcome_sha256=value["recovery_resume_outcome_sha256"],
            resume_start_authorization_sha256=value[
                "resume_start_authorization_sha256"
            ],
            resume_intent_binding_sha256=value["resume_intent_binding_sha256"],
            operation_intent_sha256=value["operation_intent_sha256"],
            operation_start_sha256=value["operation_start_sha256"],
            publication_approval_sha256=value["publication_approval_sha256"],
            publication_plan_sha256=value["publication_plan_sha256"],
            source_operation=value["source_operation"],
            previous_recovery_kind=value["previous_recovery_kind"],
            recovery_kind=value["recovery_kind"],
            result_kind=value["result_kind"],
            result_sha256=value["result_sha256"],
            decision=value["decision"],
            target_operation=value["target_operation"],
            state=value["state"],
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationError:
        _raise_load("load")
    except Exception:
        _raise_load("parse")


class _DuplicateKeyError(ValueError):
    """Raised when canonical JSON repeats a key."""


class _NonStandardJSONConstantError(ValueError):
    """Raised when canonical JSON uses NaN or an infinity constant."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    raise _NonStandardJSONConstantError(value)


def _raise_preparation(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError(
        classification
    ) from None


def _raise_persistence(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationPersistenceError(
        classification
    ) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationConflictError(
        "conflict"
    ) from None


def _raise_load(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationLoadError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationRecoveryResumeDecisionPreparation",
    "ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError",
    "ExternalPublicationRecoveryResumeDecisionPreparationConflictError",
    "ExternalPublicationRecoveryResumeDecisionPreparationError",
    "ExternalPublicationRecoveryResumeDecisionPreparationFailureDetail",
    "ExternalPublicationRecoveryResumeDecisionPreparationLoadError",
    "ExternalPublicationRecoveryResumeDecisionPreparationPersistenceError",
    "external_publication_recovery_resume_decision_preparation_canonical_bytes",
    "external_publication_recovery_resume_decision_preparation_digest",
    "load_external_publication_recovery_resume_decision_preparation",
    "persist_external_publication_recovery_resume_decision_preparation",
    "prepare_and_persist_external_publication_recovery_resume_decision_lineage",
    "serialize_external_publication_recovery_resume_decision_preparation_canonical",
]
