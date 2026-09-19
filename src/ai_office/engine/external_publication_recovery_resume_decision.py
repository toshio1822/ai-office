"""Durable operator decision evidence for recovery-resume outcomes (Phase 300).

Phase 300 adds one append-only, immutable decision boundary above the public
Phase 299 recovery-resume outcome router.  The caller supplies only the exact
Phase 295 binding path, one explicit operator decision, and operator metadata.
Phase 299 is called exactly once and is the only orchestration dependency.

A completed Phase 298 outcome is terminal and cannot receive a Phase 300
decision.  An exact Phase 299 ``DecisionRequired`` model is independently
revalidated and becomes the authority for every recovery-resume provenance and
for the current recovery kind.  Phase 300 records the decision only; it does
not prepare an intent, authorize or acquire a start, execute, reconcile, or
invoke a provider.
"""

from __future__ import annotations

import errno
import json
import os
import re
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from .external_publication_operation_start import (
    ExternalPublicationOperationStartError,
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

_DECISION_ERROR_MESSAGE = "external publication recovery resume decision is blocked"
_PERSISTENCE_ERROR_MESSAGE = (
    "external publication recovery resume decision persistence failed"
)
_LOAD_ERROR_MESSAGE = (
    "external publication recovery resume decision could not be loaded"
)
_DECISION_SCHEMA_VERSION = "external-publication-recovery-resume-decision.v1"
_DECISION_KEYS = frozenset(
    {
        "decision",
        "decided_by",
        "decision_id",
        "operation",
        "operation_intent_sha256",
        "operation_start_sha256",
        "previous_recovery_kind",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "recovery_kind",
        "recovery_resume_outcome_sha256",
        "result_kind",
        "result_sha256",
        "resume_intent_binding_sha256",
        "resume_start_authorization_sha256",
        "schema_version",
        "source_operation",
        "state",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_MAX_DECISION_BYTES = 4096
_MAX_METADATA_LENGTH = 256
_SOURCE_OPERATIONS = frozenset({"fresh", "resume"})
_RECOVERY_KINDS = frozenset({"already_acquired", "reconciliation_mismatch"})
_RESULT_KINDS = frozenset({"reconciliation", "none"})
_DECISIONS = frozenset({"stop", "authorize_resume_preparation"})
_STATE = "decided"
_OPERATION = "resume"
_NONE_RESULT_KIND = "none"
_RECONCILIATION_RESULT_KIND = "reconciliation"
_ALREADY_ACQUIRED_RECOVERY_KIND = "already_acquired"
_MISMATCH_RECOVERY_KIND = "reconciliation_mismatch"
_DECISION_FILENAME_PREFIX = "external-publication-recovery-resume-decision-"
_FILENAME_SUFFIX = ".json"

Classification = Literal[
    "configuration",
    "path_type",
    "decision",
    "operator_metadata",
    "predecessor_contract",
    "decision_not_required",
    "dependency_error",
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
_KNOWN_PHASE299_ERRORS = (
    ExternalPublicationRecoveryResumeOutcomeRoutingError,
    ExternalPublicationRecoveryResumeIntentBindingError,
    ExternalPublicationRecoveryResumeStartAuthorizationError,
    ExternalPublicationRecoveryResumeOutcomeError,
    ExternalPublicationOperationStartError,
)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionFailureDetail:
    """Detail-safe classification for one Phase 300 failure."""

    classification: Classification


class ExternalPublicationRecoveryResumeDecisionError(ValueError):
    """Raised when a Phase 300 decision or request is not exact."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_DECISION_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeDecisionCompatibilityError(
    ExternalPublicationRecoveryResumeDecisionError
):
    """Raised when a request or predecessor is incompatible."""


class ExternalPublicationRecoveryResumeDecisionPersistenceError(
    ExternalPublicationRecoveryResumeDecisionError
):
    """Raised when decision durability cannot be proven."""

    def __init__(self, classification: Classification = "target") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeDecisionConflictError(
    ExternalPublicationRecoveryResumeDecisionPersistenceError
):
    """Raised when an occupied decision target is not the exact record."""


class ExternalPublicationRecoveryResumeDecisionLoadError(
    ExternalPublicationRecoveryResumeDecisionError
):
    """Raised when a decision sidecar is not an exact canonical record."""

    def __init__(self, classification: Classification = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionFailureDetail(
            classification
        )


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecision:
    """Immutable, secret-free recovery-resume operator decision evidence."""

    schema_version: Literal["external-publication-recovery-resume-decision.v1"]
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
    operation: Literal["resume"]
    result_kind: Literal["reconciliation", "none"]
    result_sha256: str | None
    decision: Literal["stop", "authorize_resume_preparation"]
    decided_by: str
    decision_id: str
    state: Literal["decided"]

    def __post_init__(self) -> None:
        _validate_decision(self)


def serialize_external_publication_recovery_resume_decision_canonical(
    decision: ExternalPublicationRecoveryResumeDecision,
) -> str:
    """Serialize one exact decision as compact deterministic JSON."""
    _validate_decision(decision)
    try:
        return json.dumps(
            {
                "decision": decision.decision,
                "decided_by": decision.decided_by,
                "decision_id": decision.decision_id,
                "operation": decision.operation,
                "operation_intent_sha256": decision.operation_intent_sha256,
                "operation_start_sha256": decision.operation_start_sha256,
                "previous_recovery_kind": decision.previous_recovery_kind,
                "publication_approval_sha256": decision.publication_approval_sha256,
                "publication_plan_sha256": decision.publication_plan_sha256,
                "recovery_kind": decision.recovery_kind,
                "recovery_resume_outcome_sha256": (
                    decision.recovery_resume_outcome_sha256
                ),
                "result_kind": decision.result_kind,
                "result_sha256": decision.result_sha256,
                "resume_intent_binding_sha256": decision.resume_intent_binding_sha256,
                "resume_start_authorization_sha256": (
                    decision.resume_start_authorization_sha256
                ),
                "schema_version": decision.schema_version,
                "source_operation": decision.source_operation,
                "state": decision.state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except ExternalPublicationRecoveryResumeDecisionError:
        raise
    except Exception:
        _raise_decision("serialization")


def external_publication_recovery_resume_decision_canonical_bytes(
    decision: ExternalPublicationRecoveryResumeDecision,
) -> bytes:
    """Return exact canonical decision JSON encoded as UTF-8 bytes."""
    try:
        return serialize_external_publication_recovery_resume_decision_canonical(
            decision
        ).encode("utf-8")
    except ExternalPublicationRecoveryResumeDecisionError:
        raise
    except UnicodeError:
        _raise_decision("encoding")
    except Exception:
        _raise_decision("encoding")


def external_publication_recovery_resume_decision_digest(
    decision: ExternalPublicationRecoveryResumeDecision,
) -> str:
    """Return SHA-256 over exact canonical decision UTF-8 bytes."""
    return sha256(
        external_publication_recovery_resume_decision_canonical_bytes(decision)
    ).hexdigest()


def load_external_publication_recovery_resume_decision(
    path: Path,
) -> ExternalPublicationRecoveryResumeDecision:
    """Read and strictly revalidate one immutable canonical decision."""
    _validate_load_path(path)
    try:
        with path.open("rb") as handle:
            contents = handle.read(_MAX_DECISION_BYTES + 1)
    except Exception:
        _raise_load("target")
    if type(contents) is not bytes:
        _raise_load("target")
    if len(contents) > _MAX_DECISION_BYTES:
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

    decision = _parse_decision(value)
    try:
        canonical = external_publication_recovery_resume_decision_canonical_bytes(
            decision
        )
    except ExternalPublicationRecoveryResumeDecisionError:
        _raise_load("load")
    except Exception:
        _raise_load("load")
    if canonical != contents:
        _raise_load("noncanonical")
    return decision


def persist_external_publication_recovery_resume_decision(
    path: Path,
    decision: ExternalPublicationRecoveryResumeDecision,
) -> None:
    """Durably append-only persist one exact Phase 300 decision.

    A new target is exclusively created, fully written, flushed, file-fsynced,
    safely closed, and parent-directory-fsynced.  An identical existing target
    is a durable idempotent success.  Any other existing bytes are a fixed
    conflict.  Uncertain failures after exclusive creation retain the artifact
    and are never cleaned up, retried, or rewritten.
    """
    _validate_persistence_target(path)
    _validate_decision(decision)
    try:
        contents = external_publication_recovery_resume_decision_canonical_bytes(
            decision
        )
    except ExternalPublicationRecoveryResumeDecisionError:
        raise
    except Exception:
        _raise_persistence("serialization")
    _write_decision(path, contents)


def decide_and_persist_external_publication_recovery_resume(
    *,
    resume_intent_binding_path: Path,
    decision: Literal["stop", "authorize_resume_preparation"],
    decided_by: str,
    decision_id: str,
    phase299_function: Phase299Function = (
        route_external_publication_recovery_resume_outcome
    ),
) -> ExternalPublicationRecoveryResumeDecision:
    """Persist one explicit decision for the exact Phase 299 recovery route.

    Only the exact Phase 295 binding path and the three explicit operator
    values are caller-owned.  Phase 299 is called once with only that path.
    Its exact ``DecisionRequired`` object supplies all provenance and the
    current recovery kind.  A completed outcome is terminal and results in a
    detail-safe ``decision_not_required`` error with zero persistence.
    """
    _preflight(
        resume_intent_binding_path=resume_intent_binding_path,
        decision=decision,
        decided_by=decided_by,
        decision_id=decision_id,
        phase299_function=phase299_function,
    )

    routed = _call_phase299(
        phase299_function, resume_intent_binding_path=resume_intent_binding_path
    )

    if type(routed) is ExternalPublicationRecoveryResumeOutcome:
        _revalidate_completed_outcome(routed)
        if routed.state != "completed":
            _raise_decision("predecessor_contract")
        _raise_decision("decision_not_required")

    if type(routed) is not ExternalPublicationRecoveryResumeDecisionRequired:
        _raise_decision("predecessor_contract")
    _revalidate_decision_required(routed)

    decision_path = _derive_decision_path(
        resume_intent_binding_path,
        routed.recovery_resume_outcome_sha256,
    )
    _validate_persistence_target(decision_path)

    constructed = ExternalPublicationRecoveryResumeDecision(
        schema_version=_DECISION_SCHEMA_VERSION,
        recovery_resume_outcome_sha256=routed.recovery_resume_outcome_sha256,
        resume_start_authorization_sha256=routed.resume_start_authorization_sha256,
        resume_intent_binding_sha256=routed.resume_intent_binding_sha256,
        operation_intent_sha256=routed.operation_intent_sha256,
        operation_start_sha256=routed.operation_start_sha256,
        publication_approval_sha256=routed.publication_approval_sha256,
        publication_plan_sha256=routed.publication_plan_sha256,
        source_operation=routed.source_operation,
        previous_recovery_kind=routed.previous_recovery_kind,
        recovery_kind=routed.recovery_kind,
        operation=routed.operation,
        result_kind=routed.result_kind,
        result_sha256=routed.result_sha256,
        decision=decision,
        decided_by=decided_by,
        decision_id=decision_id,
        state=_STATE,
    )

    if _decision_target_present(decision_path):
        return _resolve_existing_decision(decision_path, constructed)

    try:
        persist_external_publication_recovery_resume_decision(
            decision_path, constructed
        )
    except ExternalPublicationRecoveryResumeDecisionError:
        raise
    except Exception:
        _raise_persistence("dependency_error")
    return constructed


def _preflight(
    *,
    resume_intent_binding_path: object,
    decision: object,
    decided_by: object,
    decision_id: object,
    phase299_function: object,
) -> None:
    if type(resume_intent_binding_path) is not _PATH_TYPE:
        _raise_decision("path_type")
    if type(decision) is not str or decision not in _DECISIONS:
        _raise_decision("decision")
    _validate_operator_metadata(decided_by, decision_id)
    if not callable(phase299_function):
        _raise_decision("configuration")


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
        _raise_decision("dependency_error")


def _revalidate_completed_outcome(
    outcome: ExternalPublicationRecoveryResumeOutcome,
) -> None:
    _reconstruct_model(
        ExternalPublicationRecoveryResumeOutcome,
        outcome,
        _raise_decision,
        "predecessor_contract",
    )


def _revalidate_decision_required(
    decision_required: ExternalPublicationRecoveryResumeDecisionRequired,
) -> None:
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecisionRequired,
        decision_required,
        _raise_decision,
        "predecessor_contract",
    )


def _reconstruct_model(
    model_type: type[object],
    instance: object,
    raiser: Callable[[Classification], NoReturn],
    classification: Classification,
) -> None:
    if type(instance) is not model_type:
        raiser(classification)
    try:
        values = {
            field.name: getattr(instance, field.name)
            for field in fields(model_type)  # type: ignore[arg-type]
        }
        model_type(**values)  # type: ignore[operator]
    except Exception:
        raiser(classification)


def _derive_decision_path(
    resume_intent_binding_path: Path,
    outcome_digest: str,
) -> Path:
    return resume_intent_binding_path.parent / (
        f"{_DECISION_FILENAME_PREFIX}{outcome_digest}{_FILENAME_SUFFIX}"
    )


def _decision_target_present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except Exception:
        _raise_load("target")


def _resolve_existing_decision(
    path: Path,
    constructed: ExternalPublicationRecoveryResumeDecision,
) -> ExternalPublicationRecoveryResumeDecision:
    try:
        loaded = load_external_publication_recovery_resume_decision(path)
    except ExternalPublicationRecoveryResumeDecisionError:
        raise
    except Exception:
        _raise_load("load")
    if type(loaded) is not ExternalPublicationRecoveryResumeDecision:
        _raise_load("load")
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecision,
        loaded,
        _raise_load,
        "load",
    )
    if loaded != constructed:
        _raise_conflict()
    return loaded


def _validate_decision(decision: object) -> None:
    if type(decision) is not ExternalPublicationRecoveryResumeDecision:
        _raise_decision("configuration")
    try:
        schema_version = decision.schema_version  # type: ignore[union-attr]
        outcome_digest = decision.recovery_resume_outcome_sha256  # type: ignore[union-attr]
        authorization_digest = (  # type: ignore[union-attr]
            decision.resume_start_authorization_sha256
        )
        binding_digest = decision.resume_intent_binding_sha256  # type: ignore[union-attr]
        intent_digest = decision.operation_intent_sha256  # type: ignore[union-attr]
        start_digest = decision.operation_start_sha256  # type: ignore[union-attr]
        approval_digest = decision.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = decision.publication_plan_sha256  # type: ignore[union-attr]
        source_operation = decision.source_operation  # type: ignore[union-attr]
        previous_recovery_kind = decision.previous_recovery_kind  # type: ignore[union-attr]
        recovery_kind = decision.recovery_kind  # type: ignore[union-attr]
        operation = decision.operation  # type: ignore[union-attr]
        result_kind = decision.result_kind  # type: ignore[union-attr]
        result_digest = decision.result_sha256  # type: ignore[union-attr]
        chosen = decision.decision  # type: ignore[union-attr]
        decided_by = decision.decided_by  # type: ignore[union-attr]
        decision_id = decision.decision_id  # type: ignore[union-attr]
        state = decision.state  # type: ignore[union-attr]
    except Exception:
        _raise_decision("configuration")

    if type(schema_version) is not str or schema_version != _DECISION_SCHEMA_VERSION:
        _raise_decision("configuration")
    for digest in (
        outcome_digest,
        authorization_digest,
        binding_digest,
        intent_digest,
        start_digest,
        approval_digest,
        plan_digest,
    ):
        if not _is_sha256(digest):
            _raise_decision("configuration")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_decision("configuration")
    if (
        type(previous_recovery_kind) is not str
        or previous_recovery_kind not in _RECOVERY_KINDS
    ):
        _raise_decision("configuration")
    if previous_recovery_kind == _MISMATCH_RECOVERY_KIND and source_operation != (
        _OPERATION
    ):
        _raise_decision("configuration")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_decision("configuration")
    if type(operation) is not str or operation != _OPERATION:
        _raise_decision("configuration")
    if type(result_kind) is not str or result_kind not in _RESULT_KINDS:
        _raise_decision("configuration")
    if type(chosen) is not str or chosen not in _DECISIONS:
        _raise_decision("configuration")
    if type(state) is not str or state != _STATE:
        _raise_decision("configuration")
    _validate_operator_metadata(decided_by, decision_id)

    if result_kind == _NONE_RESULT_KIND:
        if result_digest is not None:
            _raise_decision("configuration")
        if recovery_kind != _ALREADY_ACQUIRED_RECOVERY_KIND:
            _raise_decision("configuration")
        return
    if not _is_sha256(result_digest):
        _raise_decision("configuration")
    if recovery_kind != _MISMATCH_RECOVERY_KIND:
        _raise_decision("configuration")


def _validate_operator_metadata(decided_by: object, decision_id: object) -> None:
    for value in (decided_by, decision_id):
        if (
            type(value) is not str
            or not value
            or value != value.strip()
            or len(value) > _MAX_METADATA_LENGTH
        ):
            _raise_decision("operator_metadata")
        if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
            _raise_decision("operator_metadata")


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _validate_persistence_target(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_persistence("path_type")
    try:
        if not path.parent.exists() or not path.parent.is_dir():  # type: ignore[union-attr]
            _raise_persistence("parent")
        if (
            path.is_symlink()  # type: ignore[union-attr]
            or path.is_dir()  # type: ignore[union-attr]
            or (path.exists() and not path.is_file())  # type: ignore[union-attr]
        ):
            _raise_persistence("target")
    except ExternalPublicationRecoveryResumeDecisionPersistenceError:
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
    except ExternalPublicationRecoveryResumeDecisionLoadError:
        raise
    except Exception:
        _raise_load("target")


def _write_decision(path: Path, contents: bytes) -> None:
    try:
        handle = path.open("xb")
    except FileExistsError:
        _verify_existing_decision(path, contents)
        return
    except OSError as error:
        if error.errno == errno.EEXIST:
            _verify_existing_decision(path, contents)
            return
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    _persist_new_decision(handle, path.parent, contents)


def _persist_new_decision(handle: object, directory: Path, contents: bytes) -> None:
    try:
        with _decision_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short recovery resume decision write")
            active_handle.flush()  # type: ignore[attr-defined]
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_decision_directory(directory)
    except Exception:
        _raise_persistence("ambiguous")


def _verify_existing_decision(path: Path, contents: bytes) -> None:
    try:
        if (
            path.is_symlink()
            or not path.exists()
            or path.is_dir()
            or not path.is_file()
        ):
            _raise_persistence("target")
        with path.open("rb") as handle:
            existing = handle.read(_MAX_DECISION_BYTES + 1)
        if type(existing) is not bytes:
            _raise_persistence("target")
    except ExternalPublicationRecoveryResumeDecisionPersistenceError:
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
        with _decision_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")
    try:
        _fsync_decision_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


@contextmanager
def _decision_handle_scope(handle: object) -> Iterator[object]:
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
            raise OSError("recovery resume decision handle cannot close")
        close()


def _fsync_decision_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_decision(value: object) -> ExternalPublicationRecoveryResumeDecision:
    if type(value) is not dict:
        _raise_load("parse")
    if frozenset(value) != _DECISION_KEYS:
        _raise_load("keys")
    try:
        return ExternalPublicationRecoveryResumeDecision(
            schema_version=value["schema_version"],
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
            operation=value["operation"],
            result_kind=value["result_kind"],
            result_sha256=value["result_sha256"],
            decision=value["decision"],
            decided_by=value["decided_by"],
            decision_id=value["decision_id"],
            state=value["state"],
        )
    except ExternalPublicationRecoveryResumeDecisionError:
        _raise_load("load")
    except Exception:
        _raise_load("load")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


class _DuplicateKeyError(ValueError):
    pass


class _NonStandardJSONConstantError(ValueError):
    pass


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    del value
    raise _NonStandardJSONConstantError


def _raise_decision(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionCompatibilityError(
        classification
    ) from None


def _raise_persistence(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPersistenceError(
        classification
    ) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionConflictError("conflict") from None


def _raise_load(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionLoadError(classification) from None


__all__ = [
    "ExternalPublicationRecoveryResumeDecision",
    "ExternalPublicationRecoveryResumeDecisionCompatibilityError",
    "ExternalPublicationRecoveryResumeDecisionConflictError",
    "ExternalPublicationRecoveryResumeDecisionError",
    "ExternalPublicationRecoveryResumeDecisionFailureDetail",
    "ExternalPublicationRecoveryResumeDecisionLoadError",
    "ExternalPublicationRecoveryResumeDecisionPersistenceError",
    "decide_and_persist_external_publication_recovery_resume",
    "external_publication_recovery_resume_decision_canonical_bytes",
    "external_publication_recovery_resume_decision_digest",
    "load_external_publication_recovery_resume_decision",
    "persist_external_publication_recovery_resume_decision",
    "serialize_external_publication_recovery_resume_decision_canonical",
]
