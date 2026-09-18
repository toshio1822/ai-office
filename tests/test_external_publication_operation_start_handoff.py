"""Focused provider-free tests for the Phase 291 acquired-start handoff."""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import ai_office.engine.external_publication_operation_start_handoff as handoff_module
from ai_office.engine import (
    ExternalPublicationApproval,
    ExternalPublicationApprovalError,
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionResult,
    ExternalPublicationFreshOperationRequest,
    ExternalPublicationOperationError,
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartAcquisition,
    ExternalPublicationOperationStartError,
    ExternalPublicationOperationStartHandoffCompatibilityError,
    ExternalPublicationOperationStartHandoffError,
    ExternalPublicationOperationStartHandoffFailureDetail,
    ExternalPublicationPlan,
    ExternalPublicationResumeOperationRequest,
    ExternalPublicationTarget,
    acquire_external_publication_operation_start,
    approve_external_publication,
    build_external_publication_operation_intent,
    external_publication_approval_digest,
    external_publication_plan_digest,
    persist_external_publication_operation_intent,
    run_external_publication_operation,
    run_external_publication_operation_start_handoff,
)

_SCHEMA = "external-publication-operation-start.v1"
_INTENT_SCHEMA = "external-publication-operation-intent.v1"
_MESSAGE = "external publication operation start handoff is blocked"
_SOURCE = Path(handoff_module.__file__).read_text(encoding="utf-8")


class _StringChild(str):
    pass


def _forged_instance(cls: type[object], source: object) -> object:
    value = object.__new__(cls)
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        object.__setattr__(value, field.name, getattr(source, field.name))
    return value


def _assert_handoff_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationOperationStartHandoffCompatibilityError
    assert isinstance(error, ExternalPublicationOperationStartHandoffError)
    assert isinstance(error, ValueError)
    assert str(error) == _MESSAGE
    assert type(error.detail) is ExternalPublicationOperationStartHandoffFailureDetail
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _target() -> ExternalPublicationTarget:
    return ExternalPublicationTarget(
        schema_version="external-publication-target.v1",
        provider="future-provider",
        destination_id="destination-291",
    )


def _plan() -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id="regen-291",
        reconciliation_evidence_sha256="d" * 64,
        receipt_sha256="e" * 64,
        business_output_sha256="f" * 64,
        output_byte_length=11,
        provider="future-provider",
        publication_target_sha256="1" * 64,
    )


def _approval(plan: ExternalPublicationPlan) -> ExternalPublicationApproval:
    return approve_external_publication(
        plan,
        approved_by="human-reviewer-291",
        approval_id="approval-291",
    )


def _transport(target: ExternalPublicationTarget, payload: bytes) -> object:
    raise AssertionError("Phase 291 must never call a transport")


def _fresh_request(
    root: Path,
    *,
    plan: ExternalPublicationPlan | None = None,
    approval: ExternalPublicationApproval | None = None,
    target: ExternalPublicationTarget | None = None,
    transport: object = _transport,
) -> ExternalPublicationFreshOperationRequest:
    plan = plan if plan is not None else _plan()
    approval = approval if approval is not None else _approval(plan)
    return ExternalPublicationFreshOperationRequest(
        execution_evidence_path=root / "execution-evidence.json",
        plan_reconciliation_evidence_path=root / "reconciliation-evidence.json",
        output_path=root / "output.bin",
        ledger_directory=root / "ledger",
        plan=plan,
        approval=approval,
        target=target if target is not None else _target(),
        transport=transport,  # type: ignore[arg-type]
    )


def _resume_request(
    root: Path,
    *,
    approval: ExternalPublicationApproval | None = None,
) -> ExternalPublicationResumeOperationRequest:
    approval = approval if approval is not None else _approval(_plan())
    return ExternalPublicationResumeOperationRequest(
        ledger_directory=root / "ledger",
        approval=approval,
        execution_evidence_path=root / "execution-evidence.json",
        execution_reconciliation_evidence_path=root / "reconciliation.json",
    )


def _acquisition(
    status: str,
    operation: str,
    *,
    approval_digest: str,
    plan_digest: str,
    intent_digest: str = "a" * 64,
    schema_version: str = _SCHEMA,
    start_state: str = "started",
) -> ExternalPublicationOperationStartAcquisition:
    return ExternalPublicationOperationStartAcquisition(
        status=status,  # type: ignore[arg-type]
        start=ExternalPublicationOperationStart(
            schema_version=schema_version,  # type: ignore[arg-type]
            operation_intent_sha256=intent_digest,
            publication_approval_sha256=approval_digest,
            publication_plan_sha256=plan_digest,
            operation=operation,  # type: ignore[arg-type]
            state=start_state,  # type: ignore[arg-type]
        ),
    )


def _fresh_result() -> ExternalPublicationExecutionResult:
    return ExternalPublicationExecutionResult(
        schema_version="external-publication-execution-result.v1",
        regeneration_id="regen-291",
        publication_attempt_claim_sha256="a" * 64,
        publication_plan_sha256="b" * 64,
        publication_approval_sha256="c" * 64,
        business_output_sha256="d" * 64,
        output_byte_length=11,
        provider="future-provider",
        publication_target_sha256="e" * 64,
        publication_id="publication-291",
        status="published",
    )


def _resume_result() -> ExternalPublicationExecutionReconciliation:
    return ExternalPublicationExecutionReconciliation(
        schema_version="external-publication-execution-reconciliation.v1",
        claim_sha256="a" * 64,
        execution_evidence_sha256="b" * 64,
        status="matched",
        mismatched_fields=(),
    )


class _Phase290Recorder:
    def __init__(self, acquisition: object) -> None:
        self.acquisition = acquisition
        self.calls: list[tuple[object, object]] = []

    def __call__(self, *, intent_path: object, start_path: object) -> object:
        self.calls.append((intent_path, start_path))
        if isinstance(self.acquisition, BaseException):
            raise self.acquisition
        return self.acquisition


class _Phase288Recorder:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[object] = []

    def __call__(self, request: object) -> object:
        self.calls.append(request)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


# --- public surface -------------------------------------------------------


def test_public_exports_and_error_family() -> None:
    assert run_external_publication_operation_start_handoff.__name__ == (
        "run_external_publication_operation_start_handoff"
    )
    assert issubclass(ExternalPublicationOperationStartHandoffError, ValueError)
    assert issubclass(
        ExternalPublicationOperationStartHandoffCompatibilityError,
        ExternalPublicationOperationStartHandoffError,
    )
    error = ExternalPublicationOperationStartHandoffError()
    assert str(error) == _MESSAGE
    assert error.detail.classification == "dependency_error"
    assert error.__cause__ is None


def test_signature_defaults_and_no_acquisition_argument() -> None:
    signature = inspect.signature(run_external_publication_operation_start_handoff)
    parameters = signature.parameters
    assert list(parameters) == [
        "intent_path",
        "start_path",
        "request",
        "phase290_function",
        "phase288_function",
    ]
    for name in ("intent_path", "start_path", "request"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["phase290_function"].default is (
        acquire_external_publication_operation_start
    )
    assert parameters["phase288_function"].default is (
        run_external_publication_operation
    )
    assert not any("acquisition" in name for name in parameters), (
        "acquisition must not be accepted as input"
    )
    assert "ExternalPublicationOperationStartAcquisition" not in str(
        parameters["request"].annotation
    )


def test_function_rejects_acquisition_keyword(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        run_external_publication_operation_start_handoff(  # type: ignore[call-arg]
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=_fresh_request(tmp_path),
            acquisition=object(),
        )


# --- request preflight ---------------------------------------------------


@pytest.mark.parametrize("bad", ["str", 1, None, _StringChild("/tmp/x")])
def test_non_path_intent_and_start_rejected(bad: object, tmp_path: Path) -> None:
    phase290 = _Phase290Recorder(
        _acquisition(
            "acquired", "fresh", approval_digest="c" * 64, plan_digest="b" * 64
        )
    )
    with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=bad,  # type: ignore[arg-type]
            start_path=tmp_path / "start.json",
            request=_fresh_request(tmp_path),
            phase290_function=phase290,
        )
    _assert_handoff_error(info.value, "path_type")
    assert phase290.calls == []


@pytest.mark.parametrize(
    "candidate",
    [object(), {"request": "fresh"}, None, "fresh", 3],
)
def test_request_runtime_type_required(candidate: object, tmp_path: Path) -> None:
    phase290 = _Phase290Recorder(
        _acquisition(
            "acquired", "fresh", approval_digest="c" * 64, plan_digest="b" * 64
        )
    )
    with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=candidate,
            phase290_function=phase290,
        )
    _assert_handoff_error(info.value, "request_contract")
    assert phase290.calls == []


def test_request_subclass_rejected(tmp_path: Path) -> None:
    class FreshChild(ExternalPublicationFreshOperationRequest):
        pass

    base = _fresh_request(tmp_path)
    child = FreshChild(
        execution_evidence_path=base.execution_evidence_path,
        plan_reconciliation_evidence_path=base.plan_reconciliation_evidence_path,
        output_path=base.output_path,
        ledger_directory=base.ledger_directory,
        plan=base.plan,
        approval=base.approval,
        target=base.target,
        transport=base.transport,
    )
    phase290 = _Phase290Recorder(
        _acquisition(
            "acquired", "fresh", approval_digest="c" * 64, plan_digest="b" * 64
        )
    )
    with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=child,
            phase290_function=phase290,
        )
    _assert_handoff_error(info.value, "request_contract")
    assert phase290.calls == []


@pytest.mark.parametrize("bad", ["str", 2, None])
def test_unexpected_unexpected_dependency_rejected(bad: object, tmp_path: Path) -> None:
    with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=_fresh_request(tmp_path),
            phase290_function=bad,
        )
    _assert_handoff_error(info.value, "configuration")


def test_fresh_request_path_fields_require_exact_path(tmp_path: Path) -> None:
    base = _fresh_request(tmp_path)
    for field in (
        "execution_evidence_path",
        "plan_reconciliation_evidence_path",
        "output_path",
        "ledger_directory",
    ):
        forged = dataclasses.replace(base, **{field: _StringChild("/tmp/forged")})
        phase290 = _Phase290Recorder(
            _acquisition(
                "acquired", "fresh", approval_digest="c" * 64, plan_digest="b" * 64
            )
        )
        with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
            run_external_publication_operation_start_handoff(
                intent_path=tmp_path / "intent.json",
                start_path=tmp_path / "start.json",
                request=forged,
                phase290_function=phase290,
            )
        _assert_handoff_error(info.value, "path_type")
        assert phase290.calls == []


def test_fresh_request_plan_approval_target_and_transport_required(
    tmp_path: Path,
) -> None:
    base = _fresh_request(tmp_path)
    cases = (
        ("plan", object(), "request_contract"),
        ("approval", object(), "request_contract"),
        ("target", object(), "request_contract"),
        ("transport", "not-callable", "request_contract"),
    )
    for field, value, classification in cases:
        forged = dataclasses.replace(base, **{field: value})
        phase290 = _Phase290Recorder(
            _acquisition(
                "acquired", "fresh", approval_digest="c" * 64, plan_digest="b" * 64
            )
        )
        with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
            run_external_publication_operation_start_handoff(
                intent_path=tmp_path / "intent.json",
                start_path=tmp_path / "start.json",
                request=forged,
                phase290_function=phase290,
            )
        _assert_handoff_error(info.value, classification)
        assert phase290.calls == []


def test_fresh_preflight_rejects_unbound_approval_by_identity(
    tmp_path: Path,
) -> None:
    plan = _plan()
    other_plan = dataclasses.replace(plan, regeneration_id="regen-other")
    approval = _approval(plan)
    request = _fresh_request(
        tmp_path, plan=other_plan, approval=approval, target=_target()
    )
    phase290 = _Phase290Recorder(_acquire_ok("fresh"))
    with pytest.raises(ExternalPublicationApprovalError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=request,
            phase290_function=phase290,
        )
    assert type(info.value) is ExternalPublicationApprovalError
    assert phase290.calls == []


def test_fresh_preflight_calls_approval_digest_once_with_exact_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan()
    approval = _approval(plan)
    request = _fresh_request(tmp_path, plan=plan, approval=approval)
    seen: list[object] = []

    def counting_digest(value: object) -> object:
        seen.append(value)
        return external_publication_approval_digest(value)  # type: ignore[arg-type]

    monkeypatch.setattr(
        handoff_module, "external_publication_approval_digest", counting_digest
    )
    result = _fresh_result()
    phase288 = _Phase288Recorder(result)
    acquisition = _acquisition(
        "acquired",
        "fresh",
        approval_digest=external_publication_approval_digest(approval),
        plan_digest=external_publication_plan_digest(plan),
    )
    returned = run_external_publication_operation_start_handoff(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        request=request,
        phase290_function=_Phase290Recorder(acquisition),
        phase288_function=phase288,
    )
    assert seen == [approval]
    assert returned is result
    assert phase288.calls == [request]


def test_resume_preflight_paths_and_approval(tmp_path: Path) -> None:
    base = _resume_request(tmp_path)
    for field in (
        "ledger_directory",
        "execution_evidence_path",
        "execution_reconciliation_evidence_path",
    ):
        forged = dataclasses.replace(base, **{field: _StringChild("/tmp/x")})
        phase290 = _Phase290Recorder(
            _acquisition(
                "acquired", "resume", approval_digest="c" * 64, plan_digest="b" * 64
            )
        )
        with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
            run_external_publication_operation_start_handoff(
                intent_path=tmp_path / "intent.json",
                start_path=tmp_path / "start.json",
                request=forged,
                phase290_function=phase290,
            )
        _assert_handoff_error(info.value, "path_type")
        assert phase290.calls == []

    forged = dataclasses.replace(base, approval=object())
    phase290 = _Phase290Recorder(
        _acquisition(
            "acquired", "resume", approval_digest="c" * 64, plan_digest="b" * 64
        )
    )
    with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=forged,
            phase290_function=phase290,
        )
    _assert_handoff_error(info.value, "request_contract")
    assert phase290.calls == []


def test_resume_preflight_propagates_known_approval_error_by_identity(
    tmp_path: Path,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    forged = _forged_instance(ExternalPublicationApproval, approval)
    object.__setattr__(forged, "publication_plan_sha256", "A" * 64)
    request = dataclasses.replace(_resume_request(tmp_path), approval=forged)
    phase290 = _Phase290Recorder(_acquire_ok("resume"))
    with pytest.raises(ExternalPublicationApprovalError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=request,
            phase290_function=phase290,
        )
    assert type(info.value) is ExternalPublicationApprovalError
    assert info.value.detail.classification == "digest"
    assert phase290.calls == []


def test_resume_preflight_requires_lowercase_plan_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan()
    approval = _approval(plan)
    forged = _forged_instance(ExternalPublicationApproval, approval)
    object.__setattr__(forged, "publication_plan_sha256", "A" * 64)
    request = dataclasses.replace(_resume_request(tmp_path), approval=forged)
    monkeypatch.setattr(
        handoff_module, "external_publication_approval_digest", lambda value: "c" * 64
    )
    phase290 = _Phase290Recorder(_acquire_ok("resume"))
    with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=request,
            phase290_function=phase290,
        )
    _assert_handoff_error(info.value, "request_lineage")
    assert phase290.calls == []


# --- Phase 290 ordering --------------------------------------------------


def _acquire_ok(operation: str) -> ExternalPublicationOperationStartAcquisition:
    plan = _plan()
    approval = _approval(plan)
    return _acquisition(
        "acquired",
        operation,
        approval_digest=external_publication_approval_digest(approval),
        plan_digest=external_publication_plan_digest(plan),
    )


def test_phase290_called_once_with_exact_caller_path_identity(
    tmp_path: Path,
) -> None:
    intent_path = tmp_path / "intent.json"
    start_path = tmp_path / "start.json"
    plan = _plan()
    approval = _approval(plan)
    request = _fresh_request(tmp_path, plan=plan, approval=approval)
    acquisition = _acquisition(
        "acquired",
        "fresh",
        approval_digest=external_publication_approval_digest(approval),
        plan_digest=external_publication_plan_digest(plan),
    )
    phase290 = _Phase290Recorder(acquisition)
    phase288 = _Phase288Recorder(_fresh_result())
    run_external_publication_operation_start_handoff(
        intent_path=intent_path,
        start_path=start_path,
        request=request,
        phase290_function=phase290,
        phase288_function=phase288,
    )
    assert len(phase290.calls) == 1
    assert phase290.calls[0][0] is intent_path
    assert phase290.calls[0][1] is start_path


def test_known_phase290_error_propagates_same_object(tmp_path: Path) -> None:
    error = ExternalPublicationOperationStartError("intent_contract")
    phase290 = _Phase290Recorder(error)
    phase288 = _Phase288Recorder(_fresh_result())
    with pytest.raises(ExternalPublicationOperationStartError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=_fresh_request(tmp_path),
            phase290_function=phase290,
            phase288_function=phase288,
        )
    assert info.value is error
    assert phase288.calls == []


def test_unexpected_phase290_error_is_detail_safe(tmp_path: Path) -> None:
    secret = "provider-secret-should-not-leak"
    phase290 = _Phase290Recorder(RuntimeError(secret))
    with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=_fresh_request(tmp_path),
            phase290_function=phase290,
        )
    _assert_handoff_error(info.value, "dependency_error")
    assert secret not in str(info.value)
    assert secret not in repr(info.value.detail)
    assert info.value.__cause__ is None


@pytest.mark.parametrize("bad", [object(), "acquired", None, {"status": "acquired"}])
def test_non_acquisition_runtime_type_rejected(bad: object, tmp_path: Path) -> None:
    phase290 = _Phase290Recorder(bad)
    phase288 = _Phase288Recorder(_fresh_result())
    with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=_fresh_request(tmp_path),
            phase290_function=phase290,
            phase288_function=phase288,
        )
    _assert_handoff_error(info.value, "start_contract")
    assert phase288.calls == []


def test_acquisition_subclass_and_forged_status_rejected(tmp_path: Path) -> None:
    class AcquisitionChild(ExternalPublicationOperationStartAcquisition):
        pass

    good = _acquire_ok("fresh")
    child = object.__new__(AcquisitionChild)
    object.__setattr__(child, "status", good.status)
    object.__setattr__(child, "start", good.start)
    phase288 = _Phase288Recorder(_fresh_result())
    with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=_fresh_request(tmp_path),
            phase290_function=_Phase290Recorder(child),
            phase288_function=phase288,
        )
    _assert_handoff_error(info.value, "start_contract")

    forged_status = _forged_instance(ExternalPublicationOperationStartAcquisition, good)
    object.__setattr__(forged_status, "status", "bogus")
    with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=_fresh_request(tmp_path),
            phase290_function=_Phase290Recorder(forged_status),
            phase288_function=phase288,
        )
    _assert_handoff_error(info.value, "start_contract")
    assert phase288.calls == []


def test_forged_start_rejected(tmp_path: Path) -> None:
    good = _acquire_ok("fresh")
    forged_start = _forged_instance(ExternalPublicationOperationStart, good.start)
    object.__setattr__(forged_start, "state", "running")
    acquisition = _forged_instance(ExternalPublicationOperationStartAcquisition, good)
    object.__setattr__(acquisition, "start", forged_start)
    phase288 = _Phase288Recorder(_fresh_result())
    with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=_fresh_request(tmp_path),
            phase290_function=_Phase290Recorder(acquisition),
            phase288_function=phase288,
        )
    _assert_handoff_error(info.value, "start_contract")
    assert phase288.calls == []


def test_start_lineage_mismatch_stops_before_phase288(tmp_path: Path) -> None:
    plan = _plan()
    approval = _approval(plan)
    request = _fresh_request(tmp_path, plan=plan, approval=approval)
    approval_digest = external_publication_approval_digest(approval)
    plan_digest = external_publication_plan_digest(plan)

    cases = (
        (
            _acquisition(
                "acquired",
                "resume",
                approval_digest=approval_digest,
                plan_digest=plan_digest,
            ),
            "operation_mismatch",
        ),
        (
            _acquisition(
                "acquired", "fresh", approval_digest="9" * 64, plan_digest=plan_digest
            ),
            "start_lineage",
        ),
        (
            _acquisition(
                "acquired",
                "fresh",
                approval_digest=approval_digest,
                plan_digest="9" * 64,
            ),
            "start_lineage",
        ),
    )
    for acquisition, classification in cases:
        phase288 = _Phase288Recorder(_fresh_result())
        with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
            run_external_publication_operation_start_handoff(
                intent_path=tmp_path / "intent.json",
                start_path=tmp_path / "start.json",
                request=request,
                phase290_function=_Phase290Recorder(acquisition),
                phase288_function=phase288,
            )
        _assert_handoff_error(info.value, classification)
        assert phase288.calls == []


# --- acquired / already_acquired branching -------------------------------


def test_already_acquired_fresh_returns_same_object_zero_phase288(
    tmp_path: Path,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    request = _fresh_request(tmp_path, plan=plan, approval=approval)
    acquisition = _acquisition(
        "already_acquired",
        "fresh",
        approval_digest=external_publication_approval_digest(approval),
        plan_digest=external_publication_plan_digest(plan),
    )
    phase290 = _Phase290Recorder(acquisition)
    phase288 = _Phase288Recorder(_fresh_result())
    returned = run_external_publication_operation_start_handoff(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        request=request,
        phase290_function=phase290,
        phase288_function=phase288,
    )
    assert returned is acquisition
    assert phase288.calls == []
    assert len(phase290.calls) == 1


def test_already_acquired_resume_returns_same_object_zero_phase288(
    tmp_path: Path,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    request = _resume_request(tmp_path, approval=approval)
    acquisition = _acquisition(
        "already_acquired",
        "resume",
        approval_digest=external_publication_approval_digest(approval),
        plan_digest=approval.publication_plan_sha256,
    )
    phase288 = _Phase288Recorder(_resume_result())
    returned = run_external_publication_operation_start_handoff(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        request=request,
        phase290_function=_Phase290Recorder(acquisition),
        phase288_function=phase288,
    )
    assert returned is acquisition
    assert phase288.calls == []


def test_acquired_fresh_dispatches_exactly_once_with_request_identity(
    tmp_path: Path,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    request = _fresh_request(tmp_path, plan=plan, approval=approval)
    result = _fresh_result()
    phase288 = _Phase288Recorder(result)
    returned = run_external_publication_operation_start_handoff(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        request=request,
        phase290_function=_Phase290Recorder(
            _acquisition(
                "acquired",
                "fresh",
                approval_digest=external_publication_approval_digest(approval),
                plan_digest=external_publication_plan_digest(plan),
            )
        ),
        phase288_function=phase288,
    )
    assert returned is result
    assert len(phase288.calls) == 1
    assert phase288.calls[0] is request


def test_acquired_resume_dispatches_exactly_once_with_request_identity(
    tmp_path: Path,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    request = _resume_request(tmp_path, approval=approval)
    result = _resume_result()
    phase288 = _Phase288Recorder(result)
    returned = run_external_publication_operation_start_handoff(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        request=request,
        phase290_function=_Phase290Recorder(
            _acquisition(
                "acquired",
                "resume",
                approval_digest=external_publication_approval_digest(approval),
                plan_digest=approval.publication_plan_sha256,
            )
        ),
        phase288_function=phase288,
    )
    assert returned is result
    assert len(phase288.calls) == 1
    assert phase288.calls[0] is request


def test_wrong_result_type_rejected_without_second_call(tmp_path: Path) -> None:
    plan = _plan()
    approval = _approval(plan)
    acquisition = _acquisition(
        "acquired",
        "fresh",
        approval_digest=external_publication_approval_digest(approval),
        plan_digest=external_publication_plan_digest(plan),
    )
    malformed = _forged_instance(ExternalPublicationExecutionResult, _fresh_result())
    object.__setattr__(malformed, "status", "bogus")
    cases = (
        (_resume_result(), "fresh_result_contract"),
        (object(), "fresh_result_contract"),
        (malformed, "fresh_result_contract"),
    )
    for result, classification in cases:
        phase288 = _Phase288Recorder(result)
        with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
            run_external_publication_operation_start_handoff(
                intent_path=tmp_path / "intent.json",
                start_path=tmp_path / "start.json",
                request=_fresh_request(tmp_path, plan=plan, approval=approval),
                phase290_function=_Phase290Recorder(acquisition),
                phase288_function=phase288,
            )
        _assert_handoff_error(info.value, classification)
        assert len(phase288.calls) == 1


def test_wrong_resume_result_rejected_without_second_call(tmp_path: Path) -> None:
    plan = _plan()
    approval = _approval(plan)
    acquisition = _acquisition(
        "acquired",
        "resume",
        approval_digest=external_publication_approval_digest(approval),
        plan_digest=approval.publication_plan_sha256,
    )
    forged = _forged_instance(
        ExternalPublicationExecutionReconciliation, _resume_result()
    )
    object.__setattr__(forged, "claim_sha256", "z")
    phase288 = _Phase288Recorder(forged)
    with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=_resume_request(tmp_path, approval=approval),
            phase290_function=_Phase290Recorder(acquisition),
            phase288_function=phase288,
        )
    _assert_handoff_error(info.value, "resume_result_contract")
    assert len(phase288.calls) == 1


def test_known_phase288_error_propagates_same_object(tmp_path: Path) -> None:
    plan = _plan()
    approval = _approval(plan)
    error = ExternalPublicationOperationError("dependency_error")
    phase288 = _Phase288Recorder(error)
    with pytest.raises(ExternalPublicationOperationError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=_fresh_request(tmp_path, plan=plan, approval=approval),
            phase290_function=_Phase290Recorder(
                _acquisition(
                    "acquired",
                    "fresh",
                    approval_digest=external_publication_approval_digest(approval),
                    plan_digest=external_publication_plan_digest(plan),
                )
            ),
            phase288_function=phase288,
        )
    assert info.value is error
    assert len(phase288.calls) == 1


def test_unexpected_phase288_error_is_detail_safe(tmp_path: Path) -> None:
    plan = _plan()
    approval = _approval(plan)
    secret = "network-token-should-not-leak"
    phase288 = _Phase288Recorder(RuntimeError(secret))
    with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            request=_fresh_request(tmp_path, plan=plan, approval=approval),
            phase290_function=_Phase290Recorder(
                _acquisition(
                    "acquired",
                    "fresh",
                    approval_digest=external_publication_approval_digest(approval),
                    plan_digest=external_publication_plan_digest(plan),
                )
            ),
            phase288_function=phase288,
        )
    _assert_handoff_error(info.value, "dependency_error")
    assert secret not in str(info.value)
    assert len(phase288.calls) == 1


# --- restart replay regression ------------------------------------------


def test_restart_replay_regression_in_memory(tmp_path: Path) -> None:
    import ai_office.engine.external_publication_operation as operation_module
    from ai_office.engine.external_publication_execution_evidence import (
        ExternalPublicationExecutionEvidenceError,
    )

    plan = _plan()
    approval = _approval(plan)
    intent = build_external_publication_operation_intent(approval, operation="fresh")
    intent_path = tmp_path / "intent.json"
    start_path = tmp_path / "start.json"
    persist_external_publication_operation_intent(intent_path, intent)
    request = _fresh_request(tmp_path, plan=plan, approval=approval)

    ambiguous = ExternalPublicationExecutionEvidenceError("write")
    phase288 = _Phase288Recorder(ambiguous)

    with pytest.raises(ExternalPublicationExecutionEvidenceError) as info:
        run_external_publication_operation_start_handoff(
            intent_path=intent_path,
            start_path=start_path,
            request=request,
            phase288_function=phase288,
        )
    assert info.value is ambiguous
    assert start_path.exists()
    first_bytes = start_path.read_bytes()

    second = run_external_publication_operation_start_handoff(
        intent_path=intent_path,
        start_path=start_path,
        request=request,
        phase288_function=phase288,
    )
    assert type(second) is ExternalPublicationOperationStartAcquisition
    assert second.status == "already_acquired"
    assert second.start.operation == "fresh"
    assert len(phase288.calls) == 1
    assert start_path.read_bytes() == first_bytes
    assert operation_module.run_external_publication_operation is not None


# --- real-file integration with real Phase 289/290 ----------------------


def test_real_durable_fence_fresh_restart_fence(tmp_path: Path) -> None:
    plan = _plan()
    approval = _approval(plan)
    intent = build_external_publication_operation_intent(approval, operation="fresh")
    intent_path = tmp_path / "intent.json"
    start_path = tmp_path / "start.json"
    persist_external_publication_operation_intent(intent_path, intent)

    request = _fresh_request(tmp_path, plan=plan, approval=approval)
    result = _fresh_result()
    phase288 = _Phase288Recorder(result)

    first = run_external_publication_operation_start_handoff(
        intent_path=intent_path,
        start_path=start_path,
        request=request,
        phase288_function=phase288,
    )
    assert first is result
    assert len(phase288.calls) == 1
    intent_bytes = intent_path.read_bytes()
    start_bytes = start_path.read_bytes()

    second = run_external_publication_operation_start_handoff(
        intent_path=intent_path,
        start_path=start_path,
        request=request,
        phase288_function=phase288,
    )
    assert type(second) is ExternalPublicationOperationStartAcquisition
    assert second.status == "already_acquired"
    assert second.start.operation == "fresh"
    assert len(phase288.calls) == 1
    assert intent_path.read_bytes() == intent_bytes
    assert start_path.read_bytes() == start_bytes


def test_real_durable_fence_resume_restart_fence(tmp_path: Path) -> None:
    plan = _plan()
    approval = _approval(plan)
    intent = build_external_publication_operation_intent(approval, operation="resume")
    intent_path = tmp_path / "intent.json"
    start_path = tmp_path / "start.json"
    persist_external_publication_operation_intent(intent_path, intent)

    request = _resume_request(tmp_path, approval=approval)
    result = _resume_result()
    phase288 = _Phase288Recorder(result)

    first = run_external_publication_operation_start_handoff(
        intent_path=intent_path,
        start_path=start_path,
        request=request,
        phase288_function=phase288,
    )
    assert first is result
    assert len(phase288.calls) == 1

    second = run_external_publication_operation_start_handoff(
        intent_path=intent_path,
        start_path=start_path,
        request=request,
        phase288_function=phase288,
    )
    assert type(second) is ExternalPublicationOperationStartAcquisition
    assert second.status == "already_acquired"
    assert second.start.operation == "resume"
    assert len(phase288.calls) == 1


# --- source audit -------------------------------------------------------


def _module_imports() -> dict[str, set[str]]:
    tree = ast.parse(_SOURCE)
    imports: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.setdefault(node.level * "." + node.module, set())
            for alias in node.names:
                imports[node.level * "." + node.module].add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.setdefault(alias.name, set())
    return imports


def test_source_audit_no_forbidden_lifecycle_imports() -> None:
    imports = _module_imports()
    flat = {f"{module}:{name}" for module, names in imports.items() for name in names}

    forbidden_names = {
        "claim_external_publication_attempt",
        "load_external_publication_attempt_claim",
        "build_external_publication_attempt_claim",
        "execute_approved_external_publication",
        "execute_and_persist_approved_external_publication",
        "resume_external_publication_reconciliation_closure",
        "reconcile_and_persist_external_publication_execution",
    }
    assert not any(
        name in forbidden_names for module, names in imports.items() for name in names
    ), flat

    orchestration = imports.get(".external_publication_execution_orchestration", set())
    assert orchestration == {"ExternalPublicationExecutionOrchestrationError"}
    resume = imports.get(".external_publication_execution_reconciliation_resume", set())
    assert resume == {"ExternalPublicationExecutionReconciliationResumeError"}

    assert not any("external_publication_attempt_claim" in module for module in imports)


def test_source_audit_no_clock_random_environment_access() -> None:
    for token in (
        "import os",
        "os.environ",
        "getenv",
        "import random",
        "import uuid",
        "import time",
        "import datetime",
        "sleep(",
        "subprocess",
        "socket",
    ):
        assert token not in _SOURCE, token


def test_source_audit_no_direct_phase285_or_287_calls() -> None:
    tree = ast.parse(_SOURCE)
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)
    assert "phase285_function" not in str(_SOURCE) or "phase287_function" not in str(
        _SOURCE
    )
    for forbidden in (
        "execute_and_persist_approved_external_publication",
        "resume_external_publication_reconciliation_closure",
        "claim_external_publication_attempt",
    ):
        assert forbidden not in called, forbidden


def test_no_cli_change_and_no_phase291_command() -> None:
    from typer.testing import CliRunner

    from ai_office.cli import app

    runner = CliRunner()
    root = runner.invoke(app, ["--help"])
    assert root.exit_code == 0
    assert "handoff" not in root.output.lower()

    workflows = runner.invoke(app, ["workflows", "--help"])
    assert workflows.exit_code == 0
    assert "handoff" not in workflows.output.lower()
    assert "phase291" not in workflows.output.lower().replace(" ", "")

    cli_source = Path("src/ai_office/cli.py").read_text(encoding="utf-8")
    assert "start_handoff" not in cli_source
