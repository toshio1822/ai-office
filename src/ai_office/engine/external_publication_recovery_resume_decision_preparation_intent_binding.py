"""Durable Phase 302 preparation-to-resume-intent provenance binding.

Phase 302 consumes the exact Phase 299 recovery route, Phase 300 operator
decision, and Phase 301 decision-bound preparation.  It establishes a new
immutable binding rooted directly in the Phase 301 preparation digest before
it resolves or materializes the exact Phase 289 ``resume`` operation intent.

The binding is recovery authority for this expected intent identity only.  It
does not mean that an intent was already materialized, that a start is
authorized or acquired, that execution is authorized, or that reconciliation
or replay may occur.  No Phase 290 start, execution, provider, transport, or
network work is performed here.
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

from .external_publication_operation_intent import (
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationIntentError,
    external_publication_operation_intent_digest,
    load_external_publication_operation_intent,
    persist_external_publication_operation_intent,
)
from .external_publication_operation_start import ExternalPublicationOperationStartError
from .external_publication_recovery_resume_decision import (
    ExternalPublicationRecoveryResumeDecision,
    ExternalPublicationRecoveryResumeDecisionError,
    external_publication_recovery_resume_decision_digest,
    load_external_publication_recovery_resume_decision,
)
from .external_publication_recovery_resume_decision_preparation import (
    ExternalPublicationRecoveryResumeDecisionPreparation,
    ExternalPublicationRecoveryResumeDecisionPreparationError,
    external_publication_recovery_resume_decision_preparation_digest,
    load_external_publication_recovery_resume_decision_preparation,
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

_BINDING_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation intent binding "
    "is blocked"
)
_PERSISTENCE_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation intent binding "
    "persistence failed"
)
_LOAD_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation intent binding "
    "could not be loaded"
)
_BINDING_SCHEMA_VERSION = (
    "external-publication-recovery-resume-decision-preparation-intent-binding.v1"
)
_DECISION_SCHEMA_VERSION = "external-publication-recovery-resume-decision.v1"
_PREPARATION_SCHEMA_VERSION = (
    "external-publication-recovery-resume-decision-preparation.v1"
)
_INTENT_SCHEMA_VERSION = "external-publication-operation-intent.v1"
_BINDING_KEYS = frozenset(
    {
        "decision_preparation_sha256",
        "operation",
        "operation_intent_sha256",
        "previous_recovery_kind",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "recovery_kind",
        "recovery_resume_decision_sha256",
        "result_kind",
        "result_sha256",
        "schema_version",
        "source_operation",
        "state",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_MAX_BINDING_BYTES = 4096
_SOURCE_OPERATIONS = frozenset({"fresh", "resume"})
_RECOVERY_KINDS = frozenset({"already_acquired", "reconciliation_mismatch"})
_RESULT_KINDS = frozenset({"reconciliation", "none"})
_OPERATION = "resume"
_STATE = "authorized"
_DECISION = "authorize_resume_preparation"
_PREPARATION_STATE = "prepared"
_DECISION_STATE = "decided"
_NONE_RESULT_KIND = "none"
_RECONCILIATION_RESULT_KIND = "reconciliation"
_ALREADY_ACQUIRED_RECOVERY_KIND = "already_acquired"
_MISMATCH_RECOVERY_KIND = "reconciliation_mismatch"
_DECISION_FILENAME_PREFIX = "external-publication-recovery-resume-decision-"
_PREPARATION_FILENAME_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-"
)
_BINDING_FILENAME_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-intent-binding-"
)
_INTENT_FILENAME_PREFIX = "external-publication-operation-intent-"
_FILENAME_SUFFIX = ".json"

Classification = Literal[
    "configuration",
    "path_type",
    "dependency_error",
    "predecessor_contract",
    "predecessor_lineage",
    "materialization_not_required",
    "materialization_not_authorized",
    "decision_digest",
    "preparation_digest",
    "intent_contract",
    "intent_digest",
    "intent_conflict",
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
PreparationLoader = Callable[[Path], object]
PreparationDigestFunction = Callable[
    [ExternalPublicationRecoveryResumeDecisionPreparation], object
]
IntentLoader = Callable[[Path], object]
IntentDigestFunction = Callable[[ExternalPublicationOperationIntent], object]
IntentPersistFunction = Callable[[Path, ExternalPublicationOperationIntent], object]

_KNOWN_PHASE299_ERRORS = (
    ExternalPublicationRecoveryResumeOutcomeRoutingError,
    ExternalPublicationRecoveryResumeIntentBindingError,
    ExternalPublicationRecoveryResumeStartAuthorizationError,
    ExternalPublicationRecoveryResumeOutcomeError,
    ExternalPublicationOperationStartError,
)
_KNOWN_DECISION_ERRORS = (ExternalPublicationRecoveryResumeDecisionError,)
_KNOWN_PREPARATION_ERRORS = (ExternalPublicationRecoveryResumeDecisionPreparationError,)
_KNOWN_INTENT_ERRORS = (ExternalPublicationOperationIntentError,)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingFailureDetail:
    """Detail-safe classification for one Phase 302 failure."""

    classification: Classification


_FailureDetail = (
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingFailureDetail
)


class ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError(
    ValueError
):
    """Raised when a Phase 302 binding request or record is not exact."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_BINDING_ERROR_MESSAGE)
        self.detail = _FailureDetail(classification)


class ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError(  # noqa: E501
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError
):
    """Raised when a path, predecessor, binding, or intent is incompatible."""


class ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError(
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError
):
    """Raised when Phase 302 binding durability cannot be proven."""

    def __init__(self, classification: Classification = "target") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = _FailureDetail(classification)


class ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingConflictError(
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError
):
    """Raised when an occupied binding target is not the exact record."""


class ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingLoadError(
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError
):
    """Raised when a binding sidecar is not an exact canonical record."""

    def __init__(self, classification: Classification = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = _FailureDetail(classification)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding:
    """Immutable, secret-free Phase 302 preparation-intent binding.

    ``authorized`` means only that the exact Phase 301 preparation authorizes
    the exact expected Phase 289 ``resume`` intent identity.  It records no
    start authorization, start acquisition, execution, reconciliation, or
    replay permission.
    """

    schema_version: Literal[
        "external-publication-recovery-resume-decision-preparation-intent-binding.v1"
    ]
    decision_preparation_sha256: str
    recovery_resume_decision_sha256: str
    publication_approval_sha256: str
    publication_plan_sha256: str
    operation_intent_sha256: str
    source_operation: Literal["fresh", "resume"]
    previous_recovery_kind: Literal["already_acquired", "reconciliation_mismatch"]
    recovery_kind: Literal["already_acquired", "reconciliation_mismatch"]
    result_kind: Literal["reconciliation", "none"]
    result_sha256: str | None
    operation: Literal["resume"]
    state: Literal["authorized"]

    def __post_init__(self) -> None:
        _validate_binding(self)


def serialize_external_publication_recovery_resume_decision_preparation_intent_binding_canonical(  # noqa: E501
    binding: ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
) -> str:
    """Serialize one exact Phase 302 binding as compact canonical JSON."""
    _validate_binding(binding)
    try:
        return json.dumps(
            {
                "decision_preparation_sha256": binding.decision_preparation_sha256,
                "operation": binding.operation,
                "operation_intent_sha256": binding.operation_intent_sha256,
                "previous_recovery_kind": binding.previous_recovery_kind,
                "publication_approval_sha256": binding.publication_approval_sha256,
                "publication_plan_sha256": binding.publication_plan_sha256,
                "recovery_kind": binding.recovery_kind,
                "recovery_resume_decision_sha256": (
                    binding.recovery_resume_decision_sha256
                ),
                "result_kind": binding.result_kind,
                "result_sha256": binding.result_sha256,
                "schema_version": binding.schema_version,
                "source_operation": binding.source_operation,
                "state": binding.state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError:
        raise
    except Exception:
        _raise_binding("serialization")


def external_publication_recovery_resume_decision_preparation_intent_binding_canonical_bytes(  # noqa: E501
    binding: ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
) -> bytes:
    """Return exact canonical Phase 302 JSON encoded as UTF-8 bytes."""
    try:
        return serialize_external_publication_recovery_resume_decision_preparation_intent_binding_canonical(  # noqa: E501
            binding
        ).encode("utf-8")
    except ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError:
        raise
    except UnicodeError:
        _raise_binding("encoding")
    except Exception:
        _raise_binding("encoding")


def external_publication_recovery_resume_decision_preparation_intent_binding_digest(
    binding: ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
) -> str:
    """Return SHA-256 over exact canonical Phase 302 binding bytes."""
    return sha256(
        external_publication_recovery_resume_decision_preparation_intent_binding_canonical_bytes(
            binding
        )
    ).hexdigest()


def load_external_publication_recovery_resume_decision_preparation_intent_binding(
    path: Path,
) -> ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding:
    """Strict-load one immutable canonical Phase 302 binding."""
    _validate_load_path(path)
    try:
        with path.open("rb") as handle:
            contents = handle.read(_MAX_BINDING_BYTES + 1)
    except Exception:
        _raise_load("target")
    if type(contents) is not bytes:
        _raise_load("target")
    if len(contents) > _MAX_BINDING_BYTES:
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

    binding = _parse_binding(value)
    try:
        canonical = external_publication_recovery_resume_decision_preparation_intent_binding_canonical_bytes(  # noqa: E501
            binding
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError:
        _raise_load("load")
    except Exception:
        _raise_load("load")
    if canonical != contents:
        _raise_load("noncanonical")
    return binding


def persist_external_publication_recovery_resume_decision_preparation_intent_binding(
    path: Path,
    binding: ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
) -> None:
    """Durably append-only persist one exact Phase 302 binding.

    Canonical bytes and the concrete target contract are established before
    exclusive creation.  New artifacts are fully written, flushed, file-
    fsynced, safely closed, and parent-directory-fsynced.  Exact occupied
    bytes are idempotent; every other occupied byte sequence is a fixed
    conflict.  Any uncertain post-create failure retains the artifact and is
    never cleaned up, retried, or rewritten.
    """
    _validate_persistence_target(path)
    _validate_binding(binding)
    try:
        contents = external_publication_recovery_resume_decision_preparation_intent_binding_canonical_bytes(  # noqa: E501
            binding
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError:
        raise
    except Exception:
        _raise_persistence("serialization")
    _write_binding(path, contents)


def materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent(  # noqa: E501
    *,
    resume_intent_binding_path: Path,
    phase299_function: Phase299Function = route_external_publication_recovery_resume_outcome,  # noqa: E501
    decision_loader: DecisionLoader = load_external_publication_recovery_resume_decision,  # noqa: E501
    decision_digest_function: DecisionDigestFunction = external_publication_recovery_resume_decision_digest,  # noqa: E501
    preparation_loader: PreparationLoader = load_external_publication_recovery_resume_decision_preparation,  # noqa: E501
    preparation_digest_function: PreparationDigestFunction = external_publication_recovery_resume_decision_preparation_digest,  # noqa: E501
    intent_loader: IntentLoader = load_external_publication_operation_intent,
    intent_digest_function: IntentDigestFunction = external_publication_operation_intent_digest,  # noqa: E501
    intent_persist_function: IntentPersistFunction = persist_external_publication_operation_intent,  # noqa: E501
) -> ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding:
    """Bind the exact Phase 301 preparation before handling its Phase 289 intent.

    The caller owns only the existing Phase 295 binding path, which supplies
    the namespace root.  Phase 299 is called exactly once with only that path;
    all Phase 300, Phase 301, Phase 302, and Phase 289 target paths are derived
    internally.  The new Phase 302 binding is resolved or durably persisted
    before any Phase 289 intent loader or persistence call.
    """
    _preflight(
        resume_intent_binding_path=resume_intent_binding_path,
        phase299_function=phase299_function,
        decision_loader=decision_loader,
        decision_digest_function=decision_digest_function,
        preparation_loader=preparation_loader,
        preparation_digest_function=preparation_digest_function,
        intent_loader=intent_loader,
        intent_digest_function=intent_digest_function,
        intent_persist_function=intent_persist_function,
    )

    routed = _call_phase299(
        phase299_function,
        resume_intent_binding_path=resume_intent_binding_path,
    )

    if type(routed) is ExternalPublicationRecoveryResumeOutcome:
        _reconstruct_model(
            ExternalPublicationRecoveryResumeOutcome,
            routed,
            "predecessor_contract",
        )
        if routed.state != "completed":
            _raise_binding("predecessor_contract")
        _raise_binding("materialization_not_required")

    if type(routed) is not ExternalPublicationRecoveryResumeDecisionRequired:
        _raise_binding("predecessor_contract")
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecisionRequired,
        routed,
        "predecessor_contract",
    )

    decision_path = _derive_decision_path(
        resume_intent_binding_path,
        routed.recovery_resume_outcome_sha256,
    )
    loaded_decision = _strict_load_decision(decision_loader, decision_path)
    if type(loaded_decision) is not ExternalPublicationRecoveryResumeDecision:
        _raise_binding("predecessor_contract")
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecision,
        loaded_decision,
        "predecessor_contract",
    )
    _validate_decision_lineage(routed, loaded_decision)

    if loaded_decision.decision == "stop":
        _raise_binding("materialization_not_authorized")
    if loaded_decision.decision != _DECISION:
        _raise_binding("predecessor_contract")

    decision_digest = _decision_digest_once(
        decision_digest_function,
        loaded_decision,
    )

    preparation_path = _derive_preparation_path(
        resume_intent_binding_path,
        decision_digest,
    )
    loaded_preparation = _strict_load_preparation(
        preparation_loader,
        preparation_path,
    )
    if (
        type(loaded_preparation)
        is not ExternalPublicationRecoveryResumeDecisionPreparation
    ):
        _raise_binding("predecessor_contract")
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecisionPreparation,
        loaded_preparation,
        "predecessor_contract",
    )
    _validate_preparation_lineage(loaded_decision, loaded_preparation, decision_digest)

    preparation_digest = _preparation_digest_once(
        preparation_digest_function,
        loaded_preparation,
    )

    expected_intent = _construct_expected_intent(loaded_preparation)
    _reconstruct_model(
        ExternalPublicationOperationIntent,
        expected_intent,
        "intent_contract",
    )
    _validate_intent_contract(expected_intent)
    intent_digest = _intent_digest_once(intent_digest_function, expected_intent)

    constructed = ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding(
        schema_version=_BINDING_SCHEMA_VERSION,  # type: ignore[arg-type]
        decision_preparation_sha256=preparation_digest,
        recovery_resume_decision_sha256=(
            loaded_preparation.recovery_resume_decision_sha256
        ),
        publication_approval_sha256=loaded_preparation.publication_approval_sha256,
        publication_plan_sha256=loaded_preparation.publication_plan_sha256,
        operation_intent_sha256=intent_digest,
        source_operation=loaded_preparation.source_operation,
        previous_recovery_kind=loaded_preparation.previous_recovery_kind,
        recovery_kind=loaded_preparation.recovery_kind,
        result_kind=loaded_preparation.result_kind,
        result_sha256=loaded_preparation.result_sha256,
        operation=_OPERATION,  # type: ignore[arg-type]
        state=_STATE,  # type: ignore[arg-type]
    )

    binding_path = _derive_binding_path(
        resume_intent_binding_path,
        preparation_digest,
    )
    if _binding_target_present(binding_path):
        binding = _resolve_existing_binding(binding_path, constructed)
    else:
        try:
            persist_external_publication_recovery_resume_decision_preparation_intent_binding(
                binding_path,
                constructed,
            )
        except ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError:
            raise
        except Exception:
            _raise_persistence("dependency_error")
        binding = constructed

    intent_path = _derive_intent_path(resume_intent_binding_path, intent_digest)
    _materialize_intent(
        intent_path=intent_path,
        expected_intent=expected_intent,
        intent_loader=intent_loader,
        intent_persist_function=intent_persist_function,
    )
    return binding


def _preflight(
    *,
    resume_intent_binding_path: object,
    phase299_function: object,
    decision_loader: object,
    decision_digest_function: object,
    preparation_loader: object,
    preparation_digest_function: object,
    intent_loader: object,
    intent_digest_function: object,
    intent_persist_function: object,
) -> None:
    if type(resume_intent_binding_path) is not _PATH_TYPE:
        _raise_binding("path_type")
    for dependency in (
        phase299_function,
        decision_loader,
        decision_digest_function,
        preparation_loader,
        preparation_digest_function,
        intent_loader,
        intent_digest_function,
        intent_persist_function,
    ):
        if not callable(dependency):
            _raise_binding("configuration")


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
        _raise_binding("dependency_error")


def _strict_load_decision(loader: DecisionLoader, path: Path) -> object:
    try:
        return loader(path)
    except _KNOWN_DECISION_ERRORS:
        raise
    except Exception:
        _raise_binding("dependency_error")


def _decision_digest_once(
    digest_function: DecisionDigestFunction,
    decision: object,
) -> str:
    try:
        digest = digest_function(decision)  # type: ignore[arg-type]
    except _KNOWN_DECISION_ERRORS:
        raise
    except Exception:
        _raise_binding("dependency_error")
    if not _is_sha256(digest):
        _raise_binding("decision_digest")
    return digest


def _strict_load_preparation(loader: PreparationLoader, path: Path) -> object:
    try:
        return loader(path)
    except _KNOWN_PREPARATION_ERRORS:
        raise
    except Exception:
        _raise_binding("dependency_error")


def _preparation_digest_once(
    digest_function: PreparationDigestFunction,
    preparation: object,
) -> str:
    try:
        digest = digest_function(preparation)  # type: ignore[arg-type]
    except _KNOWN_PREPARATION_ERRORS:
        raise
    except Exception:
        _raise_binding("dependency_error")
    if not _is_sha256(digest):
        _raise_binding("preparation_digest")
    return digest


def _construct_expected_intent(
    preparation: object,
) -> ExternalPublicationOperationIntent:
    try:
        return ExternalPublicationOperationIntent(
            schema_version=_INTENT_SCHEMA_VERSION,  # type: ignore[arg-type]
            publication_approval_sha256=preparation.publication_approval_sha256,
            publication_plan_sha256=preparation.publication_plan_sha256,
            operation=_OPERATION,  # type: ignore[arg-type]
        )
    except ExternalPublicationOperationIntentError:
        _raise_binding("intent_contract")
    except Exception:
        _raise_binding("intent_contract")


def _validate_intent_contract(intent: object) -> None:
    if type(intent) is not ExternalPublicationOperationIntent:
        _raise_binding("intent_contract")
    try:
        values = {
            field.name: getattr(intent, field.name)
            for field in fields(ExternalPublicationOperationIntent)
        }
        ExternalPublicationOperationIntent(**values)  # type: ignore[arg-type]
    except Exception:
        _raise_binding("intent_contract")
    if intent.operation != _OPERATION:  # type: ignore[union-attr]
        _raise_binding("intent_contract")


def _intent_digest_once(
    digest_function: IntentDigestFunction,
    intent: object,
) -> str:
    try:
        digest = digest_function(intent)  # type: ignore[arg-type]
    except _KNOWN_INTENT_ERRORS:
        raise
    except Exception:
        _raise_binding("dependency_error")
    if not _is_sha256(digest):
        _raise_binding("intent_digest")
    return digest


def _materialize_intent(
    *,
    intent_path: Path,
    expected_intent: ExternalPublicationOperationIntent,
    intent_loader: IntentLoader,
    intent_persist_function: IntentPersistFunction,
) -> None:
    if _intent_target_present(intent_path):
        loaded = _strict_load_intent(intent_loader, intent_path)
        _reconstruct_model(
            ExternalPublicationOperationIntent,
            loaded,
            "intent_contract",
        )
        _validate_intent_contract(loaded)
        if loaded != expected_intent:
            _raise_binding("intent_conflict")
        return
    try:
        intent_persist_function(intent_path, expected_intent)
    except _KNOWN_INTENT_ERRORS:
        raise
    except Exception:
        _raise_binding("dependency_error")


def _strict_load_intent(loader: IntentLoader, path: Path) -> object:
    try:
        loaded = loader(path)
    except _KNOWN_INTENT_ERRORS:
        raise
    except Exception:
        _raise_binding("dependency_error")
    if type(loaded) is not ExternalPublicationOperationIntent:
        _raise_binding("intent_contract")
    return loaded


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
            _raise_binding("predecessor_lineage")
    if decision.operation != _OPERATION:
        _raise_binding("predecessor_lineage")
    if decision.state != _DECISION_STATE:
        _raise_binding("predecessor_lineage")


def _validate_preparation_lineage(
    decision: ExternalPublicationRecoveryResumeDecision,
    preparation: ExternalPublicationRecoveryResumeDecisionPreparation,
    decision_digest: str,
) -> None:
    pairs = (
        ("recovery_resume_decision_sha256", decision_digest),
        (
            "recovery_resume_outcome_sha256",
            decision.recovery_resume_outcome_sha256,
        ),
        (
            "resume_start_authorization_sha256",
            decision.resume_start_authorization_sha256,
        ),
        ("resume_intent_binding_sha256", decision.resume_intent_binding_sha256),
        ("operation_intent_sha256", decision.operation_intent_sha256),
        ("operation_start_sha256", decision.operation_start_sha256),
        ("publication_approval_sha256", decision.publication_approval_sha256),
        ("publication_plan_sha256", decision.publication_plan_sha256),
        ("source_operation", decision.source_operation),
        ("previous_recovery_kind", decision.previous_recovery_kind),
        ("recovery_kind", decision.recovery_kind),
        ("result_kind", decision.result_kind),
        ("result_sha256", decision.result_sha256),
    )
    for field_name, expected in pairs:
        if getattr(preparation, field_name) != expected:
            _raise_binding("predecessor_lineage")
    if preparation.decision != _DECISION:
        _raise_binding("predecessor_lineage")
    if preparation.target_operation != _OPERATION:
        _raise_binding("predecessor_lineage")
    if preparation.state != _PREPARATION_STATE:
        _raise_binding("predecessor_lineage")


def _reconstruct_model(
    model_type: type[object],
    instance: object,
    classification: Classification,
) -> None:
    if type(instance) is not model_type:
        _raise_binding(classification)
    try:
        values = {
            field.name: getattr(instance, field.name)
            for field in fields(model_type)  # type: ignore[arg-type]
        }
        model_type(**values)  # type: ignore[operator]
    except Exception:
        _raise_binding(classification)


def _derive_decision_path(anchor: Path, outcome_digest: str) -> Path:
    return anchor.parent / (
        f"{_DECISION_FILENAME_PREFIX}{outcome_digest}{_FILENAME_SUFFIX}"
    )


def _derive_preparation_path(anchor: Path, decision_digest: str) -> Path:
    return anchor.parent / (
        f"{_PREPARATION_FILENAME_PREFIX}{decision_digest}{_FILENAME_SUFFIX}"
    )


def _derive_binding_path(anchor: Path, preparation_digest: str) -> Path:
    return anchor.parent / (
        f"{_BINDING_FILENAME_PREFIX}{preparation_digest}{_FILENAME_SUFFIX}"
    )


def _derive_intent_path(anchor: Path, intent_digest: str) -> Path:
    return anchor.parent / (
        f"{_INTENT_FILENAME_PREFIX}{intent_digest}{_FILENAME_SUFFIX}"
    )


def _binding_target_present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except Exception:
        _raise_load("target")


def _intent_target_present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except Exception:
        _raise_binding("target")


def _resolve_existing_binding(
    path: Path,
    constructed: ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
) -> ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding:
    try:
        loaded = load_external_publication_recovery_resume_decision_preparation_intent_binding(  # noqa: E501
            path
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError:
        raise
    except Exception:
        _raise_load("load")
    if (
        type(loaded)
        is not ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding
    ):
        _raise_load("load")
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
        loaded,
        "load",
    )
    if loaded != constructed:
        _raise_conflict()
    return loaded


def _validate_binding(binding: object) -> None:
    if (
        type(binding)
        is not ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding
    ):
        _raise_binding("configuration")
    try:
        _check_binding(binding)
    except ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError:
        raise
    except Exception:
        _raise_binding("configuration")


def _check_binding(
    binding: ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
) -> None:
    schema_version = binding.schema_version
    preparation_digest = binding.decision_preparation_sha256
    decision_digest = binding.recovery_resume_decision_sha256
    approval_digest = binding.publication_approval_sha256
    plan_digest = binding.publication_plan_sha256
    intent_digest = binding.operation_intent_sha256
    source_operation = binding.source_operation
    previous_recovery_kind = binding.previous_recovery_kind
    recovery_kind = binding.recovery_kind
    result_kind = binding.result_kind
    result_digest = binding.result_sha256
    operation = binding.operation
    state = binding.state

    if type(schema_version) is not str or schema_version != _BINDING_SCHEMA_VERSION:
        _raise_binding("configuration")
    for digest in (
        preparation_digest,
        decision_digest,
        approval_digest,
        plan_digest,
        intent_digest,
    ):
        if not _is_sha256(digest):
            _raise_binding("configuration")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_binding("configuration")
    if (
        type(previous_recovery_kind) is not str
        or previous_recovery_kind not in _RECOVERY_KINDS
    ):
        _raise_binding("configuration")
    if (
        previous_recovery_kind == _MISMATCH_RECOVERY_KIND
        and source_operation != _OPERATION
    ):
        _raise_binding("configuration")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_binding("configuration")
    if type(result_kind) is not str or result_kind not in _RESULT_KINDS:
        _raise_binding("configuration")
    if type(operation) is not str or operation != _OPERATION:
        _raise_binding("configuration")
    if type(state) is not str or state != _STATE:
        _raise_binding("configuration")

    if result_kind == _NONE_RESULT_KIND:
        if result_digest is not None:
            _raise_binding("configuration")
        if recovery_kind != _ALREADY_ACQUIRED_RECOVERY_KIND:
            _raise_binding("configuration")
        return
    if not _is_sha256(result_digest):
        _raise_binding("configuration")
    if recovery_kind != _MISMATCH_RECOVERY_KIND:
        _raise_binding("configuration")


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
    except ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError:  # noqa: E501
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
    except ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingLoadError:
        raise
    except Exception:
        _raise_load("target")


def _write_binding(path: Path, contents: bytes) -> None:
    try:
        handle = path.open("xb")
    except FileExistsError:
        _verify_existing_binding(path, contents)
        return
    except OSError as error:
        if error.errno == errno.EEXIST:
            _verify_existing_binding(path, contents)
            return
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    _persist_new_binding(handle, path.parent, contents)


def _persist_new_binding(handle: object, directory: Path, contents: bytes) -> None:
    try:
        with _binding_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short recovery resume preparation intent binding write")
            active_handle.flush()  # type: ignore[attr-defined]
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_binding_directory(directory)
    except Exception:
        _raise_persistence("ambiguous")


def _verify_existing_binding(path: Path, contents: bytes) -> None:
    try:
        if (
            path.is_symlink()
            or not path.exists()
            or path.is_dir()
            or not path.is_file()
        ):
            _raise_persistence("target")
        with path.open("rb") as handle:
            existing = handle.read(_MAX_BINDING_BYTES + 1)
        if type(existing) is not bytes:
            _raise_persistence("target")
    except ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError:  # noqa: E501
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
        with _binding_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")
    try:
        _fsync_binding_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


@contextmanager
def _binding_handle_scope(handle: object) -> Iterator[object]:
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
            raise OSError(
                "recovery resume preparation intent binding handle cannot close"
            )
        close()


def _fsync_binding_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_binding(
    value: object,
) -> ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding:
    if type(value) is not dict:
        _raise_load("parse")
    if frozenset(value) != _BINDING_KEYS:
        _raise_load("keys")
    try:
        return ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding(
            schema_version=value["schema_version"],
            decision_preparation_sha256=value["decision_preparation_sha256"],
            recovery_resume_decision_sha256=value["recovery_resume_decision_sha256"],
            publication_approval_sha256=value["publication_approval_sha256"],
            publication_plan_sha256=value["publication_plan_sha256"],
            operation_intent_sha256=value["operation_intent_sha256"],
            source_operation=value["source_operation"],
            previous_recovery_kind=value["previous_recovery_kind"],
            recovery_kind=value["recovery_kind"],
            result_kind=value["result_kind"],
            result_sha256=value["result_sha256"],
            operation=value["operation"],
            state=value["state"],
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError:
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
    del value
    raise _NonStandardJSONConstantError


def _raise_binding(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError(  # noqa: E501
        classification
    ) from None


def _raise_persistence(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError(  # noqa: E501
        classification
    ) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingConflictError(  # noqa: E501
        "conflict"
    ) from None


def _raise_load(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingLoadError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding",
    "ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError",
    "ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingConflictError",
    "ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError",
    "ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingFailureDetail",
    "ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingLoadError",
    "ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError",
    "external_publication_recovery_resume_decision_preparation_intent_binding_canonical_bytes",
    "external_publication_recovery_resume_decision_preparation_intent_binding_digest",
    "load_external_publication_recovery_resume_decision_preparation_intent_binding",
    "materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent",
    "persist_external_publication_recovery_resume_decision_preparation_intent_binding",
    "serialize_external_publication_recovery_resume_decision_preparation_intent_binding_canonical",
]
