"""Focused and integration regressions for the Phase 297 recovery resume handoff."""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_resume_start_handoff as handoff_module  # noqa: E501
from ai_office.engine import (
    ExternalPublicationApproval,
    ExternalPublicationError,
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationLifecycleOutcome,
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartAcquisition,
    ExternalPublicationOperationStartError,
    ExternalPublicationPlan,
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeIntentBindingError,
    ExternalPublicationRecoveryResumeIntentBindingLoadError,
    ExternalPublicationRecoveryResumeStartAuthorization,
    ExternalPublicationRecoveryResumeStartHandoffCompatibilityError,
    ExternalPublicationRecoveryResumeStartHandoffError,
    ExternalPublicationRecoveryResumeStartHandoffFailureDetail,
    ExternalPublicationResumeOperationRequest,
    acquire_external_publication_operation_start,
    approve_external_publication,
    authorize_and_persist_external_publication_recovery_resume_start,
    build_external_publication_operation_intent,
    decide_and_persist_external_publication_recovery,
    external_publication_approval_digest,
    external_publication_operation_intent_digest,
    external_publication_operation_start_digest,
    external_publication_recovery_resume_intent_binding_digest,
    external_publication_recovery_resume_start_authorization_digest,
    load_external_publication_operation_intent,
    load_external_publication_operation_start,
    load_external_publication_recovery_resume_intent_binding,
    load_external_publication_recovery_resume_start_authorization,
    materialize_and_bind_external_publication_recovery_resume_intent,
    persist_external_publication_operation_intent,
    persist_external_publication_operation_lifecycle_outcome,
    prepare_and_persist_external_publication_recovery_resume_lineage,
    run_external_publication_operation_start_handoff,
    run_external_publication_recovery_resume_start_handoff,
)

_BINDING_SCHEMA = "external-publication-recovery-resume-intent-binding.v1"
_AUTHORIZATION_SCHEMA = "external-publication-recovery-resume-start-authorization.v1"
_INTENT_SCHEMA = "external-publication-operation-intent.v1"
_START_SCHEMA = "external-publication-operation-start.v1"
_LIFECYCLE_SCHEMA = "external-publication-operation-lifecycle-outcome.v1"
_RECONCILIATION_SCHEMA = "external-publication-execution-reconciliation.v1"
_MESSAGE = "external publication recovery resume start handoff is blocked"
_AUTHORIZATION_FILENAME_PREFIX = (
    "external-publication-recovery-resume-start-authorization-"
)
_START_FILENAME_PREFIX = "external-publication-recovery-resume-start-"
_FILENAME_SUFFIX = ".json"
_SOURCE = Path(handoff_module.__file__).read_text(encoding="utf-8")
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
    "start_digest_function",
    "phase291_function",
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
    }
)


class _StringChild(str):
    pass


_UNSET: object = object()


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
    if source is None:
        raise AssertionError("source instance is required")
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
    assert (
        type(error) is ExternalPublicationRecoveryResumeStartHandoffCompatibilityError
    )
    assert isinstance(error, ExternalPublicationRecoveryResumeStartHandoffError)
    assert isinstance(error, ValueError)
    assert str(error) == _MESSAGE
    assert (
        type(error.detail) is ExternalPublicationRecoveryResumeStartHandoffFailureDetail
    )
    assert error.detail.classification == classification
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


# --- builders ------------------------------------------------------------


def _binding(
    *,
    schema_version: object = _BINDING_SCHEMA,
    resume_preparation_sha256: object = "a" * 64,
    recovery_decision_sha256: object = "b" * 64,
    publication_approval_sha256: object = "c" * 64,
    publication_plan_sha256: object = "d" * 64,
    operation_intent_sha256: object = "e" * 64,
    source_operation: object = "fresh",
    recovery_kind: object = "already_acquired",
    operation: object = "resume",
    state: object = "authorized",
) -> ExternalPublicationRecoveryResumeIntentBinding:
    return ExternalPublicationRecoveryResumeIntentBinding(
        schema_version=schema_version,  # type: ignore[arg-type]
        resume_preparation_sha256=resume_preparation_sha256,  # type: ignore[arg-type]
        recovery_decision_sha256=recovery_decision_sha256,  # type: ignore[arg-type]
        publication_approval_sha256=publication_approval_sha256,  # type: ignore[arg-type]
        publication_plan_sha256=publication_plan_sha256,  # type: ignore[arg-type]
        operation_intent_sha256=operation_intent_sha256,  # type: ignore[arg-type]
        source_operation=source_operation,  # type: ignore[arg-type]
        recovery_kind=recovery_kind,  # type: ignore[arg-type]
        operation=operation,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
    )


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


def _approval(
    *,
    approved: object = True,
    publication_plan_sha256: object = "d" * 64,
    approved_by: object = "human-reviewer-297",
    approval_id: object = "approval-297",
) -> ExternalPublicationApproval:
    return ExternalPublicationApproval(
        approved=approved,  # type: ignore[arg-type]
        publication_plan_sha256=publication_plan_sha256,  # type: ignore[arg-type]
        approved_by=approved_by,  # type: ignore[arg-type]
        approval_id=approval_id,  # type: ignore[arg-type]
    )


def _start(
    *,
    schema_version: object = _START_SCHEMA,
    operation_intent_sha256: object = "e" * 64,
    publication_approval_sha256: object = "c" * 64,
    publication_plan_sha256: object = "d" * 64,
    operation: object = "resume",
    state: object = "started",
) -> ExternalPublicationOperationStart:
    return ExternalPublicationOperationStart(
        schema_version=schema_version,  # type: ignore[arg-type]
        operation_intent_sha256=operation_intent_sha256,  # type: ignore[arg-type]
        publication_approval_sha256=publication_approval_sha256,  # type: ignore[arg-type]
        publication_plan_sha256=publication_plan_sha256,  # type: ignore[arg-type]
        operation=operation,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
    )


def _reconciliation(
    *,
    schema_version: object = _RECONCILIATION_SCHEMA,
    claim_sha256: object = "a" * 64,
    execution_evidence_sha256: object = "b" * 64,
    status: object = "matched",
    mismatched_fields: object = (),
) -> ExternalPublicationExecutionReconciliation:
    return ExternalPublicationExecutionReconciliation(
        schema_version=schema_version,  # type: ignore[arg-type]
        claim_sha256=claim_sha256,  # type: ignore[arg-type]
        execution_evidence_sha256=execution_evidence_sha256,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        mismatched_fields=mismatched_fields,  # type: ignore[arg-type]
    )


def _resume_request(
    root: Path,
    *,
    approval: object = None,
    ledger_directory: object = None,
    execution_evidence_path: object = None,
    execution_reconciliation_evidence_path: object = None,
) -> ExternalPublicationResumeOperationRequest:
    return ExternalPublicationResumeOperationRequest(
        ledger_directory=(
            root / "ledger" if ledger_directory is None else ledger_directory
        ),  # type: ignore[arg-type]
        approval=_approval() if approval is None else approval,  # type: ignore[arg-type]
        execution_evidence_path=(
            root / "execution-evidence.json"
            if execution_evidence_path is None
            else execution_evidence_path
        ),  # type: ignore[arg-type]
        execution_reconciliation_evidence_path=(
            root / "execution-reconciliation-evidence.json"
            if execution_reconciliation_evidence_path is None
            else execution_reconciliation_evidence_path
        ),  # type: ignore[arg-type]
    )


def _expected_start_for(intent_digest: str, approval: object, plan: object) -> object:
    return _start(
        operation_intent_sha256=intent_digest,
        publication_approval_sha256=approval,
        publication_plan_sha256=plan,
    )


class _Lineage:
    """One exactly self-consistent Phase 295/296/289/runtime request lineage."""

    def __init__(self, root: Path) -> None:
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
        self.expected_start = _expected_start_for(
            self.intent_digest, self.approval_digest, self.plan_digest
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
        phase291_result: object = _UNSET,
        lineage: _Lineage | None = None,
    ) -> None:
        self.lineage = lineage if lineage is not None else _Lineage(root)
        self.root = root
        self.binding = self.lineage.binding if binding is _UNSET else binding
        self.authorization = (
            self.lineage.authorization if authorization is _UNSET else authorization
        )
        self.intent = self.lineage.intent if intent is _UNSET else intent
        self.request = self.lineage.request if request is _UNSET else request
        self.phase291_result = (
            ExternalPublicationOperationStartAcquisition(
                status="already_acquired",
                start=self.lineage.expected_start,  # type: ignore[arg-type]
            )
            if phase291_result is _UNSET
            else phase291_result
        )
        self.binding_loader = _CallRecorder(result=self.binding)
        self.binding_digest = _CallRecorder(result=self.lineage.binding_digest)
        self.authorization_loader = _CallRecorder(result=self.authorization)
        self.authorization_digest = _CallRecorder(
            result=self.lineage.authorization_digest
        )
        self.intent_loader = _CallRecorder(result=self.intent)
        self.intent_digest = _CallRecorder(result=self.lineage.intent_digest)
        self.approval_digest = _CallRecorder(result=self.lineage.approval_digest)
        self.start_digest = _CallRecorder(
            delegate=external_publication_operation_start_digest
        )
        self.phase291 = _CallRecorder(result=self.phase291_result)

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
            "start_digest_function": self.start_digest,
            "phase291_function": self.phase291,
        }

    def run(
        self,
        *,
        phase291_result: object = _UNSET,
        phase291_fault: object = _UNSET,
        **overrides: object,
    ) -> object:
        if phase291_result is not _UNSET:
            self.phase291.result = phase291_result
        if phase291_fault is not _UNSET:
            self.phase291.fault = phase291_fault
        kwargs = self.kwargs()
        kwargs.update(overrides)
        return handoff_module.run_external_publication_recovery_resume_start_handoff(
            **kwargs  # type: ignore[arg-type]
        )

    def assert_phase291_zero_call(self) -> None:
        assert self.phase291.call_count == 0

    def assert_preflight_zero_call(self) -> None:
        self.assert_phase291_zero_call()
        assert self.binding_loader.call_count == 0
        assert self.binding_digest.call_count == 0
        assert self.authorization_loader.call_count == 0
        assert self.authorization_digest.call_count == 0
        assert self.intent_loader.call_count == 0
        assert self.intent_digest.call_count == 0
        assert self.start_digest.call_count == 0


# --- public surface ------------------------------------------------------


def test_public_exports_and_error_family() -> None:
    assert handoff_module.__all__ == [
        "ExternalPublicationRecoveryResumeStartHandoffCompatibilityError",
        "ExternalPublicationRecoveryResumeStartHandoffError",
        "ExternalPublicationRecoveryResumeStartHandoffFailureDetail",
        "run_external_publication_recovery_resume_start_handoff",
    ]
    assert (
        run_external_publication_recovery_resume_start_handoff
        is handoff_module.run_external_publication_recovery_resume_start_handoff
    )


def test_error_family_hierarchy_and_messages() -> None:
    error = ExternalPublicationRecoveryResumeStartHandoffError()
    assert isinstance(error, ValueError)
    assert str(error) == _MESSAGE
    assert error.detail.classification == "dependency_error"
    compatibility = ExternalPublicationRecoveryResumeStartHandoffCompatibilityError(
        "result_contract"
    )
    assert isinstance(compatibility, ExternalPublicationRecoveryResumeStartHandoffError)
    assert str(compatibility) == _MESSAGE
    assert compatibility.detail.classification == "result_contract"
    assert compatibility.__cause__ is None
    assert _field_names(ExternalPublicationRecoveryResumeStartHandoffFailureDetail) == {
        "classification"
    }


def test_signature_defaults_and_no_caller_authority_arguments() -> None:
    signature = inspect.signature(
        run_external_publication_recovery_resume_start_handoff
    )
    assert tuple(signature.parameters) == _EXPECTED_PARAMETERS
    for name, parameter in signature.parameters.items():
        if name in _EXPECTED_PARAMETERS[:3]:
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
            assert parameter.default is inspect.Parameter.empty
        else:
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert "authorization_path" not in signature.parameters
    assert "start_path" not in signature.parameters
    assert "phase290_function" not in signature.parameters
    assert "phase288_function" not in signature.parameters
    defaults = {
        name: parameter.default for name, parameter in signature.parameters.items()
    }
    assert (
        defaults["binding_loader"]
        is load_external_publication_recovery_resume_intent_binding
    )
    assert (
        defaults["binding_digest_function"]
        is external_publication_recovery_resume_intent_binding_digest
    )
    assert (
        defaults["authorization_loader"]
        is load_external_publication_recovery_resume_start_authorization
    )
    assert (
        defaults["authorization_digest_function"]
        is external_publication_recovery_resume_start_authorization_digest
    )
    assert defaults["intent_loader"] is load_external_publication_operation_intent
    assert (
        defaults["intent_digest_function"]
        is external_publication_operation_intent_digest
    )
    assert defaults["approval_digest_function"] is external_publication_approval_digest
    assert (
        defaults["start_digest_function"] is external_publication_operation_start_digest
    )
    assert (
        defaults["phase291_function"]
        is run_external_publication_operation_start_handoff
    )


def test_api_is_keyword_only(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        handoff_module.run_external_publication_recovery_resume_start_handoff(  # type: ignore[misc]
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            _resume_request(tmp_path),
        )


# --- preflight -----------------------------------------------------------


@pytest.mark.parametrize("bad_path", ["binding.json", None, 1, b"x"])
def test_preflight_rejects_non_path_binding(tmp_path: Path, bad_path: object) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(resume_intent_binding_path=bad_path)
    _assert_error(excinfo.value, "path_type")
    harness.assert_phase291_zero_call()


@pytest.mark.parametrize("bad_path", ["intent.json", None, 2, object()])
def test_preflight_rejects_non_path_intent(tmp_path: Path, bad_path: object) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(resume_intent_path=bad_path)
    _assert_error(excinfo.value, "path_type")
    harness.assert_phase291_zero_call()


def test_preflight_rejects_identical_paths(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(resume_intent_path=harness.lineage.binding_path)
    _assert_error(excinfo.value, "path_conflict")
    harness.assert_phase291_zero_call()


def test_preflight_rejects_fresh_request(tmp_path: Path) -> None:
    from ai_office.engine import ExternalPublicationFreshOperationRequest

    harness = _Harness(tmp_path)
    forged = object.__new__(ExternalPublicationFreshOperationRequest)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(request=forged)
    _assert_error(excinfo.value, "request_contract")
    harness.assert_phase291_zero_call()
    assert harness.binding_loader.call_count == 0


def test_preflight_rejects_request_lookalike(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    lookalike = _RequestLookalike(harness.lineage.request)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(request=lookalike)
    _assert_error(excinfo.value, "request_contract")
    harness.assert_phase291_zero_call()


def test_preflight_rejects_forged_request_subclass_operand(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        ExternalPublicationResumeOperationRequest,
        harness.lineage.request,
        ledger_directory="not-a-path",
    )
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(request=forged)
    _assert_error(excinfo.value, "path_type")
    harness.assert_phase291_zero_call()


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
        "start_digest_function",
        "phase291_function",
    ],
)
def test_preflight_rejects_non_callable_dependency(
    tmp_path: Path, dependency_name: str
) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(**{dependency_name: "not-callable"})
    _assert_error(excinfo.value, "configuration")
    harness.assert_phase291_zero_call()


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
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(request=forged)
    _assert_error(excinfo.value, "path_type")
    harness.assert_phase291_zero_call()


def test_preflight_rejects_missing_request_path_field(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        ExternalPublicationResumeOperationRequest, harness.lineage.request
    )
    object.__delattr__(forged, "execution_evidence_path")
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(request=forged)
    _assert_error(excinfo.value, "path_type")
    harness.assert_phase291_zero_call()


def test_preflight_rejects_non_approval_type(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        ExternalPublicationResumeOperationRequest,
        harness.lineage.request,
        approval="approval",
    )
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(request=forged)
    _assert_error(excinfo.value, "request_contract")
    harness.assert_phase291_zero_call()


def test_preflight_rejects_approval_not_true(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    forged_approval = _forged_instance(
        ExternalPublicationApproval, harness.lineage.approval, approved=False
    )
    forged_request = _forged_instance(
        ExternalPublicationResumeOperationRequest,
        harness.lineage.request,
        approval=forged_approval,
    )
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(request=forged_request)
    _assert_error(excinfo.value, "request_contract")
    harness.assert_phase291_zero_call()


@pytest.mark.parametrize(
    "bad_metadata",
    [
        "",
        " padded",
        "padded ",
        "\u0001control",
        "\ud800surrogate",
        "x" * 257,
        123,
        None,
    ],
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
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(request=forged_request)
    _assert_error(excinfo.value, "request_contract")
    harness.assert_phase291_zero_call()


def test_preflight_accepts_metadata_at_length_limit(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    forged_approval = _forged_instance(
        ExternalPublicationApproval,
        harness.lineage.approval,
        approved_by="x" * 256,
        approval_id="y" * 256,
    )
    forged_request = _forged_instance(
        ExternalPublicationResumeOperationRequest,
        harness.lineage.request,
        approval=forged_approval,
    )
    result = harness.run(request=forged_request)
    assert result is harness.phase291_result
    assert harness.phase291.call_count == 1


def test_preflight_rejects_transport_attribute(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        ExternalPublicationResumeOperationRequest, harness.lineage.request
    )
    object.__setattr__(forged, "transport", lambda *args: None)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(request=forged)
    _assert_error(excinfo.value, "request_contract")
    harness.assert_phase291_zero_call()


def test_preflight_performs_no_filesystem_mutation(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    before = sorted(path.name for path in tmp_path.iterdir())
    harness.run()
    after = sorted(path.name for path in tmp_path.iterdir())
    assert before == after == []


# --- binding -------------------------------------------------------------


def test_binding_loader_called_once_with_exact_path_identity(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    assert harness.binding_loader.call_count == 1
    assert harness.binding_loader.first_positional is harness.lineage.binding_path
    assert harness.binding_digest.call_count == 1
    assert harness.binding_digest.first_positional is harness.lineage.binding


@pytest.mark.parametrize(
    "bad_binding",
    ["binding", None, 1, _intent(), object()],
)
def test_binding_loader_non_exact_model_rejected(
    tmp_path: Path, bad_binding: object
) -> None:
    harness = _Harness(tmp_path, binding=bad_binding)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "binding_contract")
    assert harness.binding_digest.call_count == 0
    harness.assert_phase291_zero_call()


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("schema_version", "external-publication-recovery-resume-intent-binding.v2"),
        ("resume_preparation_sha256", "A" * 64),
        ("resume_preparation_sha256", ""),
        ("recovery_decision_sha256", "z" * 64),
        ("publication_approval_sha256", _StringChild("c" * 64)),
        ("publication_plan_sha256", "d" * 63),
        ("operation_intent_sha256", 5),
        ("source_operation", "replay"),
        ("recovery_kind", "mismatch"),
        ("operation", "fresh"),
        ("state", "pending"),
    ],
)
def test_binding_local_validation_is_independent_of_digest(
    tmp_path: Path, field_name: str, bad_value: object
) -> None:
    """A malformed binding must fail closed before any digest is trusted."""
    harness = _Harness(tmp_path)
    forged = _forged_instance(harness.lineage.binding, harness.lineage.binding, **{})
    object.__setattr__(forged, field_name, bad_value)
    harness.binding_loader.result = forged
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "binding_contract")
    assert harness.binding_digest.call_count == 0
    harness.assert_phase291_zero_call()


def test_binding_rejects_reconciliation_mismatch_with_fresh_source(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        harness.lineage.binding,
        harness.lineage.binding,
        recovery_kind="reconciliation_mismatch",
    )
    harness.binding_loader.result = forged
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "binding_contract")
    harness.assert_phase291_zero_call()


@pytest.mark.parametrize(
    "bad_digest",
    ["", "A" * 64, "a" * 63, "a" * 65, _StringChild("a" * 64), None, 1],
)
def test_binding_digest_malformed_rejected(tmp_path: Path, bad_digest: object) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(binding_digest_function=_CallRecorder(result=bad_digest))
    _assert_error(excinfo.value, "binding_digest")
    harness.assert_phase291_zero_call()


def test_binding_loader_known_error_identity_preserved(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    known = ExternalPublicationRecoveryResumeIntentBindingLoadError()
    with pytest.raises(
        ExternalPublicationRecoveryResumeIntentBindingLoadError
    ) as excinfo:
        harness.run(binding_loader=_CallRecorder(fault=known))
    assert excinfo.value is known
    harness.assert_phase291_zero_call()


def test_binding_digest_known_error_identity_preserved(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    known = ExternalPublicationRecoveryResumeIntentBindingError("binding_contract")
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as excinfo:
        harness.run(binding_digest_function=_CallRecorder(fault=known))
    assert excinfo.value is known
    harness.assert_phase291_zero_call()


def test_binding_loader_unexpected_error_sanitized(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(binding_loader=_CallRecorder(fault=RuntimeError("secret detail")))
    _assert_error(excinfo.value, "dependency_error")
    assert "secret detail" not in str(excinfo.value)
    harness.assert_phase291_zero_call()


def test_binding_digest_unexpected_error_sanitized(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(binding_digest_function=_CallRecorder(fault=RuntimeError("boom")))
    _assert_error(excinfo.value, "dependency_error")
    harness.assert_phase291_zero_call()


# --- derived authorization path / authorization --------------------------


def test_authorization_path_derived_from_binding_parent_and_digest(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    expected = harness.lineage.binding_path.parent / (
        f"{_AUTHORIZATION_FILENAME_PREFIX}{harness.lineage.binding_digest}"
        f"{_FILENAME_SUFFIX}"
    )
    recorded = harness.authorization_loader.first_positional
    assert type(recorded) is type(Path())
    assert recorded == expected
    assert recorded is not harness.lineage.authorization_path.parent
    assert recorded.parent == harness.lineage.binding_path.parent
    assert recorded.name == (
        f"{_AUTHORIZATION_FILENAME_PREFIX}{harness.lineage.binding_digest}"
        f"{_FILENAME_SUFFIX}"
    )


def test_authorization_loader_called_once_with_derived_identity(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    assert harness.authorization_loader.call_count == 1
    assert harness.authorization_digest.call_count == 1
    assert (
        harness.authorization_digest.first_positional is harness.lineage.authorization
    )
    assert harness.intent_loader.first_positional is harness.lineage.intent_path


@pytest.mark.parametrize("bad_authorization", ["authorization", None, 4, _binding()])
def test_authorization_non_exact_model_rejected(
    tmp_path: Path, bad_authorization: object
) -> None:
    harness = _Harness(tmp_path, authorization=bad_authorization)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "authorization_contract")
    assert harness.authorization_digest.call_count == 0
    harness.assert_phase291_zero_call()


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        (
            "schema_version",
            "external-publication-recovery-resume-start-authorization.v2",
        ),
        ("resume_intent_binding_sha256", "A" * 64),
        ("resume_preparation_sha256", "a" * 63),
        ("recovery_decision_sha256", None),
        ("operation_intent_sha256", ""),
        ("expected_operation_start_sha256", _StringChild("6" * 64)),
        ("publication_approval_sha256", "c" * 65),
        ("publication_plan_sha256", 7),
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
        harness.lineage.authorization,
        harness.lineage.authorization,
        **{field_name: bad_value},
    )
    harness.authorization_loader.result = forged
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "authorization_contract")
    harness.assert_phase291_zero_call()


def test_authorization_reconciliation_mismatch_requires_resume_source(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        harness.lineage.authorization,
        harness.lineage.authorization,
        recovery_kind="reconciliation_mismatch",
    )
    harness.authorization_loader.result = forged
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "authorization_contract")
    harness.assert_phase291_zero_call()


@pytest.mark.parametrize(
    "bad_digest",
    ["", "B" * 64, "b" * 63, _StringChild("b" * 64), None, 2],
)
def test_authorization_digest_malformed_rejected(
    tmp_path: Path, bad_digest: object
) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(authorization_digest_function=_CallRecorder(result=bad_digest))
    _assert_error(excinfo.value, "authorization_digest")
    harness.assert_phase291_zero_call()


def test_authorization_loader_known_error_identity_preserved(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    known = handoff_module.ExternalPublicationRecoveryResumeStartAuthorizationError(
        "load"
    )
    from ai_office.engine import (
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError,
    )

    known = ExternalPublicationRecoveryResumeStartAuthorizationLoadError()
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError
    ) as excinfo:
        harness.run(authorization_loader=_CallRecorder(fault=known))
    assert excinfo.value is known
    harness.assert_phase291_zero_call()


def test_authorization_loader_unexpected_error_sanitized(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(authorization_loader=_CallRecorder(fault=RuntimeError("boom")))
    _assert_error(excinfo.value, "dependency_error")
    harness.assert_phase291_zero_call()


def test_authorization_digest_unexpected_error_sanitized(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(
            authorization_digest_function=_CallRecorder(fault=RuntimeError("boom"))
        )
    _assert_error(excinfo.value, "dependency_error")
    harness.assert_phase291_zero_call()


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
        harness.lineage.authorization,
        harness.lineage.authorization,
        **{field_name: bad_value},
    )
    harness.authorization_loader.result = forged
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "authorization_lineage")
    assert harness.intent_loader.call_count == 0
    assert harness.intent_digest.call_count == 0
    harness.assert_phase291_zero_call()
    assert not harness.lineage.start_path.exists()


def test_binding_digest_change_breaks_authorization_lineage(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(binding_digest_function=_CallRecorder(result="7" * 64))
    _assert_error(excinfo.value, "authorization_lineage")
    harness.assert_phase291_zero_call()


# --- intent --------------------------------------------------------------


def test_intent_loaded_after_binding_and_authorization(tmp_path: Path) -> None:
    order: list[str] = []
    harness = _Harness(tmp_path)
    harness.binding_digest.delegate = None
    harness.binding_loader.delegate = lambda *args, **kwargs: (
        order.append("binding"),
        harness.lineage.binding,
    )[1]
    harness.authorization_loader.delegate = lambda *args, **kwargs: (
        order.append("authorization"),
        harness.lineage.authorization,
    )[1]
    harness.intent_loader.delegate = lambda *args, **kwargs: (
        order.append("intent"),
        harness.lineage.intent,
    )[1]
    harness.phase291.delegate = lambda *args, **kwargs: (
        order.append("phase291"),
        harness.phase291_result,
    )[1]
    harness.run()
    assert order == ["binding", "authorization", "intent", "phase291"]


def test_intent_loader_called_once_with_exact_path_identity(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    assert harness.intent_loader.call_count == 1
    assert harness.intent_loader.first_positional is harness.lineage.intent_path
    assert harness.intent_digest.call_count == 1
    assert harness.intent_digest.first_positional is harness.lineage.intent


@pytest.mark.parametrize("bad_intent", ["intent", None, 6, _binding()])
def test_intent_non_exact_model_rejected(tmp_path: Path, bad_intent: object) -> None:
    harness = _Harness(tmp_path, intent=bad_intent)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "intent_contract")
    assert harness.intent_digest.call_count == 0
    harness.assert_phase291_zero_call()


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("schema_version", "external-publication-operation-intent.v2"),
        ("publication_approval_sha256", "C" * 64),
        ("publication_approval_sha256", ""),
        ("publication_plan_sha256", _StringChild("d" * 64)),
        ("operation", "fresh"),
    ],
)
def test_intent_local_validation(
    tmp_path: Path, field_name: str, bad_value: object
) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        harness.lineage.intent, harness.lineage.intent, **{field_name: bad_value}
    )
    harness.intent_loader.result = forged
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "intent_contract")
    harness.assert_phase291_zero_call()


@pytest.mark.parametrize(
    "bad_digest",
    ["", "E" * 64, "e" * 63, _StringChild("e" * 64), None, 3],
)
def test_intent_digest_malformed_rejected(tmp_path: Path, bad_digest: object) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(intent_digest_function=_CallRecorder(result=bad_digest))
    _assert_error(excinfo.value, "intent_digest")
    harness.assert_phase291_zero_call()


@pytest.mark.parametrize(
    ("kind", "field_name"),
    [
        ("digest", "operation_intent_sha256"),
        ("approval", "publication_approval_sha256"),
        ("plan", "publication_plan_sha256"),
    ],
)
def test_intent_lineage_mismatch_rejected_before_phase291(
    tmp_path: Path, kind: str, field_name: str
) -> None:
    """Binding and authorization must agree, and both must bind the intent."""
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
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run()
    _assert_error(excinfo.value, "intent_lineage")
    harness.assert_phase291_zero_call()
    assert not lineage.start_path.exists()


def test_intent_loader_known_error_identity_preserved(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    known = ExternalPublicationOperationIntentError("intent_contract")
    with pytest.raises(ExternalPublicationOperationIntentError) as excinfo:
        harness.run(intent_loader=_CallRecorder(fault=known))
    assert excinfo.value is known
    harness.assert_phase291_zero_call()


def test_intent_loader_unexpected_error_sanitized(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(intent_loader=_CallRecorder(fault=RuntimeError("boom")))
    _assert_error(excinfo.value, "dependency_error")
    harness.assert_phase291_zero_call()


def test_intent_digest_unexpected_error_sanitized(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(intent_digest_function=_CallRecorder(fault=RuntimeError("boom")))
    _assert_error(excinfo.value, "dependency_error")
    harness.assert_phase291_zero_call()


# --- expected start ------------------------------------------------------


def test_expected_start_digest_uses_constructed_identity(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    constructed = harness.start_digest.calls[0][0][0]
    assert type(constructed) is ExternalPublicationOperationStart
    assert constructed == harness.lineage.expected_start
    assert constructed.operation == "resume"
    assert constructed.state == "started"
    assert constructed.schema_version == _START_SCHEMA
    assert constructed.operation_intent_sha256 == harness.lineage.intent_digest
    assert constructed.publication_approval_sha256 == harness.lineage.approval_digest
    assert constructed.publication_plan_sha256 == harness.lineage.plan_digest


@pytest.mark.parametrize(
    "bad_digest",
    ["", "F" * 64, "f" * 63, _StringChild("f" * 64), None, 8],
)
def test_expected_start_digest_malformed_rejected(
    tmp_path: Path, bad_digest: object
) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(start_digest_function=_CallRecorder(result=bad_digest))
    _assert_error(excinfo.value, "start_digest")
    harness.assert_phase291_zero_call()


def test_expected_start_digest_must_equal_authorization(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    mismatch = "f" * 64
    assert mismatch != harness.lineage.expected_start_digest
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(start_digest_function=_CallRecorder(result=mismatch))
    _assert_error(excinfo.value, "start_digest")
    harness.assert_phase291_zero_call()
    assert not harness.lineage.start_path.exists()


def test_no_start_marker_created_or_loaded_before_phase291(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    order: list[str] = []
    harness.start_digest.delegate = lambda start: (
        order.append("start_digest"),
        external_publication_operation_start_digest(start),
    )[1]
    harness.phase291.delegate = lambda **kwargs: (
        order.append("phase291"),
        harness.phase291_result,
    )[1]
    harness.run()
    assert not harness.lineage.start_path.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == []
    assert order == ["start_digest", "phase291"]
    assert harness.start_digest.call_count == 1
    assert order.count("phase291") == 1


# --- request lineage -----------------------------------------------------


def test_request_approval_digest_uses_exact_approval_identity(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    assert harness.approval_digest.call_count == 1
    assert harness.approval_digest.first_positional is harness.lineage.request.approval


@pytest.mark.parametrize(
    "bad_digest",
    ["", "C" * 64, "c" * 63, _StringChild("c" * 64), None, 9],
)
def test_request_approval_digest_malformed_rejected(
    tmp_path: Path, bad_digest: object
) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(approval_digest_function=_CallRecorder(result=bad_digest))
    _assert_error(excinfo.value, "request_lineage")
    harness.assert_phase291_zero_call()


def test_request_approval_digest_must_match_lineage(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(approval_digest_function=_CallRecorder(result="9" * 64))
    _assert_error(excinfo.value, "request_lineage")
    harness.assert_phase291_zero_call()
    assert not harness.lineage.start_path.exists()


def test_request_plan_digest_must_match_lineage(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    other = _approval(publication_plan_sha256="9" * 64)
    forged_request = _forged_instance(
        harness.lineage.request, harness.lineage.request, approval=other
    )
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(request=forged_request)
    _assert_error(excinfo.value, "request_lineage")
    harness.assert_phase291_zero_call()
    assert not harness.lineage.start_path.exists()


def test_request_approval_digest_known_error_identity_preserved(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    known = ExternalPublicationError("approval")
    with pytest.raises(ExternalPublicationError) as excinfo:
        harness.run(approval_digest_function=_CallRecorder(fault=known))
    assert excinfo.value is known
    harness.assert_phase291_zero_call()


def test_request_approval_digest_unexpected_error_sanitized(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(approval_digest_function=_CallRecorder(fault=RuntimeError("boom")))
    _assert_error(excinfo.value, "dependency_error")
    harness.assert_phase291_zero_call()


# --- canonical start path ------------------------------------------------


def test_start_path_derived_from_binding_parent_and_authorization_digest(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    _, kwargs = harness.phase291.calls[0]
    recorded = kwargs["start_path"]
    expected = harness.lineage.binding_path.parent / (
        f"{_START_FILENAME_PREFIX}{harness.lineage.authorization_digest}"
        f"{_FILENAME_SUFFIX}"
    )
    assert recorded == expected
    assert recorded.parent == harness.lineage.binding_path.parent
    assert recorded.name == (
        f"{_START_FILENAME_PREFIX}{harness.lineage.authorization_digest}"
        f"{_FILENAME_SUFFIX}"
    )


def test_start_path_deterministic_per_authorization_digest(tmp_path: Path) -> None:
    first = _Harness(tmp_path)
    second = _Harness(tmp_path)
    second.authorization_digest.result = "9" * 64
    first.run()
    second.run()
    first_path = first.phase291.calls[0][1]["start_path"]
    second_path = second.phase291.calls[0][1]["start_path"]
    assert first_path != second_path
    assert first_path.parent == second_path.parent


# --- phase 291 call ------------------------------------------------------


def test_phase291_called_exactly_once(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    assert harness.phase291.call_count == 1
    assert harness.binding_loader.call_count == 1
    assert harness.authorization_loader.call_count == 1
    assert harness.intent_loader.call_count == 1


def test_phase291_exact_kwargs_and_identities(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.run()
    args, kwargs = harness.phase291.calls[0]
    assert args == ()
    assert set(kwargs) == {"intent_path", "start_path", "request"}
    assert kwargs["intent_path"] is harness.lineage.intent_path
    assert kwargs["request"] is harness.lineage.request
    assert "phase290_function" not in kwargs
    assert "phase288_function" not in kwargs
    assert kwargs["start_path"] != harness.lineage.intent_path


def test_phase291_not_retried_on_known_error(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    known = handoff_module.ExternalPublicationOperationStartHandoffError(
        "start_contract"
    )
    with pytest.raises(
        handoff_module.ExternalPublicationOperationStartHandoffError
    ) as excinfo:
        harness.run(phase291_function=_CallRecorder(fault=known))
    assert excinfo.value is known
    assert harness.phase291.call_count == 0


def test_phase291_known_error_identity_preserved(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    known = handoff_module.ExternalPublicationOperationStartHandoffError(
        "request_contract"
    )
    recorder = _CallRecorder(fault=known)
    with pytest.raises(
        handoff_module.ExternalPublicationOperationStartHandoffError
    ) as excinfo:
        harness.run(phase291_function=recorder)
    assert excinfo.value is known
    assert recorder.call_count == 1


def test_phase291_lower_known_error_identity_preserved(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    known = ExternalPublicationOperationStartError("acquisition")
    recorder = _CallRecorder(fault=known)
    with pytest.raises(ExternalPublicationOperationStartError) as excinfo:
        harness.run(phase291_function=recorder)
    assert excinfo.value is known
    assert recorder.call_count == 1


def test_phase291_unexpected_error_sanitized(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    recorder = _CallRecorder(fault=RuntimeError("secret detail"))
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(phase291_function=recorder)
    _assert_error(excinfo.value, "dependency_error")
    assert "secret detail" not in str(excinfo.value)
    assert recorder.call_count == 1


# --- result --------------------------------------------------------------


def test_already_acquired_accepted_and_identity_returned(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    result = harness.run()
    assert result is harness.phase291_result
    assert type(result) is ExternalPublicationOperationStartAcquisition
    assert result.status == "already_acquired"


def test_acquired_acquisition_rejected(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    acquired = ExternalPublicationOperationStartAcquisition(
        status="acquired",
        start=harness.lineage.expected_start,  # type: ignore[arg-type]
    )
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(phase291_result=acquired)
    _assert_error(excinfo.value, "result_contract")
    assert harness.phase291.call_count == 1


def test_acquisition_status_not_string_rejected(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    forged = _forged_instance(
        harness.phase291_result,
        harness.phase291_result,
        status=_StringChild("already_acquired"),
    )
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(phase291_result=forged)
    _assert_error(excinfo.value, "result_contract")


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("operation_intent_sha256", "9" * 64),
        ("publication_approval_sha256", "9" * 64),
        ("publication_plan_sha256", "9" * 64),
        ("operation", "fresh"),
        ("state", "acquired"),
        ("schema_version", "external-publication-operation-start.v2"),
    ],
)
def test_result_start_revalidated(
    tmp_path: Path, field_name: str, bad_value: object
) -> None:
    harness = _Harness(tmp_path)
    forged_start = _forged_instance(
        harness.lineage.expected_start, harness.lineage.expected_start, **{}
    )
    object.__setattr__(forged_start, field_name, bad_value)
    result = _forged_instance(
        harness.phase291_result,
        harness.phase291_result,
        start=forged_start,
    )
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(phase291_result=result)
    _assert_error(excinfo.value, "result_contract")


def test_result_start_digest_mismatch_rejected(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    other = "f" * 64
    assert other != harness.lineage.expected_start_digest
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(start_digest_function=_CallRecorder(result=other))
    _assert_error(excinfo.value, "start_digest")
    assert not harness.lineage.start_path.exists()


def test_reconciliation_accepted_and_identity_returned(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    reconciliation = _reconciliation()
    result = harness.run(phase291_result=reconciliation)
    assert result is reconciliation
    assert type(result) is ExternalPublicationExecutionReconciliation


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
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(phase291_result=forged)
    _assert_error(excinfo.value, "result_contract")


@pytest.mark.parametrize(
    "bad_result",
    [
        None,
        "result",
        7,
        object(),
        ExternalPublicationOperationStartAcquisition(
            status="already_acquired",
            start=_start(),  # type: ignore[arg-type]
        ),
    ],
)
def test_arbitrary_result_rejected(tmp_path: Path, bad_result: object) -> None:
    harness = _Harness(tmp_path)
    if type(bad_result) is ExternalPublicationOperationStartAcquisition:
        # An exact acquisition with a start that does not match the expected lineage.
        bad_result = ExternalPublicationOperationStartAcquisition(
            status="already_acquired", start=_start(operation_intent_sha256="9" * 64)
        )
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(phase291_result=bad_result)
    _assert_error(excinfo.value, "result_contract")


def test_result_lookalike_rejected(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)

    class _Lookalike:
        status = "already_acquired"
        start = harness.lineage.expected_start

    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(phase291_result=_Lookalike())
    _assert_error(excinfo.value, "result_contract")


def test_result_acquisition_subclass_rejected(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)

    class _Subclass(ExternalPublicationOperationStartAcquisition):
        pass

    forged = object.__new__(_Subclass)
    object.__setattr__(forged, "status", "already_acquired")
    object.__setattr__(forged, "start", harness.lineage.expected_start)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        harness.run(phase291_result=forged)
    _assert_error(excinfo.value, "result_contract")


def test_no_second_phase291_call_after_invalid_result(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError):
        harness.run(phase291_result=object())
    assert harness.phase291.call_count == 1


# --- source audit --------------------------------------------------------


def _module_tree() -> ast.Module:
    return ast.parse(_SOURCE)


def _imported_names() -> tuple[set[str], set[str]]:
    """Return the relative module set and every imported name."""
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


def test_source_audit_no_stdlib_ambient_imports() -> None:
    _, names = _imported_names()
    assert names & {"os", "sys", "socket", "subprocess", "time", "uuid"} == set()


def test_source_audit_forbidden_imports_and_calls() -> None:
    _, names = _imported_names()
    called = _called_names()
    forbidden = {
        "acquire_external_publication_operation_start",
        "run_external_publication_operation",
        "execute_and_persist_approved_external_publication",
        "resume_external_publication_reconciliation_closure",
        "authorize_and_persist_external_publication_recovery_resume_start",
        "materialize_and_bind_external_publication_recovery_resume_intent",
        "prepare_and_persist_external_publication_recovery_resume_lineage",
        "decide_and_persist_external_publication_recovery",
        "os",
        "time",
        "random",
        "uuid",
        "socket",
        "subprocess",
        "datetime",
    }
    assert names & forbidden == set()
    assert called & forbidden == set()
    assert called & {"open", "unlink", "rename", "replace", "rmdir", "remove"} == set()


def test_source_audit_phase291_is_the_only_public_boundary() -> None:
    _, names = _imported_names()
    assert "run_external_publication_operation_start_handoff" in names
    called = _called_names()
    assert "run_external_publication_operation_start_handoff" not in called
    assert "phase291_function" in _SOURCE
    assert "phase290_function" not in _SOURCE
    assert "phase288_function" not in _SOURCE


@pytest.mark.parametrize(
    "token",
    [
        "os.environ",
        "os.getenv",
        "Path.resolve",
        ".resolve(",
        "realpath",
        "normpath",
        "abspath",
        "samefile",
        "readlink",
        "uuid4",
        "token_hex",
        "time(",
        "datetime",
    ],
)
def test_source_audit_no_ambient_or_normalizing_apis(token: str) -> None:
    assert token not in _SOURCE


def test_no_cli_change_and_no_phase297_command() -> None:
    from typer.testing import CliRunner

    from ai_office.cli import app

    runner = CliRunner()
    root = runner.invoke(app, ["--help"])
    assert root.exit_code == 0
    assert "resume_start_handoff" not in root.output.lower()

    workflows = runner.invoke(app, ["workflows", "--help"])
    assert workflows.exit_code == 0
    assert "resume_start_handoff" not in workflows.output.lower()
    assert "phase297" not in workflows.output.lower().replace(" ", "")

    cli_source = Path("src/ai_office/cli.py").read_text(encoding="utf-8")
    assert "resume_start_handoff" not in cli_source
    assert "phase297" not in cli_source.lower().replace(" ", "")


# --- integration regressions --------------------------------------------


def _plan(regeneration_id: str = "regen-297") -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id=regeneration_id,
        reconciliation_evidence_sha256="d" * 64,
        receipt_sha256="e" * 64,
        business_output_sha256="f" * 64,
        output_byte_length=11,
        provider="future-provider",
        publication_target_sha256="1" * 64,
    )


def _lifecycle(start_digest: str, approval_digest: str, plan_digest: str):
    return ExternalPublicationOperationLifecycleOutcome(
        schema_version=_LIFECYCLE_SCHEMA,  # type: ignore[arg-type]
        operation_start_sha256=start_digest,
        publication_approval_sha256=approval_digest,
        publication_plan_sha256=plan_digest,
        operation="fresh",
        state="recovery_required",
        result_kind="none",
        result_sha256=None,
    )


def _seed_recovery_lineage(
    root: Path, *, regeneration_id: str = "regen-297"
) -> tuple[object, Path, Path, Path]:
    """Build one real Phase 294 -> 295 -> 296 lineage with no provider call."""
    approval = approve_external_publication(
        _plan(regeneration_id),
        approved_by=f"human-reviewer-297-{regeneration_id}",
        approval_id=f"approval-297-{regeneration_id}",
    )
    intent = build_external_publication_operation_intent(approval, operation="fresh")
    lineage_intent_path = root / "lineage-intent.json"
    start_path = root / "start.json"
    persist_external_publication_operation_intent(lineage_intent_path, intent)
    acquire_external_publication_operation_start(
        intent_path=lineage_intent_path, start_path=start_path
    )
    start = load_external_publication_operation_start(start_path)
    start_digest = external_publication_operation_start_digest(start)
    lifecycle_path = root / "lifecycle.json"
    persist_external_publication_operation_lifecycle_outcome(
        lifecycle_path,
        _lifecycle(
            start_digest,
            start.publication_approval_sha256,
            start.publication_plan_sha256,
        ),
    )
    decision_path = root / "decision.json"
    decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        recovery_decision_path=decision_path,
        decision="authorize_resume_preparation",  # type: ignore[arg-type]
        decided_by=f"operator-297-{regeneration_id}",
        decision_id=f"decision-297-{regeneration_id}",
    )
    preparation_path = root / "preparation.json"
    prepare_and_persist_external_publication_recovery_resume_lineage(
        recovery_decision_path=decision_path,
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        resume_preparation_path=preparation_path,
    )
    binding_path = root / "recovery-binding.json"
    bound_intent_path = root / "recovery-intent.json"
    materialize_and_bind_external_publication_recovery_resume_intent(
        resume_preparation_path=preparation_path,
        resume_intent_binding_path=binding_path,
        resume_intent_path=bound_intent_path,
    )
    binding = load_external_publication_recovery_resume_intent_binding(binding_path)
    binding_digest = external_publication_recovery_resume_intent_binding_digest(binding)
    authorization_path = binding_path.parent / (
        f"{_AUTHORIZATION_FILENAME_PREFIX}{binding_digest}{_FILENAME_SUFFIX}"
    )
    authorize_and_persist_external_publication_recovery_resume_start(
        resume_intent_binding_path=binding_path,
        resume_intent_path=bound_intent_path,
        resume_start_authorization_path=authorization_path,
    )
    return approval, binding_path, bound_intent_path, authorization_path


def _canonical_start_path(binding_path: Path) -> Path:
    authorization = load_external_publication_recovery_resume_start_authorization(
        binding_path.parent
        / (
            f"{_AUTHORIZATION_FILENAME_PREFIX}"
            f"{external_publication_recovery_resume_intent_binding_digest(load_external_publication_recovery_resume_intent_binding(binding_path))}"
            f"{_FILENAME_SUFFIX}"
        )
    )
    digest = external_publication_recovery_resume_start_authorization_digest(
        authorization
    )
    return binding_path.parent / (f"{_START_FILENAME_PREFIX}{digest}{_FILENAME_SUFFIX}")


def test_real_already_acquired_recovery_stop(tmp_path: Path) -> None:
    """A pre-existing exact marker must return the stop result, not authority."""
    approval, binding_path, intent_path, _authorization_path = _seed_recovery_lineage(
        tmp_path
    )
    start_path = _canonical_start_path(binding_path)
    assert not start_path.exists()

    first = acquire_external_publication_operation_start(
        intent_path=intent_path, start_path=start_path
    )
    assert first.status == "acquired"
    marker_before = start_path.read_bytes()

    request = ExternalPublicationResumeOperationRequest(
        ledger_directory=tmp_path / "ledger",
        approval=approval,  # type: ignore[arg-type]
        execution_evidence_path=tmp_path / "execution-evidence.json",
        execution_reconciliation_evidence_path=(
            tmp_path / "execution-reconciliation-evidence.json"
        ),
    )
    result = run_external_publication_recovery_resume_start_handoff(
        resume_intent_binding_path=binding_path,
        resume_intent_path=intent_path,
        request=request,
    )
    assert type(result) is ExternalPublicationOperationStartAcquisition
    assert result.status == "already_acquired"
    assert start_path.read_bytes() == marker_before
    assert start_path.exists()
    assert not (tmp_path / "execution-reconciliation-evidence.json").exists()


def test_real_same_invocation_acquired_resume_handoff(tmp_path: Path) -> None:
    """Phase 290 acquires inside Phase 291 and Phase 288 hands back reconciliation."""
    approval, binding_path, intent_path, _authorization_path = _seed_recovery_lineage(
        tmp_path
    )
    start_path = _canonical_start_path(binding_path)
    assert not start_path.exists()

    reconciliation = _reconciliation()
    phase288 = _CallRecorder(result=reconciliation)

    def _phase291(*, intent_path: Path, start_path: Path, request: object) -> object:
        return run_external_publication_operation_start_handoff(
            intent_path=intent_path,
            start_path=start_path,
            request=request,  # type: ignore[arg-type]
            phase288_function=phase288,
        )

    request = ExternalPublicationResumeOperationRequest(
        ledger_directory=tmp_path / "ledger",
        approval=approval,  # type: ignore[arg-type]
        execution_evidence_path=tmp_path / "execution-evidence.json",
        execution_reconciliation_evidence_path=(
            tmp_path / "execution-reconciliation-evidence.json"
        ),
    )
    result = run_external_publication_recovery_resume_start_handoff(
        resume_intent_binding_path=binding_path,
        resume_intent_path=intent_path,
        request=request,
        phase291_function=_phase291,
    )
    assert result is reconciliation
    assert type(result) is ExternalPublicationExecutionReconciliation
    assert start_path.exists()
    assert phase288.call_count == 1
    assert phase288.calls[0][0][0] is request
    marker = load_external_publication_operation_start(start_path)
    assert marker.operation == "resume"


def test_real_lineage_mismatch_stops_before_phase291(tmp_path: Path) -> None:
    approval, binding_path, intent_path, _authorization_path = _seed_recovery_lineage(
        tmp_path
    )
    start_path = _canonical_start_path(binding_path)
    other_plan = approve_external_publication(
        _plan("regen-297-other"),
        approved_by="human-reviewer-297-other",
        approval_id="approval-297-other",
    )
    assert other_plan.publication_plan_sha256 != approval.publication_plan_sha256  # type: ignore[union-attr]
    request = ExternalPublicationResumeOperationRequest(
        ledger_directory=tmp_path / "ledger",
        approval=other_plan,  # type: ignore[arg-type]
        execution_evidence_path=tmp_path / "execution-evidence.json",
        execution_reconciliation_evidence_path=(
            tmp_path / "execution-reconciliation-evidence.json"
        ),
    )
    calls: list[object] = []

    def _phase291(**kwargs: object) -> object:
        calls.append(kwargs)
        raise AssertionError("Phase 291 must not be called")

    with pytest.raises(ExternalPublicationRecoveryResumeStartHandoffError) as excinfo:
        run_external_publication_recovery_resume_start_handoff(
            resume_intent_binding_path=binding_path,
            resume_intent_path=intent_path,
            request=request,
            phase291_function=_phase291,
        )
    _assert_error(excinfo.value, "request_lineage")
    assert calls == []
    assert not start_path.exists()


def test_real_default_dependencies_are_public_helpers(tmp_path: Path) -> None:
    defaults = inspect.signature(
        run_external_publication_recovery_resume_start_handoff
    ).parameters
    assert defaults["binding_loader"].default.__module__ == (
        "ai_office.engine.external_publication_recovery_resume_intent_binding"
    )
    assert defaults["authorization_loader"].default.__module__ == (
        "ai_office.engine.external_publication_recovery_resume_start_authorization"
    )
    assert defaults["intent_loader"].default.__module__ == (
        "ai_office.engine.external_publication_operation_intent"
    )
    assert defaults["phase291_function"].default.__module__ == (
        "ai_office.engine.external_publication_operation_start_handoff"
    )
