"""Focused and integration regressions for the Phase 298 recovery resume outcome."""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_resume_outcome as outcome_module  # noqa: E501
from ai_office.engine import (
    ExternalPublicationApproval,
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationFreshOperationRequest,
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartAcquisition,
    ExternalPublicationOperationStartError,
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeIntentBindingError,
    ExternalPublicationRecoveryResumeOutcome,
    ExternalPublicationRecoveryResumeOutcomeCompatibilityError,
    ExternalPublicationRecoveryResumeOutcomeConflictError,
    ExternalPublicationRecoveryResumeOutcomeError,
    ExternalPublicationRecoveryResumeOutcomeFailureDetail,
    ExternalPublicationRecoveryResumeOutcomeLoadError,
    ExternalPublicationRecoveryResumeOutcomePersistenceError,
    ExternalPublicationRecoveryResumeStartAuthorization,
    ExternalPublicationRecoveryResumeStartAuthorizationError,
    ExternalPublicationRecoveryResumeStartHandoffError,
    ExternalPublicationResumeOperationRequest,
    external_publication_approval_digest,
    external_publication_execution_reconciliation_digest,
    external_publication_operation_intent_digest,
    external_publication_operation_start_canonical_bytes,
    external_publication_operation_start_digest,
    external_publication_recovery_resume_intent_binding_digest,
    external_publication_recovery_resume_outcome_canonical_bytes,
    external_publication_recovery_resume_outcome_digest,
    external_publication_recovery_resume_start_authorization_digest,
    load_external_publication_operation_intent,
    load_external_publication_operation_start,
    load_external_publication_recovery_resume_intent_binding,
    load_external_publication_recovery_resume_outcome,
    load_external_publication_recovery_resume_start_authorization,
    persist_external_publication_recovery_resume_outcome,
    run_and_persist_external_publication_recovery_resume_outcome,
    run_external_publication_recovery_resume_start_handoff,
    serialize_external_publication_recovery_resume_outcome_canonical,
)

_BINDING_SCHEMA = "external-publication-recovery-resume-intent-binding.v1"
_AUTHORIZATION_SCHEMA = "external-publication-recovery-resume-start-authorization.v1"
_INTENT_SCHEMA = "external-publication-operation-intent.v1"
_START_SCHEMA = "external-publication-operation-start.v1"
_OUTCOME_SCHEMA = "external-publication-recovery-resume-outcome.v1"
_RECONCILIATION_SCHEMA = "external-publication-execution-reconciliation.v1"
_INVALID_MESSAGE = "external publication recovery resume outcome is invalid"
_PERSIST_MESSAGE = "external publication recovery resume outcome persistence failed"
_LOAD_MESSAGE = "external publication recovery resume outcome could not be loaded"
_AUTHORIZATION_FILENAME_PREFIX = (
    "external-publication-recovery-resume-start-authorization-"
)
_START_FILENAME_PREFIX = "external-publication-recovery-resume-start-"
_OUTCOME_FILENAME_PREFIX = "external-publication-recovery-resume-outcome-"
_FILENAME_SUFFIX = ".json"
_SOURCE = Path(outcome_module.__file__).read_text(encoding="utf-8")
_EXPECTED_PARAMETERS = (
    "resume_intent_binding_path",
    "resume_intent_path",
    "request",
    "binding_loader",
    "binding_digest_function",
    "authorization_loader",
    "authorization_digest_function",
    "intent_loader",
    "intent_digest_function",
    "approval_digest_function",
    "expected_start_digest_function",
    "start_loader",
    "start_digest_function",
    "reconciliation_digest_function",
    "phase297_function",
)
_EXPECTED_IMPORT_MODULES = frozenset(
    {
        "external_publication",
        "external_publication_execution",
        "external_publication_execution_evidence",
        "external_publication_execution_orchestration",
        "external_publication_execution_reconciliation",
        "external_publication_execution_reconciliation_evidence",
        "external_publication_execution_reconciliation_orchestration",
        "external_publication_execution_reconciliation_resume",
        "external_publication_operation",
        "external_publication_operation_intent",
        "external_publication_operation_start",
        "external_publication_operation_start_handoff",
        "external_publication_recovery_resume_intent_binding",
        "external_publication_recovery_resume_start_authorization",
        "external_publication_recovery_resume_start_handoff",
    }
)
_CANONICAL_KEYS = (
    "operation",
    "operation_intent_sha256",
    "operation_start_sha256",
    "publication_approval_sha256",
    "publication_plan_sha256",
    "recovery_kind",
    "result_kind",
    "result_sha256",
    "resume_intent_binding_sha256",
    "resume_start_authorization_sha256",
    "schema_version",
    "source_operation",
    "state",
)
_FIELD_NAMES = (
    "schema_version",
    "resume_start_authorization_sha256",
    "resume_intent_binding_sha256",
    "operation_intent_sha256",
    "operation_start_sha256",
    "publication_approval_sha256",
    "publication_plan_sha256",
    "source_operation",
    "recovery_kind",
    "operation",
    "state",
    "result_kind",
    "result_sha256",
)

_UNSET: object = object()


class _StringChild(str):
    pass


class _RequestLookalike:
    """Attribute-compatible substitute that must never be accepted."""

    def __init__(self, source: ExternalPublicationResumeOperationRequest) -> None:
        self.ledger_directory = source.ledger_directory
        self.approval = source.approval
        self.execution_evidence_path = source.execution_evidence_path
        self.execution_reconciliation_evidence_path = (
            source.execution_reconciliation_evidence_path
        )


def _field_names(cls: type[object]) -> set[str]:
    return {field.name for field in dataclasses.fields(cls)}  # type: ignore[arg-type]


def _forged_instance(
    cls: type[object], source: object = None, **overrides: object
) -> object:
    """Allocate without running validation; every override must be a real field."""
    if not isinstance(cls, type):
        source = cls
        cls = type(cls)
    assert source is not None, "source instance is required"
    names: set[str] = set()
    value = object.__new__(cls)  # type: ignore[call-overload]
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        names.add(field.name)
        object.__setattr__(value, field.name, getattr(source, field.name))
    for name, replacement in overrides.items():
        assert name in names, name
        object.__setattr__(value, name, replacement)
    return value


def _assert_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationRecoveryResumeOutcomeCompatibilityError
    assert isinstance(error, ExternalPublicationRecoveryResumeOutcomeError)
    assert isinstance(error, ValueError)
    assert str(error) == _INVALID_MESSAGE
    assert type(error.detail) is ExternalPublicationRecoveryResumeOutcomeFailureDetail
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationRecoveryResumeOutcomePersistenceError
    assert str(error) == _PERSIST_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_load_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationRecoveryResumeOutcomeLoadError
    assert str(error) == _LOAD_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_conflict_error(error: ValueError) -> None:
    assert type(error) is ExternalPublicationRecoveryResumeOutcomeConflictError
    assert str(error) == _PERSIST_MESSAGE
    assert error.detail.classification == "conflict"
    assert error.__cause__ is None


class _CallRecorder:
    """Record every call and optionally delegate, fault, or return a fixed value."""

    def __init__(
        self,
        delegate: object = None,
        fault: object = None,
        result: object = None,
    ) -> None:
        self.delegate = delegate
        self.fault = fault
        self.result = result
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.results: list[object] = []

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        if isinstance(self.fault, BaseException):
            raise self.fault
        if self.delegate is not None:
            value = self.delegate(*args, **kwargs)  # type: ignore[operator]
            self.results.append(value)
            return value
        self.results.append(self.result)
        return self.result

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def first_positional(self) -> object:
        return self.calls[0][0][0]

    @property
    def first_result(self) -> object:
        return self.results[0]

    @property
    def first_kwargs(self) -> dict[str, object]:
        return dict(self.calls[0][1])


# --- builders ------------------------------------------------------------


def _approval(
    *,
    approved: object = True,
    publication_plan_sha256: object = "d" * 64,
    approved_by: object = "human-reviewer-298",
    approval_id: object = "approval-298",
) -> ExternalPublicationApproval:
    return ExternalPublicationApproval(
        approved=approved,  # type: ignore[arg-type]
        publication_plan_sha256=publication_plan_sha256,  # type: ignore[arg-type]
        approved_by=approved_by,  # type: ignore[arg-type]
        approval_id=approval_id,  # type: ignore[arg-type]
    )


def _binding(**overrides: object) -> ExternalPublicationRecoveryResumeIntentBinding:
    values: dict[str, object] = {
        "schema_version": _BINDING_SCHEMA,
        "resume_preparation_sha256": "a" * 64,
        "recovery_decision_sha256": "b" * 64,
        "publication_approval_sha256": "c" * 64,
        "publication_plan_sha256": "d" * 64,
        "operation_intent_sha256": "e" * 64,
        "source_operation": "fresh",
        "recovery_kind": "already_acquired",
        "operation": "resume",
        "state": "authorized",
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeIntentBinding(**values)  # type: ignore[arg-type]


def _authorization(
    **overrides: object,
) -> ExternalPublicationRecoveryResumeStartAuthorization:
    values: dict[str, object] = {
        "schema_version": _AUTHORIZATION_SCHEMA,
        "resume_intent_binding_sha256": "1" * 64,
        "resume_preparation_sha256": "a" * 64,
        "recovery_decision_sha256": "b" * 64,
        "operation_intent_sha256": "e" * 64,
        "expected_operation_start_sha256": "6" * 64,
        "publication_approval_sha256": "c" * 64,
        "publication_plan_sha256": "d" * 64,
        "source_operation": "fresh",
        "recovery_kind": "already_acquired",
        "operation": "resume",
        "state": "authorized",
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeStartAuthorization(**values)  # type: ignore[arg-type]


def _intent(**overrides: object) -> ExternalPublicationOperationIntent:
    values: dict[str, object] = {
        "schema_version": _INTENT_SCHEMA,
        "publication_approval_sha256": "c" * 64,
        "publication_plan_sha256": "d" * 64,
        "operation": "resume",
    }
    values.update(overrides)
    return ExternalPublicationOperationIntent(**values)  # type: ignore[arg-type]


def _start(**overrides: object) -> ExternalPublicationOperationStart:
    values: dict[str, object] = {
        "schema_version": _START_SCHEMA,
        "operation_intent_sha256": "e" * 64,
        "publication_approval_sha256": "c" * 64,
        "publication_plan_sha256": "d" * 64,
        "operation": "resume",
        "state": "started",
    }
    values.update(overrides)
    return ExternalPublicationOperationStart(**values)  # type: ignore[arg-type]


def _reconciliation(**overrides: object) -> ExternalPublicationExecutionReconciliation:
    values: dict[str, object] = {
        "schema_version": _RECONCILIATION_SCHEMA,
        "claim_sha256": "a" * 64,
        "execution_evidence_sha256": "b" * 64,
        "status": "matched",
        "mismatched_fields": (),
    }
    values.update(overrides)
    return ExternalPublicationExecutionReconciliation(**values)  # type: ignore[arg-type]


def _outcome(**overrides: object) -> ExternalPublicationRecoveryResumeOutcome:
    values: dict[str, object] = {
        "schema_version": _OUTCOME_SCHEMA,
        "resume_start_authorization_sha256": "1" * 64,
        "resume_intent_binding_sha256": "2" * 64,
        "operation_intent_sha256": "3" * 64,
        "operation_start_sha256": "4" * 64,
        "publication_approval_sha256": "c" * 64,
        "publication_plan_sha256": "d" * 64,
        "source_operation": "fresh",
        "recovery_kind": "already_acquired",
        "operation": "resume",
        "state": "recovery_required",
        "result_kind": "none",
        "result_sha256": None,
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeOutcome(**values)  # type: ignore[arg-type]


def _resume_request(
    root: Path,
    *,
    approval: object = _UNSET,
    ledger_directory: object = _UNSET,
    execution_evidence_path: object = _UNSET,
    execution_reconciliation_evidence_path: object = _UNSET,
) -> ExternalPublicationResumeOperationRequest:
    return ExternalPublicationResumeOperationRequest(
        ledger_directory=(
            root / "ledger" if ledger_directory is _UNSET else ledger_directory
        ),  # type: ignore[arg-type]
        approval=_approval() if approval is _UNSET else approval,  # type: ignore[arg-type]
        execution_evidence_path=(
            root / "execution-evidence.json"
            if execution_evidence_path is _UNSET
            else execution_evidence_path
        ),  # type: ignore[arg-type]
        execution_reconciliation_evidence_path=(
            root / "execution-reconciliation-evidence.json"
            if execution_reconciliation_evidence_path is _UNSET
            else execution_reconciliation_evidence_path
        ),  # type: ignore[arg-type]
    )


class _Lineage:
    """One exactly self-consistent Phase295/296/289/290/runtime lineage."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.binding_path = root / "binding.json"
        self.intent_path = root / "intent.json"
        self.approval = _approval()
        self.approval_digest = external_publication_approval_digest(self.approval)
        self.plan_digest = self.approval.publication_plan_sha256
        self.intent = _intent(
            publication_approval_sha256=self.approval_digest,
            publication_plan_sha256=self.plan_digest,
        )
        self.intent_digest = external_publication_operation_intent_digest(self.intent)
        self.binding = _binding(
            publication_approval_sha256=self.approval_digest,
            publication_plan_sha256=self.plan_digest,
            operation_intent_sha256=self.intent_digest,
        )
        self.binding_digest = (
            external_publication_recovery_resume_intent_binding_digest(self.binding)
        )
        self.expected_start = _start(
            operation_intent_sha256=self.intent_digest,
            publication_approval_sha256=self.approval_digest,
            publication_plan_sha256=self.plan_digest,
        )
        self.expected_start_digest = external_publication_operation_start_digest(
            self.expected_start
        )
        self.authorization = _authorization(
            resume_intent_binding_sha256=self.binding_digest,
            operation_intent_sha256=self.intent_digest,
            expected_operation_start_sha256=self.expected_start_digest,
            publication_approval_sha256=self.approval_digest,
            publication_plan_sha256=self.plan_digest,
        )
        self.authorization_digest = (
            external_publication_recovery_resume_start_authorization_digest(
                self.authorization
            )
        )
        self.request = _resume_request(root, approval=self.approval)

    @property
    def authorization_path(self) -> Path:
        return self.binding_path.parent / (
            f"{_AUTHORIZATION_FILENAME_PREFIX}{self.binding_digest}{_FILENAME_SUFFIX}"
        )

    @property
    def start_path(self) -> Path:
        return self.binding_path.parent / (
            f"{_START_FILENAME_PREFIX}{self.authorization_digest}{_FILENAME_SUFFIX}"
        )

    @property
    def outcome_path(self) -> Path:
        return self.binding_path.parent / (
            f"{_OUTCOME_FILENAME_PREFIX}{self.authorization_digest}{_FILENAME_SUFFIX}"
        )

    def build_outcome(
        self,
        *,
        state: str = "recovery_required",
        result_kind: str = "none",
        result_sha256: object = None,
    ) -> ExternalPublicationRecoveryResumeOutcome:
        return _outcome(
            resume_start_authorization_sha256=self.authorization_digest,
            resume_intent_binding_sha256=self.binding_digest,
            operation_intent_sha256=self.intent_digest,
            operation_start_sha256=self.expected_start_digest,
            publication_approval_sha256=self.approval_digest,
            publication_plan_sha256=self.plan_digest,
            state=state,
            result_kind=result_kind,
            result_sha256=result_sha256,
        )


class _Harness:
    """Fully injected boundary run with exact-once recorders."""

    def __init__(
        self,
        root: Path,
        *,
        binding: object = _UNSET,
        authorization: object = _UNSET,
        intent: object = _UNSET,
        request: object = _UNSET,
        phase297_result: object = _UNSET,
        lineage: _Lineage | None = None,
        write_start_marker: bool = True,
    ) -> None:
        self.lineage = lineage if lineage is not None else _Lineage(root)
        self.root = root
        self.binding = self.lineage.binding if binding is _UNSET else binding
        self.authorization = (
            self.lineage.authorization if authorization is _UNSET else authorization
        )
        self.intent = self.lineage.intent if intent is _UNSET else intent
        self.request = self.lineage.request if request is _UNSET else request
        self.phase297_result = (
            ExternalPublicationOperationStartAcquisition(
                status="already_acquired",
                start=self.lineage.expected_start,  # type: ignore[arg-type]
            )
            if phase297_result is _UNSET
            else phase297_result
        )
        if write_start_marker:
            start_bytes = external_publication_operation_start_canonical_bytes(  # noqa: E501
                self.lineage.expected_start
            )
            self.lineage.start_path.write_bytes(start_bytes)
        self.binding_loader = _CallRecorder(result=self.binding)
        self.binding_digest = _CallRecorder(result=self.lineage.binding_digest)
        self.authorization_loader = _CallRecorder(result=self.authorization)
        self.authorization_digest = _CallRecorder(
            result=self.lineage.authorization_digest
        )
        self.intent_loader = _CallRecorder(result=self.intent)
        self.intent_digest = _CallRecorder(result=self.lineage.intent_digest)
        self.approval_digest = _CallRecorder(result=self.lineage.approval_digest)
        self.expected_start_digest = _CallRecorder(
            delegate=external_publication_operation_start_digest
        )
        self.start_loader = _CallRecorder(
            delegate=load_external_publication_operation_start
        )
        self.start_digest = _CallRecorder(
            delegate=external_publication_operation_start_digest
        )
        self.reconciliation_digest = _CallRecorder(
            delegate=external_publication_execution_reconciliation_digest
        )
        self.phase297 = _CallRecorder(result=self.phase297_result)

    def kwargs(self) -> dict[str, object]:
        return {
            "resume_intent_binding_path": self.lineage.binding_path,
            "resume_intent_path": self.lineage.intent_path,
            "request": self.request,
            "binding_loader": self.binding_loader,
            "binding_digest_function": self.binding_digest,
            "authorization_loader": self.authorization_loader,
            "authorization_digest_function": self.authorization_digest,
            "intent_loader": self.intent_loader,
            "intent_digest_function": self.intent_digest,
            "approval_digest_function": self.approval_digest,
            "expected_start_digest_function": self.expected_start_digest,
            "start_loader": self.start_loader,
            "start_digest_function": self.start_digest,
            "reconciliation_digest_function": self.reconciliation_digest,
            "phase297_function": self.phase297,
        }

    def run(
        self,
        *,
        phase297_result: object = _UNSET,
        phase297_fault: object = _UNSET,
        **overrides: object,
    ) -> object:
        if phase297_result is not _UNSET:
            self.phase297.result = phase297_result
        if phase297_fault is not _UNSET:
            self.phase297.fault = phase297_fault
        kwargs = self.kwargs()
        kwargs.update(overrides)
        return (
            outcome_module.run_and_persist_external_publication_recovery_resume_outcome(
                **kwargs  # type: ignore[arg-type]
            )
        )

    def assert_phase297_zero_call(self) -> None:
        assert self.phase297.call_count == 0

    def assert_preflight_zero_call(self) -> None:
        assert self.phase297.call_count == 0
        assert self.binding_loader.call_count == 0
        assert self.binding_digest.call_count == 0
        assert self.authorization_loader.call_count == 0
        assert self.authorization_digest.call_count == 0
        assert self.intent_loader.call_count == 0
        assert self.intent_digest.call_count == 0
        assert self.start_loader.call_count == 0
        assert self.reconciliation_digest.call_count == 0
        assert not self.lineage.outcome_path.exists()


# --- model / canonical ---------------------------------------------------


def test_public_exports_and_error_family() -> None:
    assert outcome_module.__all__ == [
        "ExternalPublicationRecoveryResumeOutcome",
        "ExternalPublicationRecoveryResumeOutcomeCompatibilityError",
        "ExternalPublicationRecoveryResumeOutcomeConflictError",
        "ExternalPublicationRecoveryResumeOutcomeError",
        "ExternalPublicationRecoveryResumeOutcomeFailureDetail",
        "ExternalPublicationRecoveryResumeOutcomeLoadError",
        "ExternalPublicationRecoveryResumeOutcomePersistenceError",
        "external_publication_recovery_resume_outcome_canonical_bytes",
        "external_publication_recovery_resume_outcome_digest",
        "load_external_publication_recovery_resume_outcome",
        "persist_external_publication_recovery_resume_outcome",
        "run_and_persist_external_publication_recovery_resume_outcome",
        "serialize_external_publication_recovery_resume_outcome_canonical",
    ]


def test_error_family_hierarchy_and_messages() -> None:
    error = ExternalPublicationRecoveryResumeOutcomeError()
    assert isinstance(error, ValueError)
    assert str(error) == _INVALID_MESSAGE
    assert error.detail.classification == "dependency_error"
    compatibility = ExternalPublicationRecoveryResumeOutcomeCompatibilityError(
        "result_contract"
    )
    assert isinstance(compatibility, ExternalPublicationRecoveryResumeOutcomeError)
    assert compatibility.__cause__ is None
    persistence = ExternalPublicationRecoveryResumeOutcomePersistenceError("target")
    assert str(persistence) == _PERSIST_MESSAGE
    conflict = ExternalPublicationRecoveryResumeOutcomeConflictError()
    assert str(conflict) == _PERSIST_MESSAGE
    load = ExternalPublicationRecoveryResumeOutcomeLoadError("parse")
    assert str(load) == _LOAD_MESSAGE
    assert _field_names(ExternalPublicationRecoveryResumeOutcomeFailureDetail) == {
        "classification"
    }


def test_model_field_order_and_frozen() -> None:
    assert (
        tuple(
            field.name
            for field in dataclasses.fields(ExternalPublicationRecoveryResumeOutcome)
        )
        == _FIELD_NAMES
    )
    instance = _outcome()
    with pytest.raises(dataclasses.FrozenInstanceError):
        object.__setattr__  # noqa: B018
        instance.state = "completed"  # type: ignore[misc]


def test_model_accepts_exact_valid_combinations() -> None:
    digest = "a" * 64
    assert (
        _outcome(
            state="completed", result_kind="reconciliation", result_sha256=digest
        ).state
        == "completed"
    )
    assert (
        _outcome(
            state="recovery_required",
            result_kind="reconciliation",
            result_sha256=digest,
        ).result_kind
        == "reconciliation"
    )
    assert (
        _outcome(
            state="recovery_required", result_kind="none", result_sha256=None
        ).result_sha256
        is None
    )


@pytest.mark.parametrize(
    ("state", "result_kind", "result_sha256"),
    [
        ("completed", "none", None),
        ("completed", "none", "a" * 64),
        ("recovery_required", "none", "a" * 64),
        ("recovery_required", "reconciliation", None),
        ("completed", "reconciliation", None),
        ("completed", "other", "a" * 64),
        ("recovery_required", "other", "a" * 64),
        ("pending", "none", None),
    ],
)
def test_model_rejects_invalid_combinations(
    state: str, result_kind: str, result_sha256: object
) -> None:
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError):
        _outcome(state=state, result_kind=result_kind, result_sha256=result_sha256)


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("schema_version", "external-publication-recovery-resume-outcome.v2"),
        ("resume_start_authorization_sha256", "A" * 64),
        ("resume_start_authorization_sha256", ""),
        ("resume_intent_binding_sha256", _StringChild("2" * 64)),
        ("operation_intent_sha256", 3),
        ("operation_start_sha256", None),
        ("publication_approval_sha256", "c" * 63),
        ("publication_plan_sha256", "d" * 65),
        ("source_operation", "replay"),
        ("recovery_kind", "unknown"),
        ("operation", "fresh"),
        ("state", "authorized"),
        ("result_kind", "execution_result"),
    ],
)
def test_model_rejects_non_exact_values(field_name: str, bad_value: object) -> None:
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        _outcome(**{field_name: bad_value})
    _assert_error(excinfo.value, "configuration")


def test_model_rejects_reconciliation_mismatch_with_fresh_source() -> None:
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError):
        _outcome(recovery_kind="reconciliation_mismatch", source_operation="fresh")


def test_canonical_json_has_exactly_thirteen_keys() -> None:
    import json

    payload = json.loads(
        serialize_external_publication_recovery_resume_outcome_canonical(_outcome())
    )
    assert tuple(sorted(payload)) == _CANONICAL_KEYS
    assert len(payload) == 13


def test_canonical_bytes_and_deterministic_digest() -> None:
    outcome = _outcome()
    first = external_publication_recovery_resume_outcome_canonical_bytes(outcome)
    second = external_publication_recovery_resume_outcome_canonical_bytes(_outcome())
    assert first == second
    assert type(first) is bytes
    assert external_publication_recovery_resume_outcome_digest(
        outcome
    ) == external_publication_recovery_resume_outcome_digest(_outcome())
    assert b" " not in first
    assert b"\n" not in first


def test_canonical_serializer_rejects_forged_operand() -> None:
    forged = _forged_instance(_outcome(), _outcome(), state="pending")
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError):
        serialize_external_publication_recovery_resume_outcome_canonical(
            forged  # type: ignore[arg-type]
        )


# --- load ----------------------------------------------------------------


def test_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "outcome.json"
    outcome = _outcome()
    persist_external_publication_recovery_resume_outcome(path, outcome)
    loaded = load_external_publication_recovery_resume_outcome(path)
    assert loaded == outcome
    assert type(loaded) is ExternalPublicationRecoveryResumeOutcome


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not json",
        b"{}",
        b'{"schema_version": null}',
        b"[1, 2, 3]",
    ],
)
def test_load_rejects_malformed(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "outcome.json"
    path.write_bytes(payload)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeLoadError):
        load_external_publication_recovery_resume_outcome(path)


def test_load_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "outcome.json"
    canonical = external_publication_recovery_resume_outcome_canonical_bytes(
        _outcome()
    ).decode("utf-8")
    duplicated = canonical[:-1] + ',"state":"completed"}'
    path.write_text(duplicated, encoding="utf-8")
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeLoadError) as excinfo:
        load_external_publication_recovery_resume_outcome(path)
    _assert_load_error(excinfo.value, "parse")


def test_load_rejects_nonstandard_constant(tmp_path: Path) -> None:
    path = tmp_path / "outcome.json"
    path.write_text('{"a": NaN}', encoding="utf-8")
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeLoadError) as excinfo:
        load_external_publication_recovery_resume_outcome(path)
    _assert_load_error(excinfo.value, "parse")


def test_load_rejects_extra_key(tmp_path: Path) -> None:
    path = tmp_path / "outcome.json"
    canonical = external_publication_recovery_resume_outcome_canonical_bytes(
        _outcome()
    ).decode("utf-8")
    path.write_text(canonical[:-1] + ',"extra":1}', encoding="utf-8")
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeLoadError) as excinfo:
        load_external_publication_recovery_resume_outcome(path)
    _assert_load_error(excinfo.value, "keys")


def test_load_rejects_missing_key(tmp_path: Path) -> None:
    path = tmp_path / "outcome.json"
    trimmed = _outcome()
    forged = _forged_instance(trimmed, trimmed)
    object.__delattr__(forged, "state")
    canonical = serialize_external_publication_recovery_resume_outcome_canonical(
        trimmed
    )
    import json

    payload = json.loads(canonical)
    payload.pop("state")
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeLoadError) as excinfo:
        load_external_publication_recovery_resume_outcome(path)
    _assert_load_error(excinfo.value, "keys")


def test_load_rejects_noncanonical_bytes(tmp_path: Path) -> None:
    path = tmp_path / "outcome.json"
    import json

    canonical = serialize_external_publication_recovery_resume_outcome_canonical(
        _outcome()
    )
    pretty = json.dumps(json.loads(canonical), indent=2, sort_keys=True)
    path.write_text(pretty + "\n", encoding="utf-8")
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeLoadError) as excinfo:
        load_external_publication_recovery_resume_outcome(path)
    _assert_load_error(excinfo.value, "noncanonical")


def test_load_rejects_absent_target(tmp_path: Path) -> None:
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeLoadError) as excinfo:
        load_external_publication_recovery_resume_outcome(tmp_path / "missing.json")
    _assert_load_error(excinfo.value, "target")


def test_load_rejects_non_path(tmp_path: Path) -> None:
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeLoadError) as excinfo:
        load_external_publication_recovery_resume_outcome("outcome.json")  # type: ignore[arg-type]
    _assert_load_error(excinfo.value, "path_type")


# --- persistence ---------------------------------------------------------


def test_persist_creates_then_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "outcome.json"
    outcome = _outcome()
    persist_external_publication_recovery_resume_outcome(path, outcome)
    before = path.read_bytes()
    persist_external_publication_recovery_resume_outcome(path, outcome)
    assert path.read_bytes() == before


def test_persist_conflict_leaves_bytes_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "outcome.json"
    other = _outcome(
        state="completed", result_kind="reconciliation", result_sha256="a" * 64
    )  # noqa: E501
    persist_external_publication_recovery_resume_outcome(path, other)
    before = path.read_bytes()
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeConflictError
    ) as excinfo:  # noqa: E501
        persist_external_publication_recovery_resume_outcome(path, _outcome())
    _assert_conflict_error(excinfo.value)
    assert path.read_bytes() == before


def test_persist_rejects_missing_parent(tmp_path: Path) -> None:
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomePersistenceError
    ) as excinfo:  # noqa: E501
        persist_external_publication_recovery_resume_outcome(
            tmp_path / "missing" / "outcome.json", _outcome()
        )
    _assert_persistence_error(excinfo.value, "parent")


def test_persist_rejects_non_path(tmp_path: Path) -> None:
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomePersistenceError
    ) as excinfo:  # noqa: E501
        persist_external_publication_recovery_resume_outcome(
            "outcome.json",
            _outcome(),  # type: ignore[arg-type]
        )
    _assert_persistence_error(excinfo.value, "path_type")


def test_persist_rejects_directory_target(tmp_path: Path) -> None:
    target = tmp_path / "outcome.json"
    target.mkdir()
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomePersistenceError
    ) as excinfo:  # noqa: E501
        persist_external_publication_recovery_resume_outcome(target, _outcome())
    _assert_persistence_error(excinfo.value, "target")


def test_persist_rejects_symlink_target(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_bytes(b"{}")
    link = tmp_path / "outcome.json"
    link.symlink_to(real)
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomePersistenceError
    ) as excinfo:  # noqa: E501
        persist_external_publication_recovery_resume_outcome(link, _outcome())
    _assert_persistence_error(excinfo.value, "target")
    assert real.read_bytes() == b"{}"


def test_persist_rejects_forged_outcome(tmp_path: Path) -> None:
    forged = _forged_instance(_outcome(), _outcome(), state="pending")
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError):
        persist_external_publication_recovery_resume_outcome(
            tmp_path / "outcome.json",
            forged,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "stage",
    ["write", "short_write", "flush", "file_fsync", "close", "dir_fsync"],
)
def test_persist_ambiguity_retains_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """An uncertain durability step retains the artifact with no retry."""
    path = tmp_path / "outcome.json"
    real_open = Path.open
    real_fsync = outcome_module.os.fsync
    real_dir_fsync = outcome_module._fsync_outcome_directory

    class _FaultyHandle:
        def __init__(self, handle: object) -> None:
            self._handle = handle

        def __enter__(self) -> _FaultyHandle:
            self._handle.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *exc: object) -> bool:
            if stage == "close":
                # The real handle's __exit__ would close it; break close here so
                # the durability step itself is the one that fails.
                raise OSError("close fault")
            return self._handle.__exit__(*exc)  # type: ignore[attr-defined]

        def write(self, data: bytes) -> int:
            if stage == "write":
                raise OSError("write fault")
            if stage == "short_write":
                return len(data) - 1
            return self._handle.write(data)  # type: ignore[attr-defined]

        def flush(self) -> None:
            if stage == "flush":
                raise OSError("flush fault")
            self._handle.flush()  # type: ignore[attr-defined]

        def fileno(self) -> int:
            return self._handle.fileno()  # type: ignore[attr-defined]

        def close(self) -> None:
            if stage == "close":
                raise OSError("close fault")
            self._handle.close()  # type: ignore[attr-defined]

    def _fake_open(self: Path, *args: object, **kwargs: object) -> object:
        handle = real_open(self, *args, **kwargs)  # type: ignore[arg-type]
        if "x" in str(args[0]) if args else False:
            return _FaultyHandle(handle)
        return handle

    if stage in {"write", "short_write", "flush", "file_fsync", "close"}:

        def _open(
            self: Path, mode: str = "r", *args: object, **kwargs: object
        ) -> object:  # noqa: E501
            handle = real_open(self, mode, *args, **kwargs)  # type: ignore[arg-type]
            if mode == "xb":
                return _FaultyHandle(handle)
            return handle

        monkeypatch.setattr(Path, "open", _open)
    if stage == "file_fsync":

        def _fsync_boom(*args: object, **kwargs: object) -> None:
            raise OSError("fsync fault")

        monkeypatch.setattr(outcome_module.os, "fsync", _fsync_boom)
    if stage == "dir_fsync":

        def _dir_boom(directory: Path) -> None:
            raise OSError("dir fsync fault")

        monkeypatch.setattr(outcome_module, "_fsync_outcome_directory", _dir_boom)

    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomePersistenceError
    ) as excinfo:  # noqa: E501
        persist_external_publication_recovery_resume_outcome(path, _outcome())
    _assert_persistence_error(excinfo.value, "ambiguous")
    # The artifact is retained (never cleaned up, rewritten, or deleted) and no
    # retry happened; a later exact retry may still be accepted.
    assert path.exists()
    monkeypatch.setattr(outcome_module.os, "fsync", real_fsync)
    monkeypatch.setattr(outcome_module, "_fsync_outcome_directory", real_dir_fsync)
    monkeypatch.setattr(Path, "open", real_open)
    assert path.exists()


def test_retained_exact_artifact_is_accepted_later(tmp_path: Path) -> None:
    path = tmp_path / "outcome.json"
    outcome = _outcome()
    persist_external_publication_recovery_resume_outcome(path, outcome)
    retained = path.read_bytes()
    persist_external_publication_recovery_resume_outcome(path, _outcome())
    assert path.read_bytes() == retained


def test_retained_partial_artifact_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "outcome.json"
    canonical = external_publication_recovery_resume_outcome_canonical_bytes(_outcome())
    path.write_bytes(canonical[: len(canonical) // 2])
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeConflictError):
        persist_external_publication_recovery_resume_outcome(path, _outcome())


# --- public surface / preflight ------------------------------------------


def test_signature_defaults_and_no_caller_authority_arguments() -> None:
    signature = inspect.signature(
        run_and_persist_external_publication_recovery_resume_outcome
    )
    assert tuple(signature.parameters) == _EXPECTED_PARAMETERS
    for name, parameter in signature.parameters.items():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, name
    for forbidden in (
        "resume_start_authorization_path",
        "resume_start_path",
        "start_path",
        "outcome_path",
        "phase297_result",
        "phase291_function",
        "phase290_function",
        "phase288_function",
    ):
        assert forbidden not in signature.parameters
    defaults = {n: p.default for n, p in signature.parameters.items()}
    assert (
        defaults["binding_loader"]
        is load_external_publication_recovery_resume_intent_binding
    )
    assert (
        defaults["authorization_loader"]
        is load_external_publication_recovery_resume_start_authorization
    )
    assert defaults["intent_loader"] is load_external_publication_operation_intent
    assert defaults["start_loader"] is load_external_publication_operation_start
    assert (
        defaults["expected_start_digest_function"]
        is external_publication_operation_start_digest
    )
    assert (
        defaults["start_digest_function"] is external_publication_operation_start_digest
    )
    assert (
        defaults["reconciliation_digest_function"]
        is external_publication_execution_reconciliation_digest
    )
    assert (
        defaults["phase297_function"]
        is run_external_publication_recovery_resume_start_handoff
    )


def test_api_is_keyword_only(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        run_and_persist_external_publication_recovery_resume_outcome(  # type: ignore[misc]
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            _resume_request(tmp_path),
        )


@pytest.mark.parametrize("bad_path", ["binding.json", None, 1, b"x"])
def test_preflight_rejects_non_path_binding(tmp_path: Path, bad_path: object) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(resume_intent_binding_path=bad_path)
    _assert_error(excinfo.value, "path_type")
    harness.assert_preflight_zero_call()


@pytest.mark.parametrize("bad_path", ["intent.json", None, 2, object()])
def test_preflight_rejects_non_path_intent(tmp_path: Path, bad_path: object) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(resume_intent_path=bad_path)
    _assert_error(excinfo.value, "path_type")
    harness.assert_preflight_zero_call()


def test_preflight_rejects_identical_paths(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(resume_intent_path=harness.lineage.binding_path)
    _assert_error(excinfo.value, "path_conflict")
    harness.assert_preflight_zero_call()


def test_preflight_rejects_fresh_request(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    forged = object.__new__(ExternalPublicationFreshOperationRequest)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(request=forged)
    _assert_error(excinfo.value, "request_contract")
    harness.assert_preflight_zero_call()


def test_preflight_rejects_request_lookalike(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    lookalike = _RequestLookalike(harness.lineage.request)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(request=lookalike)
    _assert_error(excinfo.value, "request_contract")
    harness.assert_preflight_zero_call()


@pytest.mark.parametrize(
    "field_name",
    [
        "ledger_directory",
        "execution_evidence_path",
        "execution_reconciliation_evidence_path",
    ],
)
@pytest.mark.parametrize("bad_value", ["relative.json", None, 3])
def test_preflight_rejects_non_path_request_fields(
    tmp_path: Path, field_name: str, bad_value: object
) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        ExternalPublicationResumeOperationRequest,
        harness.lineage.request,
        **{field_name: bad_value},
    )
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(request=forged)
    _assert_error(excinfo.value, "path_type")
    harness.assert_preflight_zero_call()


def test_preflight_rejects_non_approval_type(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        ExternalPublicationResumeOperationRequest,
        harness.lineage.request,
        approval="approval",
    )
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(request=forged)
    _assert_error(excinfo.value, "request_contract")
    harness.assert_preflight_zero_call()


@pytest.mark.parametrize(
    "bad_metadata",
    ["", " padded", "padded ", "\u0001control", "\ud800surrogate", "x" * 257, 12],
)
@pytest.mark.parametrize("field_name", ["approved_by", "approval_id"])
def test_preflight_rejects_bad_approval_metadata(
    tmp_path: Path, field_name: str, bad_metadata: object
) -> None:
    harness = _Harness(tmp_path)
    forged_approval = _forged_instance(
        ExternalPublicationApproval,
        harness.lineage.approval,
        **{field_name: bad_metadata},
    )
    forged_request = _forged_instance(
        ExternalPublicationResumeOperationRequest,
        harness.lineage.request,
        approval=forged_approval,
    )
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(request=forged_request)
    _assert_error(excinfo.value, "request_contract")
    harness.assert_preflight_zero_call()


def test_preflight_rejects_transport_attribute(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        ExternalPublicationResumeOperationRequest, harness.lineage.request
    )
    object.__setattr__(forged, "transport", lambda *args: None)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(request=forged)
    _assert_error(excinfo.value, "request_contract")
    harness.assert_preflight_zero_call()


@pytest.mark.parametrize(
    "dependency_name",
    [
        "binding_loader",
        "binding_digest_function",
        "authorization_loader",
        "authorization_digest_function",
        "intent_loader",
        "intent_digest_function",
        "approval_digest_function",
        "expected_start_digest_function",
        "start_loader",
        "start_digest_function",
        "reconciliation_digest_function",
        "phase297_function",
    ],
)
def test_preflight_rejects_non_callable_dependency(
    tmp_path: Path, dependency_name: str
) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(**{dependency_name: "not-callable"})
    _assert_error(excinfo.value, "configuration")
    harness.assert_preflight_zero_call()


def test_preflight_no_filesystem_mutation(tmp_path: Path) -> None:
    harness = _Harness(tmp_path, write_start_marker=False)
    before = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError):
        harness.run(
            request=_forged_instance(
                ExternalPublicationResumeOperationRequest,
                harness.lineage.request,
                ledger_directory="bad",
            )
        )
    assert sorted(p.name for p in tmp_path.iterdir()) == before == []


# --- binding / authorization / intent / expected start / request ---------


def test_binding_loaded_once_with_exact_path_identity(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    assert harness.binding_loader.call_count == 1
    assert harness.binding_loader.first_positional is harness.lineage.binding_path
    assert harness.binding_digest.call_count == 1
    assert harness.binding_digest.first_positional is harness.lineage.binding


@pytest.mark.parametrize("bad_binding", ["binding", None, 1, _intent(), object()])
def test_binding_non_exact_model_rejected(tmp_path: Path, bad_binding: object) -> None:
    harness = _Harness(tmp_path, binding=bad_binding)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "binding_contract")
    assert harness.binding_digest.call_count == 0
    harness.assert_phase297_zero_call()


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("schema_version", "external-publication-recovery-resume-intent-binding.v2"),
        ("resume_preparation_sha256", "A" * 64),
        ("recovery_decision_sha256", "z" * 64),
        ("publication_approval_sha256", _StringChild("c" * 64)),
        ("operation_intent_sha256", 5),
        ("source_operation", "replay"),
        ("recovery_kind", "mismatch"),
        ("operation", "fresh"),
        ("state", "pending"),
    ],
)
def test_binding_local_validation_independent_of_digest(
    tmp_path: Path, field_name: str, bad_value: object
) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(harness.lineage.binding, harness.lineage.binding)
    object.__setattr__(forged, field_name, bad_value)
    harness.binding_loader.result = forged
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "binding_contract")
    harness.assert_phase297_zero_call()


@pytest.mark.parametrize(
    "bad_digest", ["", "A" * 64, "a" * 63, _StringChild("a" * 64), None, 1]
)
def test_binding_digest_malformed_rejected(tmp_path: Path, bad_digest: object) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(binding_digest_function=_CallRecorder(result=bad_digest))
    _assert_error(excinfo.value, "binding_digest")
    harness.assert_phase297_zero_call()


def test_authorization_path_derived_from_binding_parent_and_digest(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    recorded = harness.authorization_loader.first_positional
    expected = harness.lineage.binding_path.parent / (
        f"{_AUTHORIZATION_FILENAME_PREFIX}{harness.lineage.binding_digest}"
        f"{_FILENAME_SUFFIX}"
    )
    assert type(recorded) is type(Path())
    assert recorded == expected
    assert recorded.parent == harness.lineage.binding_path.parent


def test_authorization_loaded_once_with_derived_identity(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    assert harness.authorization_loader.call_count == 1
    assert harness.authorization_digest.call_count == 1
    assert (
        harness.authorization_digest.first_positional is harness.lineage.authorization
    )


@pytest.mark.parametrize("bad_auth", ["authorization", None, 4, _binding()])
def test_authorization_non_exact_model_rejected(
    tmp_path: Path, bad_auth: object
) -> None:
    harness = _Harness(tmp_path, authorization=bad_auth)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "authorization_contract")
    assert harness.authorization_digest.call_count == 0
    harness.assert_phase297_zero_call()


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        (
            "schema_version",
            "external-publication-recovery-resume-start-authorization.v2",
        ),
        ("resume_intent_binding_sha256", "A" * 64),
        ("operation_intent_sha256", ""),
        ("expected_operation_start_sha256", _StringChild("6" * 64)),
        ("publication_approval_sha256", "c" * 65),
        ("source_operation", "replay"),
        ("recovery_kind", "unknown"),
        ("operation", "fresh"),
        ("state", "pending"),
    ],
)
def test_authorization_local_field_validation(
    tmp_path: Path, field_name: str, bad_value: object
) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        harness.lineage.authorization, harness.lineage.authorization
    )
    object.__setattr__(forged, field_name, bad_value)
    harness.authorization_loader.result = forged
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "authorization_contract")
    harness.assert_phase297_zero_call()


@pytest.mark.parametrize(
    "bad_digest", ["", "B" * 64, "b" * 63, _StringChild("b" * 64), None, 2]
)
def test_authorization_digest_malformed_rejected(
    tmp_path: Path, bad_digest: object
) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(authorization_digest_function=_CallRecorder(result=bad_digest))
    _assert_error(excinfo.value, "authorization_digest")
    harness.assert_phase297_zero_call()


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("resume_intent_binding_sha256", "9" * 64),
        ("resume_preparation_sha256", "9" * 64),
        ("recovery_decision_sha256", "9" * 64),
        ("operation_intent_sha256", "9" * 64),
        ("publication_approval_sha256", "9" * 64),
        ("publication_plan_sha256", "9" * 64),
        ("source_operation", "resume"),
    ],
)
def test_authorization_lineage_mismatch_rejected_before_intent(
    tmp_path: Path, field_name: str, bad_value: object
) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        harness.lineage.authorization, harness.lineage.authorization
    )
    object.__setattr__(forged, field_name, bad_value)
    harness.authorization_loader.result = forged
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "authorization_lineage")
    assert harness.intent_loader.call_count == 0
    harness.assert_phase297_zero_call()
    assert not harness.lineage.outcome_path.exists()


def test_intent_loaded_once_with_exact_path_identity(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    assert harness.intent_loader.call_count == 1
    assert harness.intent_loader.first_positional is harness.lineage.intent_path
    assert harness.intent_digest.call_count == 1
    assert harness.intent_digest.first_positional is harness.lineage.intent


@pytest.mark.parametrize("bad_intent", ["intent", None, 6, _binding()])
def test_intent_non_exact_model_rejected(tmp_path: Path, bad_intent: object) -> None:
    harness = _Harness(tmp_path, intent=bad_intent)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "intent_contract")
    assert harness.intent_digest.call_count == 0
    harness.assert_phase297_zero_call()


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("schema_version", "external-publication-operation-intent.v2"),
        ("publication_approval_sha256", "C" * 64),
        ("publication_plan_sha256", _StringChild("d" * 64)),
        ("operation", "fresh"),
    ],
)
def test_intent_local_validation(
    tmp_path: Path, field_name: str, bad_value: object
) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(harness.lineage.intent, harness.lineage.intent)
    object.__setattr__(forged, field_name, bad_value)
    harness.intent_loader.result = forged
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "intent_contract")
    harness.assert_phase297_zero_call()


@pytest.mark.parametrize(
    "bad_digest", ["", "E" * 64, "e" * 63, _StringChild("e" * 64), None, 3]
)
def test_intent_digest_malformed_rejected(tmp_path: Path, bad_digest: object) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(intent_digest_function=_CallRecorder(result=bad_digest))
    _assert_error(excinfo.value, "intent_digest")
    harness.assert_phase297_zero_call()


@pytest.mark.parametrize("kind", ["digest", "approval", "plan"])
def test_intent_lineage_mismatch_rejected_before_phase297(
    tmp_path: Path, kind: str
) -> None:
    harness = _Harness(tmp_path)
    lineage = harness.lineage
    other = "9" * 64
    if kind == "digest":
        mismatch = {"operation_intent_sha256": other}
    elif kind == "approval":
        mismatch = {"publication_approval_sha256": other}
    else:
        mismatch = {"publication_plan_sha256": other}
    harness.binding_loader.result = _forged_instance(
        lineage.binding, lineage.binding, **mismatch
    )
    harness.binding_digest.result = lineage.binding_digest
    harness.authorization_loader.result = _forged_instance(
        lineage.authorization, lineage.authorization, **mismatch
    )
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "intent_lineage")
    harness.assert_phase297_zero_call()
    assert not lineage.outcome_path.exists()


def test_expected_start_digest_uses_constructed_identity(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    constructed = harness.expected_start_digest.calls[0][0][0]
    assert type(constructed) is ExternalPublicationOperationStart
    assert constructed == harness.lineage.expected_start
    assert constructed.operation == "resume"
    assert constructed.state == "started"


@pytest.mark.parametrize(
    "bad_digest", ["", "F" * 64, "f" * 63, _StringChild("f" * 64), None, 8]
)
def test_expected_start_digest_malformed_rejected(
    tmp_path: Path, bad_digest: object
) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(expected_start_digest_function=_CallRecorder(result=bad_digest))
    _assert_error(excinfo.value, "start_digest")
    harness.assert_phase297_zero_call()


def test_expected_start_digest_must_equal_authorization(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    mismatch = "f" * 64
    assert mismatch != harness.lineage.expected_start_digest
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(expected_start_digest_function=_CallRecorder(result=mismatch))
    _assert_error(excinfo.value, "start_digest")
    harness.assert_phase297_zero_call()
    assert not harness.lineage.outcome_path.exists()


def test_request_approval_digest_uses_exact_identity(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    assert harness.approval_digest.call_count == 1
    assert harness.approval_digest.first_positional is harness.lineage.request.approval


@pytest.mark.parametrize(
    "bad_digest", ["", "C" * 64, "c" * 63, _StringChild("c" * 64), None, 9]
)
def test_request_approval_digest_malformed_rejected(
    tmp_path: Path, bad_digest: object
) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(approval_digest_function=_CallRecorder(result=bad_digest))
    _assert_error(excinfo.value, "request_lineage")
    harness.assert_phase297_zero_call()


def test_request_approval_digest_must_match_lineage(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(approval_digest_function=_CallRecorder(result="9" * 64))
    _assert_error(excinfo.value, "request_lineage")
    harness.assert_phase297_zero_call()
    assert not harness.lineage.outcome_path.exists()


def test_request_plan_digest_must_match_lineage(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    other = _approval(publication_plan_sha256="9" * 64)
    forged_request = _forged_instance(
        harness.lineage.request, harness.lineage.request, approval=other
    )
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(request=forged_request)
    _assert_error(excinfo.value, "request_lineage")
    harness.assert_phase297_zero_call()
    assert not harness.lineage.outcome_path.exists()


def test_binding_loader_known_error_identity_preserved(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    known = ExternalPublicationRecoveryResumeIntentBindingError("binding_contract")
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as excinfo:  # noqa: E501
        harness.run(binding_loader=_CallRecorder(fault=known))
    assert excinfo.value is known
    harness.assert_phase297_zero_call()


def test_authorization_loader_known_error_identity_preserved(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    known = ExternalPublicationRecoveryResumeStartAuthorizationError("load")
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationError
    ) as excinfo:
        harness.run(authorization_loader=_CallRecorder(fault=known))
    assert excinfo.value is known
    harness.assert_phase297_zero_call()


@pytest.mark.parametrize(
    "dependency_name",
    [
        "binding_loader",
        "binding_digest_function",
        "authorization_loader",
        "authorization_digest_function",
        "intent_loader",
        "intent_digest_function",
        "approval_digest_function",
        "expected_start_digest_function",
    ],
)
def test_unexpected_dependency_error_sanitized(
    tmp_path: Path, dependency_name: str
) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(**{dependency_name: _CallRecorder(fault=RuntimeError("secret"))})
    _assert_error(excinfo.value, "dependency_error")
    assert "secret" not in str(excinfo.value)
    harness.assert_phase297_zero_call()


# --- canonical paths -----------------------------------------------------


def test_start_and_outcome_paths_derived_from_binding_parent_and_digest(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    digest = harness.lineage.authorization_digest
    parent = harness.lineage.binding_path.parent
    expected_start = parent / f"{_START_FILENAME_PREFIX}{digest}{_FILENAME_SUFFIX}"
    expected_outcome = parent / f"{_OUTCOME_FILENAME_PREFIX}{digest}{_FILENAME_SUFFIX}"
    assert harness.lineage.start_path == expected_start
    assert harness.lineage.outcome_path == expected_outcome
    assert harness.lineage.outcome_path.exists()
    assert harness.start_loader.first_positional == expected_start


def test_different_authorization_digests_produce_different_outcome_names(
    tmp_path: Path,
) -> None:
    first = _Lineage(tmp_path)
    second = _Lineage(tmp_path)
    assert first.authorization_digest == second.authorization_digest
    assert first.outcome_path == second.outcome_path
    assert first.outcome_path.name.startswith(_OUTCOME_FILENAME_PREFIX)
    assert first.start_path.name != first.outcome_path.name


# --- existing outcome fast path ------------------------------------------


def test_existing_outcome_returns_identity_with_zero_phase297(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    expected = harness.lineage.build_outcome()
    persist_external_publication_recovery_resume_outcome(
        harness.lineage.outcome_path, expected
    )
    before = harness.lineage.outcome_path.read_bytes()
    harness.phase297.delegate = None
    harness.phase297.result = "must-not-be-used"
    result = harness.run()
    assert result == expected
    assert type(result) is ExternalPublicationRecoveryResumeOutcome
    harness.assert_phase297_zero_call()
    assert harness.reconciliation_digest.call_count == 0
    assert harness.lineage.outcome_path.read_bytes() == before


def test_existing_outcome_still_loads_and_binds_start(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    persist_external_publication_recovery_resume_outcome(
        harness.lineage.outcome_path, harness.lineage.build_outcome()
    )
    harness.run()
    assert harness.start_loader.call_count == 1
    assert harness.start_loader.first_positional == harness.lineage.start_path
    assert harness.start_digest.call_count == 1
    harness.assert_phase297_zero_call()


def test_existing_outcome_with_missing_start_fails_closed(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    persist_external_publication_recovery_resume_outcome(
        harness.lineage.outcome_path, harness.lineage.build_outcome()
    )
    harness.lineage.start_path.unlink()
    with pytest.raises(ExternalPublicationOperationStartError):
        harness.run()
    harness.assert_phase297_zero_call()


def test_existing_outcome_with_mismatched_start_fails_closed(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    persist_external_publication_recovery_resume_outcome(
        harness.lineage.outcome_path, harness.lineage.build_outcome()
    )
    other = _start(operation_intent_sha256="9" * 64)
    harness.lineage.start_path.write_bytes(
        external_publication_operation_start_canonical_bytes(other)
    )
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError):
        harness.run()
    harness.assert_phase297_zero_call()


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("resume_start_authorization_sha256", "9" * 64),
        ("resume_intent_binding_sha256", "9" * 64),
        ("operation_intent_sha256", "9" * 64),
        ("operation_start_sha256", "9" * 64),
        ("publication_approval_sha256", "9" * 64),
        ("publication_plan_sha256", "9" * 64),
    ],
)
def test_existing_outcome_lineage_mismatch_fails_closed(
    tmp_path: Path, field_name: str, bad_value: object
) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        harness.lineage.build_outcome(),
        harness.lineage.build_outcome(),
        **{field_name: bad_value},
    )
    persist_external_publication_recovery_resume_outcome(
        harness.lineage.outcome_path,
        forged,  # type: ignore[arg-type]
    )
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError):
        harness.run()
    harness.assert_phase297_zero_call()


def test_existing_outcome_malformed_bytes_fails_closed(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.lineage.outcome_path.write_bytes(b"{not canonical")
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeLoadError):
        harness.run()
    harness.assert_phase297_zero_call()
    assert harness.lineage.outcome_path.read_bytes() == b"{not canonical"


# --- phase297 absent-outcome path ----------------------------------------


def test_phase297_called_exactly_once_with_exact_kwargs(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    assert harness.phase297.call_count == 1
    args, kwargs = harness.phase297.calls[0]
    assert args == ()
    assert set(kwargs) == {
        "resume_intent_binding_path",
        "resume_intent_path",
        "request",
    }
    assert kwargs["resume_intent_binding_path"] is harness.lineage.binding_path
    assert kwargs["resume_intent_path"] is harness.lineage.intent_path
    assert kwargs["request"] is harness.lineage.request
    assert "phase291_function" not in kwargs
    assert "phase290_function" not in kwargs
    assert "phase288_function" not in kwargs


def test_phase297_not_retried_on_known_error(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    known = ExternalPublicationRecoveryResumeStartHandoffError("start_digest")
    recorder = _CallRecorder(fault=known)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(phase297_function=recorder)
    assert excinfo.value is known
    assert recorder.call_count == 1
    assert not harness.lineage.outcome_path.exists()


def test_phase297_lower_known_error_identity_preserved(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    known = ExternalPublicationOperationStartError("acquisition")
    recorder = _CallRecorder(fault=known)
    with pytest.raises(ExternalPublicationOperationStartError) as excinfo:
        harness.run(phase297_function=recorder)
    assert excinfo.value is known
    assert recorder.call_count == 1
    assert not harness.lineage.outcome_path.exists()


def test_phase297_unexpected_error_sanitized_and_no_outcome(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    recorder = _CallRecorder(fault=RuntimeError("secret detail"))
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(phase297_function=recorder)
    _assert_error(excinfo.value, "dependency_error")
    assert "secret detail" not in str(excinfo.value)
    assert recorder.call_count == 1
    assert not harness.lineage.outcome_path.exists()


# --- durable start after normal return -----------------------------------


def test_start_loaded_once_from_derived_path_and_digested(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    assert harness.start_loader.call_count == 1
    assert harness.start_loader.first_positional == harness.lineage.start_path
    assert type(harness.start_loader.first_positional) is type(Path())
    assert harness.start_digest.call_count == 1
    assert harness.start_digest.first_positional is harness.start_loader.first_result
    assert harness.phase297.call_count == 1


def test_start_absent_after_normal_return_creates_no_outcome(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    harness.lineage.start_path.unlink()
    # A known Phase 290 start loader error propagates with exact identity.
    with pytest.raises(ExternalPublicationOperationStartError):
        harness.run()
    assert harness.phase297.call_count == 1
    assert not harness.lineage.outcome_path.exists()


def test_start_malformed_after_normal_return_creates_no_outcome(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    harness.lineage.start_path.write_bytes(b"{bad")
    with pytest.raises(ExternalPublicationOperationStartError):
        harness.run()
    assert harness.phase297.call_count == 1
    assert not harness.lineage.outcome_path.exists()


def test_start_mismatched_after_normal_return_creates_no_outcome(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    other = _start(operation_intent_sha256="9" * 64)
    harness.lineage.start_path.write_bytes(
        external_publication_operation_start_canonical_bytes(other)
    )
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError):
        harness.run()
    assert harness.phase297.call_count == 1
    assert not harness.lineage.outcome_path.exists()


def test_start_digest_mismatch_creates_no_outcome(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError):
        harness.run(start_digest_function=_CallRecorder(result="9" * 64))
    assert harness.phase297.call_count == 1
    assert not harness.lineage.outcome_path.exists()


# --- already_acquired classification -------------------------------------


def test_already_acquired_produces_recovery_required_none(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    result = harness.run()
    assert type(result) is ExternalPublicationRecoveryResumeOutcome
    assert result.state == "recovery_required"
    assert result.result_kind == "none"
    assert result.result_sha256 is None
    assert harness.reconciliation_digest.call_count == 0
    assert harness.lineage.outcome_path.exists()


def test_already_acquired_persisted_fields_match_lineage(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    result = harness.run()
    assert result.resume_start_authorization_sha256 == (
        harness.lineage.authorization_digest
    )
    assert result.resume_intent_binding_sha256 == harness.lineage.binding_digest
    assert result.operation_intent_sha256 == harness.lineage.intent_digest
    assert result.operation_start_sha256 == harness.lineage.expected_start_digest
    assert result.publication_approval_sha256 == harness.lineage.approval_digest
    assert result.publication_plan_sha256 == harness.lineage.plan_digest
    assert result.operation == "resume"
    assert result.schema_version == _OUTCOME_SCHEMA


def test_acquired_acquisition_rejected(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    acquired = ExternalPublicationOperationStartAcquisition(
        status="acquired",
        start=harness.lineage.expected_start,  # type: ignore[arg-type]
    )
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(phase297_result=acquired)
    _assert_error(excinfo.value, "result_contract")
    assert not harness.lineage.outcome_path.exists()


def test_acquisition_embedded_start_mismatch_rejected(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    other = _start(operation_intent_sha256="9" * 64)
    forged = _forged_instance(
        harness.phase297_result, harness.phase297_result, start=other
    )
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError):
        harness.run(phase297_result=forged)
    assert not harness.lineage.outcome_path.exists()


def test_acquisition_status_not_string_rejected(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        harness.phase297_result,
        harness.phase297_result,
        status=_StringChild("already_acquired"),
    )
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError):
        harness.run(phase297_result=forged)


# --- reconciliation classification ---------------------------------------


def test_reconciliation_matched_is_completed(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    reconciliation = _reconciliation(status="matched")
    result = harness.run(phase297_result=reconciliation)
    assert result.state == "completed"
    assert result.result_kind == "reconciliation"
    assert result.result_sha256 == (
        external_publication_execution_reconciliation_digest(reconciliation)
    )
    assert harness.reconciliation_digest.call_count == 1
    assert harness.reconciliation_digest.first_positional is reconciliation


def test_reconciliation_mismatch_is_recovery_required(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    reconciliation = _reconciliation(
        status="lineage_mismatch", mismatched_fields=("provider",)
    )
    result = harness.run(phase297_result=reconciliation)
    assert result.state == "recovery_required"
    assert result.result_kind == "reconciliation"
    assert result.result_sha256 == (
        external_publication_execution_reconciliation_digest(reconciliation)
    )


# Phase 283 canonical lineage-field order, used for ordering regressions.
_CANONICAL_LINEAGE_ORDER = (
    "publication_attempt_claim_sha256",
    "regeneration_id",
    "publication_plan_sha256",
    "publication_approval_sha256",
    "business_output_sha256",
    "output_byte_length",
    "provider",
    "publication_target_sha256",
)


def _forged_reconciliation(
    *, status: object, mismatched_fields: object
) -> ExternalPublicationExecutionReconciliation:
    """Allocate an exact runtime instance without running model validation."""
    return _forged_instance(  # type: ignore[return-value]
        ExternalPublicationExecutionReconciliation,
        _reconciliation(),
        status=status,
        mismatched_fields=mismatched_fields,
    )


@pytest.mark.parametrize(
    ("case", "status", "mismatched_fields"),
    [
        ("matched_nonempty", "matched", ("provider",)),
        ("mismatch_empty", "lineage_mismatch", ()),
        ("unknown_field", "lineage_mismatch", ("not_a_lineage_field",)),
        ("duplicate_field", "lineage_mismatch", ("provider", "provider")),
        (
            "noncanonical_order",
            "lineage_mismatch",
            ("provider", "regeneration_id"),
        ),
    ],
)
def test_reconciliation_local_contract_rejected_before_digest(
    tmp_path: Path, case: str, status: object, mismatched_fields: object
) -> None:
    """A forged exact instance must fail locally, never via the digest helper."""
    harness = _Harness(tmp_path)
    forged = _forged_reconciliation(status=status, mismatched_fields=mismatched_fields)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(phase297_result=forged)
    _assert_error(excinfo.value, "result_contract")
    # Phase 297 ran exactly once and was never retried.
    assert harness.phase297.call_count == 1
    # The injected digest helper must not be trusted to discover the violation.
    assert harness.reconciliation_digest.call_count == 0
    # No Phase 298 outcome is persisted for an invalid reconciliation.
    assert not harness.lineage.outcome_path.exists()


def test_reconciliation_noncanonical_order_is_not_canonical_equivalent(
    tmp_path: Path,
) -> None:
    """The same field set in the wrong order is not a valid Phase 283 record."""
    harness = _Harness(tmp_path)
    reversed_fields = tuple(reversed(_CANONICAL_LINEAGE_ORDER))
    forged = _forged_reconciliation(
        status="lineage_mismatch", mismatched_fields=reversed_fields
    )
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(phase297_result=forged)
    _assert_error(excinfo.value, "result_contract")
    assert harness.phase297.call_count == 1
    assert harness.reconciliation_digest.call_count == 0
    assert not harness.lineage.outcome_path.exists()


def test_reconciliation_canonical_order_accepted(tmp_path: Path) -> None:
    """A canonical non-empty tuple is the valid lineage_mismatch shape."""
    harness = _Harness(tmp_path)
    canonical = (
        "regeneration_id",
        "provider",
        "publication_target_sha256",
    )
    assert (
        tuple(field for field in _CANONICAL_LINEAGE_ORDER if field in canonical)
        == canonical
    )
    reconciliation = _reconciliation(
        status="lineage_mismatch", mismatched_fields=canonical
    )
    result = harness.run(phase297_result=reconciliation)
    assert result.state == "recovery_required"
    assert result.result_kind == "reconciliation"
    assert harness.reconciliation_digest.call_count == 1
    assert harness.reconciliation_digest.first_positional is reconciliation
    assert harness.lineage.outcome_path.exists()


@pytest.mark.parametrize(
    "bad_digest", ["", "D" * 64, "d" * 63, _StringChild("d" * 64), None, 4]
)
def test_reconciliation_digest_malformed_rejected(
    tmp_path: Path, bad_digest: object
) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(
            phase297_result=_reconciliation(),
            reconciliation_digest_function=_CallRecorder(result=bad_digest),
        )
    _assert_error(excinfo.value, "result_digest")
    assert not harness.lineage.outcome_path.exists()


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("schema_version", "external-publication-execution-reconciliation.v2"),
        ("claim_sha256", "A" * 64),
        ("execution_evidence_sha256", ""),
        ("status", "unknown"),
        ("mismatched_fields", ["provider"]),
    ],
)
def test_reconciliation_malformed_rejected(
    tmp_path: Path, field_name: str, bad_value: object
) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        _reconciliation(), _reconciliation(), **{field_name: bad_value}
    )
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(phase297_result=forged)
    _assert_error(excinfo.value, "result_contract")


def test_reconciliation_subclass_rejected(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)

    class _Subclass(ExternalPublicationExecutionReconciliation):
        pass

    forged = object.__new__(_Subclass)
    for field in dataclasses.fields(ExternalPublicationExecutionReconciliation):
        object.__setattr__(forged, field.name, getattr(_reconciliation(), field.name))
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(phase297_result=forged)
    _assert_error(excinfo.value, "result_contract")


@pytest.mark.parametrize("bad_result", [None, "result", 7, object(), b"x"])
def test_arbitrary_result_rejected(tmp_path: Path, bad_result: object) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(phase297_result=bad_result)
    _assert_error(excinfo.value, "result_contract")
    assert not harness.lineage.outcome_path.exists()


def test_fresh_execution_result_lookalike_rejected(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)

    class _FreshLookalike:
        schema_version = "external-publication-execution.v1"

    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as excinfo:
        harness.run(phase297_result=_FreshLookalike())
    _assert_error(excinfo.value, "result_contract")


def test_phase297_result_not_called_twice_on_bad_result(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError):
        harness.run(phase297_result=object())
    assert harness.phase297.call_count == 1


# --- persistence of constructed outcome ----------------------------------


def test_persist_exactly_once_no_retry(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    assert harness.lineage.outcome_path.exists()
    persisted = load_external_publication_recovery_resume_outcome(
        harness.lineage.outcome_path
    )
    assert persisted.state == "recovery_required"
    assert persisted.result_kind == "none"


def test_existing_completed_outcome_is_fast_path(tmp_path: Path) -> None:
    """An existing exact outcome is the stop boundary: zero Phase 297 calls."""
    harness = _Harness(tmp_path)
    existing = harness.lineage.build_outcome(
        state="completed", result_kind="reconciliation", result_sha256="a" * 64
    )
    persist_external_publication_recovery_resume_outcome(
        harness.lineage.outcome_path, existing
    )
    before = harness.lineage.outcome_path.read_bytes()
    result = harness.run()
    assert result == existing
    assert result.state == "completed"
    harness.assert_phase297_zero_call()
    assert harness.reconciliation_digest.call_count == 0
    assert harness.lineage.outcome_path.read_bytes() == before


# --- crash semantics -----------------------------------------------------


def test_crash_before_persist_then_already_acquired(tmp_path: Path) -> None:
    """A lost prior result is never inferred; the second run records uncertainty."""
    harness = _Harness(tmp_path)
    lineage = harness.lineage
    # Prior invocation acquired the marker but crashed before persisting.
    assert lineage.start_path.exists()
    assert not lineage.outcome_path.exists()

    # The second invocation sees no outcome and Phase297 stops already_acquired.
    result = harness.run()
    assert harness.phase297.call_count == 1
    assert result.state == "recovery_required"
    assert result.result_kind == "none"
    assert result.result_sha256 is None
    assert lineage.outcome_path.exists()
    marker_before = lineage.start_path.read_bytes()
    # No replay: no new start marker bytes, no Phase288 execution evidence.
    assert lineage.start_path.read_bytes() == marker_before
    assert not (tmp_path / "execution-reconciliation-evidence.json").exists()


def test_second_run_after_persist_is_zero_call(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    first = harness.run()
    assert harness.phase297.call_count == 1
    second = harness.run()
    assert harness.phase297.call_count == 1
    assert second == first


# --- source audit --------------------------------------------------------


def _module_tree() -> ast.Module:
    return ast.parse(_SOURCE)


def _imported_names() -> tuple[set[str], set[str]]:
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                modules.add(node.module or "")
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
    return modules, names


def _called_names() -> set[str]:
    called: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    return called


def test_source_audit_exact_import_set() -> None:
    modules, _ = _imported_names()
    assert modules == set(_EXPECTED_IMPORT_MODULES)


def test_source_audit_no_phase292_or_lower_orchestration() -> None:
    _, names = _imported_names()
    called = _called_names()
    forbidden = {
        "run_external_publication_operation_start_handoff",
        "acquire_external_publication_operation_start",
        "run_external_publication_operation",
        "execute_and_persist_approved_external_publication",
        "resume_external_publication_reconciliation_closure",
        "authorize_and_persist_external_publication_recovery_resume_start",
        "materialize_and_bind_external_publication_recovery_resume_intent",
        "prepare_and_persist_external_publication_recovery_resume_lineage",
        "decide_and_persist_external_publication_recovery",
        "persist_external_publication_operation_lifecycle_outcome",
    }
    assert names & forbidden == set()
    assert called & forbidden == set()


def test_source_audit_only_public_phase297_dependency() -> None:
    _, names = _imported_names()
    called = _called_names()
    assert "run_external_publication_recovery_resume_start_handoff" in names
    assert "run_external_publication_recovery_resume_start_handoff" not in called
    assert "phase297_function" in _SOURCE


@pytest.mark.parametrize(
    "token",
    [
        "os.environ",
        "os.getenv",
        "uuid4",
        "token_hex",
        "socket",
        "subprocess",
        "Path.resolve",
        ".resolve(",
        "realpath",
        "normpath",
        "abspath",
        "samefile",
        "readlink",
    ],
)
def test_source_audit_no_ambient_or_normalizing_apis(token: str) -> None:
    assert token not in _SOURCE


def test_no_cli_change_and_no_phase298_command() -> None:
    from typer.testing import CliRunner

    from ai_office.cli import app

    runner = CliRunner()
    root = runner.invoke(app, ["--help"])
    assert root.exit_code == 0
    assert "resume_outcome" not in root.output.lower()

    workflows = runner.invoke(app, ["workflows", "--help"])
    assert workflows.exit_code == 0
    assert "resume_outcome" not in workflows.output.lower()
    assert "phase298" not in workflows.output.lower().replace(" ", "")

    cli_source = Path("src/ai_office/cli.py").read_text(encoding="utf-8")
    assert "resume_outcome" not in cli_source
    assert "phase298" not in cli_source.lower().replace(" ", "")
