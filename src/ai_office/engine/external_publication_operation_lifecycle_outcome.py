"""Durable post-handoff lifecycle outcome evidence for one Phase 291 return.

Phase 292 adds one append-only, immutable lifecycle sidecar on top of the
Phase 291 same-invocation handoff.  It records durable post-handoff knowledge
only: it never authorizes a retry, never converts ``fresh`` to ``resume``,
never infers provider success or failure from an exception, and never guesses
a durable state from a lower artifact.

Only a *normal* Phase 291 return may create lifecycle outcome evidence:

- fresh → exact ``ExternalPublicationExecutionResult(status="published")``
  becomes ``completed``;
- resume → exact ``ExternalPublicationExecutionReconciliation(status="matched")``
  becomes ``completed``;
- exact ``ExternalPublicationOperationStartAcquisition(status="already_acquired")``
  becomes ``recovery_required`` and is never ``completed``;
- resume reconciliation ``lineage_mismatch`` becomes ``recovery_required``.

If Phase 291 raises, Phase 292 propagates the known error unchanged and writes
no sidecar.  Exception-side durable ambiguity classification belongs to a
later explicit phase.

``recovery_required`` is durable knowledge that automatic replay must stop.  It
is neither a failure classification nor retry permission.  An existing exact
lifecycle outcome is an idempotent fast path with a Phase 291 zero-call count.
"""

from __future__ import annotations

import errno
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from .external_publication import (
    ExternalPublicationApproval,
    ExternalPublicationError,
    ExternalPublicationPlan,
    external_publication_approval_digest,
    external_publication_plan_digest,
    validate_external_publication_approval,
)
from .external_publication_execution import (
    ExternalPublicationExecutionError,
    ExternalPublicationExecutionResult,
)
from .external_publication_execution_evidence import (
    ExternalPublicationExecutionEvidenceError,
    external_publication_execution_result_digest,
)
from .external_publication_execution_orchestration import (
    ExternalPublicationExecutionOrchestrationError,
)
from .external_publication_execution_reconciliation import (
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationError,
)
from .external_publication_execution_reconciliation_evidence import (
    ExternalPublicationExecutionReconciliationEvidenceError,
    external_publication_execution_reconciliation_digest,
)
from .external_publication_execution_reconciliation_orchestration import (
    ExternalPublicationExecutionReconciliationOrchestrationError,
)
from .external_publication_execution_reconciliation_resume import (
    ExternalPublicationExecutionReconciliationResumeError,
)
from .external_publication_operation import (
    ExternalPublicationFreshOperationRequest,
    ExternalPublicationOperationError,
    ExternalPublicationResumeOperationRequest,
)
from .external_publication_operation_intent import (
    ExternalPublicationOperationIntentError,
)
from .external_publication_operation_start import (
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartAcquisition,
    ExternalPublicationOperationStartError,
    external_publication_operation_start_digest,
    load_external_publication_operation_start,
)
from .external_publication_operation_start_handoff import (
    ExternalPublicationOperationStartHandoffError,
    run_external_publication_operation_start_handoff,
)

_LIFECYCLE_ERROR_MESSAGE = "external publication operation lifecycle outcome is invalid"
_PERSISTENCE_ERROR_MESSAGE = (
    "external publication operation lifecycle outcome persistence failed"
)
_LOAD_ERROR_MESSAGE = (
    "external publication operation lifecycle outcome could not be loaded"
)
_LIFECYCLE_SCHEMA_VERSION = "external-publication-operation-lifecycle-outcome.v1"
_START_SCHEMA_VERSION = "external-publication-operation-start.v1"
_LIFECYCLE_KEYS = frozenset(
    {
        "operation",
        "operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "result_kind",
        "result_sha256",
        "schema_version",
        "state",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_MAX_LIFECYCLE_BYTES = 4096
_OPERATIONS = frozenset({"fresh", "resume"})
_STATES = frozenset({"completed", "recovery_required"})
_RESULT_KINDS = frozenset({"execution_result", "reconciliation", "none"})
_ACQUISITION_STATUSES = frozenset({"acquired", "already_acquired"})

Classification = Literal[
    "configuration",
    "path_type",
    "request_contract",
    "request_lineage",
    "start_contract",
    "start_lineage",
    "handoff_result_contract",
    "result_lineage",
    "result_digest",
    "serialization",
    "encoding",
    "parent",
    "target",
    "create",
    "ambiguous",
    "conflict",
    "load",
    "parse",
    "keys",
    "noncanonical",
    "dependency_error",
]
Phase291Function = Callable[..., object]
StartLoader = Callable[[Path], object]
StartDigestFunction = Callable[[ExternalPublicationOperationStart], object]
FreshResultDigestFunction = Callable[[ExternalPublicationExecutionResult], object]
ReconciliationDigestFunction = Callable[
    [ExternalPublicationExecutionReconciliation], object
]

_PHASE291_KNOWN_ERRORS = (
    ExternalPublicationOperationStartHandoffError,
    ExternalPublicationOperationStartError,
    ExternalPublicationOperationIntentError,
    ExternalPublicationError,
    ExternalPublicationOperationError,
    ExternalPublicationExecutionOrchestrationError,
    ExternalPublicationExecutionError,
    ExternalPublicationExecutionEvidenceError,
    ExternalPublicationExecutionReconciliationResumeError,
    ExternalPublicationExecutionReconciliationOrchestrationError,
    ExternalPublicationExecutionReconciliationError,
    ExternalPublicationExecutionReconciliationEvidenceError,
)


@dataclass(frozen=True)
class ExternalPublicationOperationLifecycleOutcomeFailureDetail:
    """Detail-safe classification for one lifecycle outcome failure."""

    classification: Classification


class ExternalPublicationOperationLifecycleOutcomeError(ValueError):
    """Raised when a lifecycle outcome record or request is not exact."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_LIFECYCLE_ERROR_MESSAGE)
        self.detail = ExternalPublicationOperationLifecycleOutcomeFailureDetail(
            classification
        )


class ExternalPublicationOperationLifecycleOutcomeCompatibilityError(
    ExternalPublicationOperationLifecycleOutcomeError
):
    """Raised when a request, dependency, or handoff return is incompatible."""


class ExternalPublicationOperationLifecycleOutcomePersistenceError(
    ExternalPublicationOperationLifecycleOutcomeError
):
    """Raised when lifecycle outcome durability cannot be proven."""

    def __init__(self, classification: Classification = "target") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationOperationLifecycleOutcomeFailureDetail(
            classification
        )


class ExternalPublicationOperationLifecycleOutcomeConflictError(
    ExternalPublicationOperationLifecycleOutcomePersistenceError
):
    """Raised when an occupied lifecycle target is not the exact record."""


class ExternalPublicationOperationLifecycleOutcomeLoadError(
    ExternalPublicationOperationLifecycleOutcomeError
):
    """Raised when a lifecycle sidecar is not an exact canonical record."""

    def __init__(self, classification: Classification = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = ExternalPublicationOperationLifecycleOutcomeFailureDetail(
            classification
        )


@dataclass(frozen=True)
class ExternalPublicationOperationLifecycleOutcome:
    """Immutable, secret-free durable post-handoff lifecycle knowledge."""

    schema_version: Literal["external-publication-operation-lifecycle-outcome.v1"]
    operation_start_sha256: str
    publication_approval_sha256: str
    publication_plan_sha256: str
    operation: Literal["fresh", "resume"]
    state: Literal["completed", "recovery_required"]
    result_kind: Literal["execution_result", "reconciliation", "none"]
    result_sha256: str | None

    def __post_init__(self) -> None:
        _validate_lifecycle_outcome(self)


def serialize_external_publication_operation_lifecycle_outcome_canonical(
    outcome: ExternalPublicationOperationLifecycleOutcome,
) -> str:
    """Serialize one exact lifecycle outcome as compact deterministic JSON."""
    _validate_lifecycle_outcome(outcome)
    try:
        return json.dumps(
            {
                "operation": outcome.operation,
                "operation_start_sha256": outcome.operation_start_sha256,
                "publication_approval_sha256": outcome.publication_approval_sha256,
                "publication_plan_sha256": outcome.publication_plan_sha256,
                "result_kind": outcome.result_kind,
                "result_sha256": outcome.result_sha256,
                "schema_version": outcome.schema_version,
                "state": outcome.state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except ExternalPublicationOperationLifecycleOutcomeError:
        raise
    except Exception:
        _raise_lifecycle("serialization")


def external_publication_operation_lifecycle_outcome_canonical_bytes(
    outcome: ExternalPublicationOperationLifecycleOutcome,
) -> bytes:
    """Return exact canonical lifecycle JSON encoded as UTF-8 bytes."""
    try:
        return serialize_external_publication_operation_lifecycle_outcome_canonical(
            outcome
        ).encode("utf-8")
    except ExternalPublicationOperationLifecycleOutcomeError:
        raise
    except UnicodeError:
        _raise_lifecycle("encoding")
    except Exception:
        _raise_lifecycle("encoding")


def external_publication_operation_lifecycle_outcome_digest(
    outcome: ExternalPublicationOperationLifecycleOutcome,
) -> str:
    """Return SHA-256 over exact canonical lifecycle UTF-8 bytes."""
    return sha256(
        external_publication_operation_lifecycle_outcome_canonical_bytes(outcome)
    ).hexdigest()


def load_external_publication_operation_lifecycle_outcome(
    path: Path,
) -> ExternalPublicationOperationLifecycleOutcome:
    """Read and strictly revalidate one immutable canonical lifecycle record."""
    _validate_load_path(path)
    try:
        with path.open("rb") as handle:
            contents = handle.read(_MAX_LIFECYCLE_BYTES + 1)
    except Exception:
        _raise_load("target")
    if type(contents) is not bytes:
        _raise_load("target")
    if len(contents) > _MAX_LIFECYCLE_BYTES:
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

    outcome = _parse_outcome(value)
    try:
        canonical = external_publication_operation_lifecycle_outcome_canonical_bytes(
            outcome
        )
    except ExternalPublicationOperationLifecycleOutcomeError:
        _raise_load("load")
    except Exception:
        _raise_load("load")
    if canonical != contents:
        _raise_load("noncanonical")
    return outcome


def persist_external_publication_operation_lifecycle_outcome(
    path: Path,
    outcome: ExternalPublicationOperationLifecycleOutcome,
) -> None:
    """Durably append-only persist one exact lifecycle outcome record.

    The canonical bytes are derived and validated first.  A new target is
    created exclusively, written in full, flushed, file-fsynced, closed, and
    then parent-directory-fsynced.  An identical existing target is an
    idempotent success; any other existing content is a fixed conflict and is
    never overwritten, truncated, deleted, or renamed over.  An uncertain
    failure after exclusive creation retains the artifact with no cleanup and
    no retry.
    """
    _validate_persistence_target(path)
    _validate_lifecycle_outcome(outcome)
    try:
        contents = external_publication_operation_lifecycle_outcome_canonical_bytes(
            outcome
        )
    except ExternalPublicationOperationLifecycleOutcomeError:
        raise
    except Exception:
        _raise_persistence("serialization")
    _write_lifecycle_outcome(path, contents)


def run_and_persist_external_publication_operation_lifecycle_outcome(
    *,
    intent_path: Path,
    start_path: Path,
    lifecycle_outcome_path: Path,
    request: (
        ExternalPublicationFreshOperationRequest
        | ExternalPublicationResumeOperationRequest
    ),
    phase291_function: Phase291Function = (
        run_external_publication_operation_start_handoff
    ),
    start_loader: StartLoader = load_external_publication_operation_start,
    start_digest_function: StartDigestFunction = (
        external_publication_operation_start_digest
    ),
    fresh_result_digest_function: FreshResultDigestFunction = (
        external_publication_execution_result_digest
    ),
    reconciliation_digest_function: ReconciliationDigestFunction = (
        external_publication_execution_reconciliation_digest
    ),
) -> ExternalPublicationOperationLifecycleOutcome:
    """Persist durable post-handoff lifecycle evidence for one exact request.

    Phase 291 is called at most once and only when no lifecycle sidecar exists.
    A normal Phase 291 return is converted into one immutable lifecycle outcome
    and persisted append-only.  A Phase 291 exception propagates unchanged with
    no sidecar, and an existing exact sidecar is an idempotent zero-call fast
    path.  No retry, fallback, or automatic continuation is attempted.
    """
    expected_operation, approval_digest, plan_digest = _preflight(
        intent_path=intent_path,
        start_path=start_path,
        lifecycle_outcome_path=lifecycle_outcome_path,
        request=request,
        phase291_function=phase291_function,
        start_loader=start_loader,
        start_digest_function=start_digest_function,
        fresh_result_digest_function=fresh_result_digest_function,
        reconciliation_digest_function=reconciliation_digest_function,
    )

    if _lifecycle_target_present(lifecycle_outcome_path):
        return _load_existing_outcome(
            lifecycle_outcome_path=lifecycle_outcome_path,
            start_path=start_path,
            expected_operation=expected_operation,
            approval_digest=approval_digest,
            plan_digest=plan_digest,
            start_loader=start_loader,
            start_digest_function=start_digest_function,
        )

    result = _call_phase291(
        phase291_function=phase291_function,
        intent_path=intent_path,
        start_path=start_path,
        request=request,
    )
    start = _strict_load_start(start_loader, start_path)
    start_digest = _start_digest_once(start_digest_function, start)
    _validate_start_contract(start)
    _validate_start_lineage(
        start,
        expected_operation=expected_operation,
        approval_digest=approval_digest,
        plan_digest=plan_digest,
    )
    outcome = _derive_outcome(
        request=request,
        result=result,
        start=start,
        start_digest=start_digest,
        expected_operation=expected_operation,
        fresh_result_digest_function=fresh_result_digest_function,
        reconciliation_digest_function=reconciliation_digest_function,
    )
    persist_external_publication_operation_lifecycle_outcome(
        lifecycle_outcome_path, outcome
    )
    return outcome


def _preflight(
    *,
    intent_path: object,
    start_path: object,
    lifecycle_outcome_path: object,
    request: object,
    phase291_function: object,
    start_loader: object,
    start_digest_function: object,
    fresh_result_digest_function: object,
    reconciliation_digest_function: object,
) -> tuple[str, str, str]:
    if (
        type(intent_path) is not _PATH_TYPE
        or type(start_path) is not _PATH_TYPE
        or type(lifecycle_outcome_path) is not _PATH_TYPE
    ):
        _raise_lifecycle("path_type")
    if (
        not callable(phase291_function)
        or not callable(start_loader)
        or not callable(start_digest_function)
        or not callable(fresh_result_digest_function)
        or not callable(reconciliation_digest_function)
    ):
        _raise_lifecycle("configuration")

    _validate_persistence_target(lifecycle_outcome_path)

    if type(request) is ExternalPublicationFreshOperationRequest:
        return _preflight_fresh_request(request)
    if type(request) is ExternalPublicationResumeOperationRequest:
        return _preflight_resume_request(request)
    _raise_lifecycle("request_contract")


def _preflight_fresh_request(
    request: ExternalPublicationFreshOperationRequest,
) -> tuple[str, str, str]:
    if type(request.plan) is not ExternalPublicationPlan:
        _raise_lifecycle("request_contract")
    if type(request.approval) is not ExternalPublicationApproval:
        _raise_lifecycle("request_contract")

    try:
        validate_external_publication_approval(request.plan, request.approval)
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_lifecycle("dependency_error")

    approval_digest = _approval_digest_once(request.approval)
    plan_digest = _plan_digest_once(request.plan)
    if not _is_sha256(approval_digest) or not _is_sha256(plan_digest):
        _raise_lifecycle("request_lineage")
    return "fresh", approval_digest, plan_digest


def _preflight_resume_request(
    request: ExternalPublicationResumeOperationRequest,
) -> tuple[str, str, str]:
    if type(request.approval) is not ExternalPublicationApproval:
        _raise_lifecycle("request_contract")

    approval_digest = _approval_digest_once(request.approval)
    plan_digest = request.approval.publication_plan_sha256
    if not _is_sha256(approval_digest) or not _is_sha256(plan_digest):
        _raise_lifecycle("request_lineage")
    return "resume", approval_digest, plan_digest


def _approval_digest_once(approval: ExternalPublicationApproval) -> object:
    try:
        return external_publication_approval_digest(approval)
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_lifecycle("dependency_error")


def _plan_digest_once(plan: ExternalPublicationPlan) -> object:
    try:
        return external_publication_plan_digest(plan)
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_lifecycle("dependency_error")


def _lifecycle_target_present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except Exception:
        _raise_load("target")


def _load_existing_outcome(
    *,
    lifecycle_outcome_path: Path,
    start_path: Path,
    expected_operation: str,
    approval_digest: str,
    plan_digest: str,
    start_loader: StartLoader,
    start_digest_function: StartDigestFunction,
) -> ExternalPublicationOperationLifecycleOutcome:
    loaded = load_external_publication_operation_lifecycle_outcome(
        lifecycle_outcome_path
    )
    if loaded.operation != expected_operation:
        _raise_lifecycle("request_lineage")
    if (
        loaded.publication_approval_sha256 != approval_digest
        or loaded.publication_plan_sha256 != plan_digest
    ):
        _raise_lifecycle("request_lineage")

    start = _strict_load_start(start_loader, start_path)
    start_digest = _start_digest_once(start_digest_function, start)
    _validate_start_contract(start)
    _validate_start_lineage(
        start,
        expected_operation=expected_operation,
        approval_digest=approval_digest,
        plan_digest=plan_digest,
    )
    if (
        loaded.operation_start_sha256 != start_digest
        or loaded.publication_approval_sha256 != start.publication_approval_sha256
        or loaded.publication_plan_sha256 != start.publication_plan_sha256
        or loaded.operation != start.operation
    ):
        _raise_lifecycle("start_lineage")
    return loaded


def _call_phase291(
    *,
    phase291_function: Phase291Function,
    intent_path: Path,
    start_path: Path,
    request: object,
) -> object:
    try:
        return phase291_function(
            intent_path=intent_path,
            start_path=start_path,
            request=request,
        )
    except _PHASE291_KNOWN_ERRORS:
        raise
    except Exception:
        _raise_lifecycle("dependency_error")


def _strict_load_start(loader: StartLoader, start_path: Path) -> object:
    try:
        start = loader(start_path)
    except ExternalPublicationOperationStartError:
        raise
    except Exception:
        _raise_lifecycle("dependency_error")
    if type(start) is not ExternalPublicationOperationStart:
        _raise_lifecycle("start_contract")
    return start


def _start_digest_once(
    digest_function: StartDigestFunction,
    start: object,
) -> str:
    try:
        digest = digest_function(start)  # type: ignore[arg-type]
    except ExternalPublicationOperationStartError:
        raise
    except Exception:
        _raise_lifecycle("dependency_error")
    if not _is_sha256(digest):
        _raise_lifecycle("start_lineage")
    return digest  # type: ignore[return-value]


def _validate_start_contract(start: object) -> None:
    if type(start) is not ExternalPublicationOperationStart:
        _raise_lifecycle("start_contract")
    try:
        if (
            type(start.schema_version) is not str  # type: ignore[union-attr]
            or start.schema_version != _START_SCHEMA_VERSION  # type: ignore[union-attr]
        ):
            _raise_lifecycle("start_contract")
        if not _is_sha256(start.operation_intent_sha256):  # type: ignore[union-attr]
            _raise_lifecycle("start_contract")
        if not _is_sha256(start.publication_approval_sha256):  # type: ignore[union-attr]
            _raise_lifecycle("start_contract")
        if not _is_sha256(start.publication_plan_sha256):  # type: ignore[union-attr]
            _raise_lifecycle("start_contract")
        if (
            type(start.operation) is not str  # type: ignore[union-attr]
            or start.operation not in _OPERATIONS  # type: ignore[union-attr]
        ):
            _raise_lifecycle("start_contract")
        if (
            type(start.state) is not str  # type: ignore[union-attr]
            or start.state != "started"  # type: ignore[union-attr]
        ):
            _raise_lifecycle("start_contract")
    except ExternalPublicationOperationLifecycleOutcomeError:
        raise
    except Exception:
        _raise_lifecycle("start_contract")


def _validate_start_lineage(
    start: object,
    *,
    expected_operation: str,
    approval_digest: str,
    plan_digest: str,
) -> None:
    try:
        operation = start.operation  # type: ignore[union-attr]
        start_approval = start.publication_approval_sha256  # type: ignore[union-attr]
        start_plan = start.publication_plan_sha256  # type: ignore[union-attr]
    except Exception:
        _raise_lifecycle("start_contract")
    if operation != expected_operation:
        _raise_lifecycle("start_lineage")
    if start_approval != approval_digest or start_plan != plan_digest:
        _raise_lifecycle("start_lineage")


def _derive_outcome(
    *,
    request: object,
    result: object,
    start: object,
    start_digest: str,
    expected_operation: str,
    fresh_result_digest_function: FreshResultDigestFunction,
    reconciliation_digest_function: ReconciliationDigestFunction,
) -> ExternalPublicationOperationLifecycleOutcome:
    if type(result) is ExternalPublicationOperationStartAcquisition:
        return _derive_already_acquired(
            result=result,
            start=start,
            start_digest=start_digest,
            expected_operation=expected_operation,
        )
    if type(result) is ExternalPublicationExecutionResult:
        return _derive_fresh_result(
            request=request,
            result=result,
            start=start,
            start_digest=start_digest,
            digest_function=fresh_result_digest_function,
        )
    if type(result) is ExternalPublicationExecutionReconciliation:
        return _derive_resume_result(
            request=request,
            result=result,
            start=start,
            start_digest=start_digest,
            digest_function=reconciliation_digest_function,
        )
    _raise_lifecycle("handoff_result_contract")


def _derive_fresh_result(
    *,
    request: object,
    result: object,
    start: object,
    start_digest: str,
    digest_function: FreshResultDigestFunction,
) -> ExternalPublicationOperationLifecycleOutcome:
    if type(request) is not ExternalPublicationFreshOperationRequest:
        _raise_lifecycle("handoff_result_contract")
    _validate_execution_result(result)
    try:
        result_approval = result.publication_approval_sha256  # type: ignore[union-attr]
        result_plan = result.publication_plan_sha256  # type: ignore[union-attr]
        start_approval = start.publication_approval_sha256  # type: ignore[union-attr]
        start_plan = start.publication_plan_sha256  # type: ignore[union-attr]
    except Exception:
        _raise_lifecycle("handoff_result_contract")
    if result_approval != start_approval or result_plan != start_plan:
        _raise_lifecycle("result_lineage")
    result_digest = _result_digest_once(digest_function, result)
    return ExternalPublicationOperationLifecycleOutcome(
        schema_version=_LIFECYCLE_SCHEMA_VERSION,
        operation_start_sha256=start_digest,
        publication_approval_sha256=start_approval,  # type: ignore[arg-type]
        publication_plan_sha256=start_plan,  # type: ignore[arg-type]
        operation="fresh",
        state="completed",
        result_kind="execution_result",
        result_sha256=result_digest,
    )


def _derive_resume_result(
    *,
    request: object,
    result: object,
    start: object,
    start_digest: str,
    digest_function: ReconciliationDigestFunction,
) -> ExternalPublicationOperationLifecycleOutcome:
    if type(request) is not ExternalPublicationResumeOperationRequest:
        _raise_lifecycle("handoff_result_contract")
    _validate_reconciliation(result)
    try:
        status = result.status  # type: ignore[union-attr]
        start_approval = start.publication_approval_sha256  # type: ignore[union-attr]
        start_plan = start.publication_plan_sha256  # type: ignore[union-attr]
    except Exception:
        _raise_lifecycle("handoff_result_contract")
    if status == "matched":
        state: str = "completed"
    elif status == "lineage_mismatch":
        state = "recovery_required"
    else:
        _raise_lifecycle("handoff_result_contract")
    result_digest = _result_digest_once(digest_function, result)
    return ExternalPublicationOperationLifecycleOutcome(
        schema_version=_LIFECYCLE_SCHEMA_VERSION,
        operation_start_sha256=start_digest,
        publication_approval_sha256=start_approval,  # type: ignore[arg-type]
        publication_plan_sha256=start_plan,  # type: ignore[arg-type]
        operation="resume",
        state=state,  # type: ignore[arg-type]
        result_kind="reconciliation",
        result_sha256=result_digest,
    )


def _derive_already_acquired(
    *,
    result: object,
    start: object,
    start_digest: str,
    expected_operation: str,
) -> ExternalPublicationOperationLifecycleOutcome:
    if type(result) is not ExternalPublicationOperationStartAcquisition:
        _raise_lifecycle("handoff_result_contract")
    try:
        status = result.status  # type: ignore[union-attr]
        embedded = result.start  # type: ignore[union-attr]
    except Exception:
        _raise_lifecycle("handoff_result_contract")
    if type(status) is not str or status not in _ACQUISITION_STATUSES:
        _raise_lifecycle("handoff_result_contract")
    if status != "already_acquired":
        _raise_lifecycle("handoff_result_contract")
    if type(embedded) is not ExternalPublicationOperationStart:
        _raise_lifecycle("handoff_result_contract")
    if embedded != start:
        _raise_lifecycle("start_lineage")
    if embedded.operation != expected_operation:
        _raise_lifecycle("start_lineage")
    return ExternalPublicationOperationLifecycleOutcome(
        schema_version=_LIFECYCLE_SCHEMA_VERSION,
        operation_start_sha256=start_digest,
        publication_approval_sha256=embedded.publication_approval_sha256,
        publication_plan_sha256=embedded.publication_plan_sha256,
        operation=embedded.operation,
        state="recovery_required",
        result_kind="none",
        result_sha256=None,
    )


def _validate_execution_result(result: object) -> None:
    if type(result) is not ExternalPublicationExecutionResult:
        _raise_lifecycle("handoff_result_contract")
    try:
        ExternalPublicationExecutionResult(
            schema_version=result.schema_version,  # type: ignore[union-attr]
            regeneration_id=result.regeneration_id,  # type: ignore[union-attr]
            publication_attempt_claim_sha256=(  # type: ignore[union-attr]
                result.publication_attempt_claim_sha256
            ),
            publication_plan_sha256=(  # type: ignore[union-attr]
                result.publication_plan_sha256
            ),
            publication_approval_sha256=(  # type: ignore[union-attr]
                result.publication_approval_sha256
            ),
            business_output_sha256=result.business_output_sha256,  # type: ignore[union-attr]
            output_byte_length=result.output_byte_length,  # type: ignore[union-attr]
            provider=result.provider,  # type: ignore[union-attr]
            publication_target_sha256=(  # type: ignore[union-attr]
                result.publication_target_sha256
            ),
            publication_id=result.publication_id,  # type: ignore[union-attr]
            status=result.status,  # type: ignore[union-attr]
        )
    except Exception:
        _raise_lifecycle("handoff_result_contract")
    try:
        status = result.status  # type: ignore[union-attr]
    except Exception:
        _raise_lifecycle("handoff_result_contract")
    if type(status) is not str or status != "published":
        _raise_lifecycle("handoff_result_contract")


def _validate_reconciliation(result: object) -> None:
    if type(result) is not ExternalPublicationExecutionReconciliation:
        _raise_lifecycle("handoff_result_contract")
    try:
        ExternalPublicationExecutionReconciliation(
            schema_version=result.schema_version,  # type: ignore[union-attr]
            claim_sha256=result.claim_sha256,  # type: ignore[union-attr]
            execution_evidence_sha256=(  # type: ignore[union-attr]
                result.execution_evidence_sha256
            ),
            status=result.status,  # type: ignore[union-attr]
            mismatched_fields=result.mismatched_fields,  # type: ignore[union-attr]
        )
    except Exception:
        _raise_lifecycle("handoff_result_contract")
    try:
        status = result.status  # type: ignore[union-attr]
    except Exception:
        _raise_lifecycle("handoff_result_contract")
    if type(status) is not str or status not in {"matched", "lineage_mismatch"}:
        _raise_lifecycle("handoff_result_contract")


def _result_digest_once(digest_function: Callable[..., object], result: object) -> str:
    try:
        digest = digest_function(result)
    except (
        ExternalPublicationExecutionEvidenceError,
        ExternalPublicationExecutionReconciliationEvidenceError,
    ):
        raise
    except Exception:
        _raise_lifecycle("dependency_error")
    if not _is_sha256(digest):
        _raise_lifecycle("result_digest")
    return digest  # type: ignore[return-value]


def _validate_lifecycle_outcome(outcome: object) -> None:
    if type(outcome) is not ExternalPublicationOperationLifecycleOutcome:
        _raise_lifecycle("configuration")
    try:
        _check_lifecycle_outcome(outcome)
    except ExternalPublicationOperationLifecycleOutcomeError:
        raise
    except Exception:
        _raise_lifecycle("configuration")


def _check_lifecycle_outcome(outcome: object) -> None:
    schema_version = outcome.schema_version  # type: ignore[union-attr]
    start_digest = outcome.operation_start_sha256  # type: ignore[union-attr]
    approval_digest = outcome.publication_approval_sha256  # type: ignore[union-attr]
    plan_digest = outcome.publication_plan_sha256  # type: ignore[union-attr]
    operation = outcome.operation  # type: ignore[union-attr]
    state = outcome.state  # type: ignore[union-attr]
    result_kind = outcome.result_kind  # type: ignore[union-attr]
    result_digest = outcome.result_sha256  # type: ignore[union-attr]

    if type(schema_version) is not str or schema_version != _LIFECYCLE_SCHEMA_VERSION:
        _raise_lifecycle("configuration")
    if not _is_sha256(start_digest):
        _raise_lifecycle("configuration")
    if not _is_sha256(approval_digest):
        _raise_lifecycle("configuration")
    if not _is_sha256(plan_digest):
        _raise_lifecycle("configuration")
    if type(operation) is not str or operation not in _OPERATIONS:
        _raise_lifecycle("configuration")
    if type(state) is not str or state not in _STATES:
        _raise_lifecycle("configuration")
    if type(result_kind) is not str or result_kind not in _RESULT_KINDS:
        _raise_lifecycle("configuration")

    if result_kind == "none":
        if result_digest is not None:
            _raise_lifecycle("configuration")
        if state != "recovery_required":
            _raise_lifecycle("configuration")
        return

    if not _is_sha256(result_digest):
        _raise_lifecycle("configuration")
    if state == "completed":
        if operation == "fresh" and result_kind != "execution_result":
            _raise_lifecycle("configuration")
        if operation == "resume" and result_kind != "reconciliation":
            _raise_lifecycle("configuration")
        return
    if operation != "resume" or result_kind != "reconciliation":
        _raise_lifecycle("configuration")


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
    except ExternalPublicationOperationLifecycleOutcomePersistenceError:
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
    except ExternalPublicationOperationLifecycleOutcomeLoadError:
        raise
    except Exception:
        _raise_load("target")


def _write_lifecycle_outcome(path: Path, contents: bytes) -> None:
    try:
        handle = path.open("xb")
    except FileExistsError:
        _verify_existing_outcome(path, contents)
        return
    except OSError as error:
        if error.errno == errno.EEXIST:
            _verify_existing_outcome(path, contents)
            return
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    _persist_new_outcome(handle, path.parent, contents)


def _persist_new_outcome(handle: object, directory: Path, contents: bytes) -> None:
    try:
        with _lifecycle_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short lifecycle outcome write")
            active_handle.flush()  # type: ignore[attr-defined]
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_lifecycle_directory(directory)
    except Exception:
        _raise_persistence("ambiguous")


def _verify_existing_outcome(path: Path, contents: bytes) -> None:
    try:
        if (
            path.is_symlink()
            or not path.exists()
            or path.is_dir()
            or not path.is_file()
        ):
            _raise_persistence("target")
        with path.open("rb") as handle:
            existing = handle.read(_MAX_LIFECYCLE_BYTES + 1)
        if type(existing) is not bytes:
            _raise_persistence("target")
    except ExternalPublicationOperationLifecycleOutcomePersistenceError:
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
        with _lifecycle_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")
    try:
        _fsync_lifecycle_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


@contextmanager
def _lifecycle_handle_scope(handle: object) -> Iterator[object]:
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
            raise OSError("lifecycle handle cannot close")
        close()


def _fsync_lifecycle_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_outcome(value: object) -> ExternalPublicationOperationLifecycleOutcome:
    if type(value) is not dict or frozenset(value) != _LIFECYCLE_KEYS:
        _raise_load("keys")
    try:
        return ExternalPublicationOperationLifecycleOutcome(
            schema_version=value["schema_version"],
            operation_start_sha256=value["operation_start_sha256"],
            publication_approval_sha256=value["publication_approval_sha256"],
            publication_plan_sha256=value["publication_plan_sha256"],
            operation=value["operation"],
            state=value["state"],
            result_kind=value["result_kind"],
            result_sha256=value["result_sha256"],
        )
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


def _raise_lifecycle(classification: Classification) -> NoReturn:
    raise ExternalPublicationOperationLifecycleOutcomeCompatibilityError(
        classification
    ) from None


def _raise_persistence(classification: Classification) -> NoReturn:
    raise ExternalPublicationOperationLifecycleOutcomePersistenceError(
        classification
    ) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationOperationLifecycleOutcomeConflictError(
        "conflict"
    ) from None


def _raise_load(classification: Classification) -> NoReturn:
    raise ExternalPublicationOperationLifecycleOutcomeLoadError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationOperationLifecycleOutcome",
    "ExternalPublicationOperationLifecycleOutcomeCompatibilityError",
    "ExternalPublicationOperationLifecycleOutcomeConflictError",
    "ExternalPublicationOperationLifecycleOutcomeError",
    "ExternalPublicationOperationLifecycleOutcomeFailureDetail",
    "ExternalPublicationOperationLifecycleOutcomeLoadError",
    "ExternalPublicationOperationLifecycleOutcomePersistenceError",
    "external_publication_operation_lifecycle_outcome_canonical_bytes",
    "external_publication_operation_lifecycle_outcome_digest",
    "load_external_publication_operation_lifecycle_outcome",
    "persist_external_publication_operation_lifecycle_outcome",
    "run_and_persist_external_publication_operation_lifecycle_outcome",
    "serialize_external_publication_operation_lifecycle_outcome_canonical",
]
