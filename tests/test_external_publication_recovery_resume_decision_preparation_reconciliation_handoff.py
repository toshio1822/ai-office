# ruff: noqa: E501

"""Provider-free direct regressions for the Phase 306 boundary."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_resume_decision_preparation_reconciliation_handoff as handoff_module
from ai_office.engine import (
    ExternalPublicationApproval,
    ExternalPublicationApprovalError,
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationError,
    ExternalPublicationExecutionReconciliationEvidenceError,
    ExternalPublicationExecutionReconciliationOrchestrationError,
    ExternalPublicationExecutionReconciliationResumeError,
    ExternalPublicationExecutionResult,
    ExternalPublicationFreshOperationRequest,
    ExternalPublicationOperationError,
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartAcquisition,
    ExternalPublicationOperationStartError,
    ExternalPublicationPlan,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffCompatibilityError,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffError,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffFailureDetail,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
    ExternalPublicationResumeOperationRequest,
    ExternalPublicationTarget,
    approve_external_publication,
    claim_external_publication_attempt,
    external_publication_approval_digest,
    external_publication_attempt_claim_path,
    external_publication_consumption_key,
    external_publication_operation_intent_digest,
    external_publication_operation_start_digest,
    persist_external_publication_execution_result,
    persist_external_publication_operation_intent,
    persist_external_publication_recovery_resume_decision_preparation_start_authorization,
    run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff,
)

_AUTHORIZATION_SCHEMA = (
    "external-publication-recovery-resume-decision-preparation-start-authorization.v1"
)
_ROUTE_SCHEMA = "external-publication-recovery-resume-decision-preparation-start-acquisition-route.v1"
_START_SCHEMA = "external-publication-operation-start.v1"
_INTENT_SCHEMA = "external-publication-operation-intent.v1"
_AUTHORIZATION_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-start-authorization-"
)
_INTENT_PREFIX = "external-publication-operation-intent-"
_SUFFIX = ".json"
_MESSAGE = (
    "external publication recovery resume decision preparation reconciliation "
    "handoff is blocked"
)
_SOURCE = Path(handoff_module.__file__).read_text(encoding="utf-8")


class _Recorder:
    def __init__(
        self,
        result: object = None,
        *,
        fault: BaseException | None = None,
        delegate: object | None = None,
    ) -> None:
        self.result = result
        self.fault = fault
        self.delegate = delegate
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.results: list[object] = []

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        if self.fault is not None:
            raise self.fault
        result = (
            self.delegate(*args, **kwargs)  # type: ignore[operator]
            if self.delegate is not None
            else self.result
        )
        self.results.append(result)
        return result

    @property
    def call_count(self) -> int:
        return len(self.calls)


class _PathChild(type(Path())):
    pass


class _RouteChild(
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute
):
    pass


class _AcquisitionChild(ExternalPublicationOperationStartAcquisition):
    pass


class _StartChild(ExternalPublicationOperationStart):
    pass


class _ReconciliationChild(ExternalPublicationExecutionReconciliation):
    pass


class _StringChild(str):
    pass


def _forged(source: object, **overrides: object) -> object:
    value = object.__new__(type(source))
    names = {field.name for field in dataclasses.fields(source)}  # type: ignore[arg-type]
    for name in overrides:
        assert name in names, name
    for name in names:
        object.__setattr__(value, name, getattr(source, name))
    for name, replacement in overrides.items():
        object.__setattr__(value, name, replacement)
    return value


def _plan() -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id="phase306-regeneration",
        reconciliation_evidence_sha256="a" * 64,
        receipt_sha256="b" * 64,
        business_output_sha256="c" * 64,
        output_byte_length=13,
        provider="future-provider",
        publication_target_sha256="d" * 64,
    )


def _approval() -> ExternalPublicationApproval:
    return approve_external_publication(
        _plan(),
        approved_by="phase306-reviewer",
        approval_id="phase306-approval",
    )


def _request(
    root: Path, approval: ExternalPublicationApproval | None = None
) -> ExternalPublicationResumeOperationRequest:
    return ExternalPublicationResumeOperationRequest(
        ledger_directory=root / "ledger",
        approval=_approval() if approval is None else approval,
        execution_evidence_path=root / "execution-evidence.json",
        execution_reconciliation_evidence_path=root / "reconciliation-evidence.json",
    )


def _authorization_lineage(
    root: Path,
    approval: ExternalPublicationApproval,
    *,
    binding_digest: str = "1" * 64,
    previous: str = "already_acquired",
    recovery: str = "reconciliation_mismatch",
    result_kind: str = "reconciliation",
    result_sha256: str | None = "4" * 64,
) -> tuple[
    Path,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
    ExternalPublicationOperationStart,
]:
    approval_digest = external_publication_approval_digest(approval)
    intent = ExternalPublicationOperationIntent(
        schema_version=_INTENT_SCHEMA,
        publication_approval_sha256=approval_digest,
        publication_plan_sha256=approval.publication_plan_sha256,
        operation="resume",
    )
    intent_digest = external_publication_operation_intent_digest(intent)
    start = ExternalPublicationOperationStart(
        schema_version=_START_SCHEMA,
        operation_intent_sha256=intent_digest,
        publication_approval_sha256=approval_digest,
        publication_plan_sha256=approval.publication_plan_sha256,
        operation="resume",
        state="started",
    )
    authorization = (
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization(
            schema_version=_AUTHORIZATION_SCHEMA,
            decision_preparation_intent_binding_sha256=binding_digest,
            decision_preparation_sha256="2" * 64,
            recovery_resume_decision_sha256="3" * 64,
            operation_intent_sha256=intent_digest,
            expected_operation_start_sha256=external_publication_operation_start_digest(
                start
            ),
            publication_approval_sha256=approval_digest,
            publication_plan_sha256=approval.publication_plan_sha256,
            source_operation="resume",
            previous_recovery_kind=previous,  # type: ignore[arg-type]
            recovery_kind=recovery,  # type: ignore[arg-type]
            result_kind=result_kind,  # type: ignore[arg-type]
            result_sha256=result_sha256,
            operation="resume",
            state="authorized",
        )
    )
    path = root / f"{_AUTHORIZATION_PREFIX}{binding_digest}{_SUFFIX}"
    return path, authorization, start


def _route(
    start: ExternalPublicationOperationStart,
    *,
    status: str = "acquired",
    route: str = "fresh_start_acquired",
) -> ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute:
    acquisition = ExternalPublicationOperationStartAcquisition(
        status=status,  # type: ignore[arg-type]
        start=start,
    )
    return ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute(
        schema_version=_ROUTE_SCHEMA,
        acquisition=acquisition,
        route=route,  # type: ignore[arg-type]
    )


def _reconciliation() -> ExternalPublicationExecutionReconciliation:
    return ExternalPublicationExecutionReconciliation(
        schema_version="external-publication-execution-reconciliation.v1",
        claim_sha256="5" * 64,
        execution_evidence_sha256="6" * 64,
        status="matched",
        mismatched_fields=(),
    )


def _assert_error(error: ValueError, classification: str) -> None:
    assert (
        type(error)
        is ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffCompatibilityError
    )
    assert isinstance(
        error,
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffError,
    )
    assert isinstance(error, ValueError)
    assert str(error) == _MESSAGE
    assert (
        type(error.detail)
        is ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffFailureDetail
    )
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _valid_injected_lineage(
    tmp_path: Path,
) -> tuple[
    Path,
    ExternalPublicationResumeOperationRequest,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
    ExternalPublicationOperationStart,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute,
    ExternalPublicationExecutionReconciliation,
]:
    approval = _approval()
    path, authorization, start = _authorization_lineage(tmp_path, approval)
    return (
        path,
        _request(tmp_path, approval),
        authorization,
        start,
        _route(start),
        _reconciliation(),
    )


def test_public_api_is_keyword_only_and_defaults_are_exact() -> None:
    function = handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff
    parameters = inspect.signature(function).parameters
    assert list(parameters) == [
        "start_authorization_path",
        "request",
        "authorization_loader",
        "approval_digest_function",
        "start_digest_function",
        "phase305_function",
        "phase288_function",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in parameters.values()
    )
    assert (
        parameters["authorization_loader"].default
        is handoff_module.load_external_publication_recovery_resume_decision_preparation_start_authorization
    )
    assert (
        parameters["approval_digest_function"].default
        is handoff_module.external_publication_approval_digest
    )
    assert (
        parameters["start_digest_function"].default
        is handoff_module.external_publication_operation_start_digest
    )
    assert (
        parameters["phase305_function"].default
        is handoff_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition
    )
    assert (
        parameters["phase288_function"].default
        is handoff_module.run_external_publication_operation
    )


def test_fresh_request_is_rejected_before_authorization_load_or_phase305(
    tmp_path: Path,
) -> None:
    approval = _approval()
    fresh = ExternalPublicationFreshOperationRequest(
        execution_evidence_path=tmp_path / "execution.json",
        plan_reconciliation_evidence_path=tmp_path / "plan-reconciliation.json",
        output_path=tmp_path / "output.bin",
        ledger_directory=tmp_path / "ledger",
        plan=_plan(),
        approval=approval,
        target=ExternalPublicationTarget(
            schema_version="external-publication-target.v1",
            provider="future-provider",
            destination_id="phase306-destination",
        ),
        transport=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
    )
    loader = _Recorder()
    phase305 = _Recorder()
    with pytest.raises(ValueError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=tmp_path / "authorization.json",
            request=fresh,  # type: ignore[arg-type]
            authorization_loader=loader,
            phase305_function=phase305,
        )
    _assert_error(caught.value, "request_contract")
    assert loader.call_count == 0
    assert phase305.call_count == 0


def test_preflight_rejects_path_subclass_request_paths_and_noncallable_dependencies(
    tmp_path: Path,
) -> None:
    path, request, _, _, _, _ = _valid_injected_lineage(tmp_path)
    loader = _Recorder()
    phase305 = _Recorder()
    with pytest.raises(ValueError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=_PathChild(path),
            request=request,
            authorization_loader=loader,
            phase305_function=phase305,
        )
    _assert_error(caught.value, "path_type")
    assert loader.call_count == 0
    assert phase305.call_count == 0

    forged_request = _forged(
        request, ledger_directory=_PathChild(request.ledger_directory)
    )
    with pytest.raises(ValueError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=path,
            request=forged_request,  # type: ignore[arg-type]
            authorization_loader=loader,
            phase305_function=phase305,
        )
    _assert_error(caught.value, "path_type")
    assert loader.call_count == 0
    assert phase305.call_count == 0

    for dependency_name in (
        "authorization_loader",
        "approval_digest_function",
        "start_digest_function",
        "phase305_function",
        "phase288_function",
    ):
        dependencies = {
            "authorization_loader": _Recorder(),
            "approval_digest_function": _Recorder(),
            "start_digest_function": _Recorder(),
            "phase305_function": _Recorder(),
            "phase288_function": _Recorder(),
        }
        dependencies[dependency_name] = object()
        with pytest.raises(ValueError) as caught:
            handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
                start_authorization_path=path,
                request=request,
                **dependencies,  # type: ignore[arg-type]
            )
        _assert_error(caught.value, "configuration")


def test_authorization_loader_is_exactly_once_with_exact_path_and_local_reconstruction(
    tmp_path: Path,
) -> None:
    path, request, authorization, start, route, reconciliation = (
        _valid_injected_lineage(tmp_path)
    )
    loader = _Recorder(authorization)
    approval_digest = _Recorder(external_publication_approval_digest(request.approval))
    start_digest = _Recorder(external_publication_operation_start_digest(start))
    phase305 = _Recorder(route)
    phase288 = _Recorder(reconciliation)
    result = handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=path,
        request=request,
        authorization_loader=loader,
        approval_digest_function=approval_digest,
        start_digest_function=start_digest,
        phase305_function=phase305,
        phase288_function=phase288,
    )
    assert result is reconciliation
    assert loader.calls == [((path,), {})]
    assert loader.calls[0][0][0] is path
    assert approval_digest.calls == [((request.approval,), {})]
    assert approval_digest.calls[0][0][0] is request.approval
    assert start_digest.calls == [((start,), {})]
    assert start_digest.calls[0][0][0] is route.acquisition.start
    assert phase305.calls == [((), {"start_authorization_path": path})]
    assert phase305.calls[0][1]["start_authorization_path"] is path
    assert phase288.calls == [((request,), {})]
    assert phase288.calls[0][0][0] is request


def test_authorization_loader_known_error_identity_is_preserved_and_phase305_is_zero_call(
    tmp_path: Path,
) -> None:
    path, request, _, _, _, _ = _valid_injected_lineage(tmp_path)
    error = ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError(
        "load"
    )
    loader = _Recorder(fault=error)
    phase305 = _Recorder()
    with pytest.raises(type(error)) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=path,
            request=request,
            authorization_loader=loader,
            phase305_function=phase305,
        )
    assert caught.value is error
    assert loader.call_count == 1
    assert phase305.call_count == 0


def test_unexpected_authorization_error_is_detail_safe(tmp_path: Path) -> None:
    path, request, _, _, _, _ = _valid_injected_lineage(tmp_path)
    loader = _Recorder(fault=RuntimeError("secret authorization path"))
    with pytest.raises(ValueError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=path,
            request=request,
            authorization_loader=loader,
        )
    _assert_error(caught.value, "dependency_error")
    assert "secret" not in str(caught.value)


def test_wrong_authorization_filename_precedes_approval_digest_and_phase305(
    tmp_path: Path,
) -> None:
    path, request, authorization, _, _, _ = _valid_injected_lineage(tmp_path)
    wrong_path = tmp_path / "wrong-authorization.json"
    loader = _Recorder(authorization)
    approval_digest = _Recorder("a" * 64)
    phase305 = _Recorder()
    with pytest.raises(ValueError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=wrong_path,
            request=request,
            authorization_loader=loader,
            approval_digest_function=approval_digest,
            phase305_function=phase305,
        )
    _assert_error(caught.value, "authorization_path")
    assert loader.call_count == 1
    assert approval_digest.call_count == 0
    assert phase305.call_count == 0


def test_request_approval_model_error_identity_is_preserved_before_phase305(
    tmp_path: Path,
) -> None:
    path, request, authorization, _, _, _ = _valid_injected_lineage(tmp_path)
    bad_approval = _forged(request.approval, approved=False)
    bad_request = _forged(request, approval=bad_approval)
    phase305 = _Recorder()
    with pytest.raises(ExternalPublicationApprovalError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=path,
            request=bad_request,  # type: ignore[arg-type]
            authorization_loader=_Recorder(authorization),
            phase305_function=phase305,
        )
    assert caught.value.detail.classification == "approval_metadata"
    assert phase305.call_count == 0


@pytest.mark.parametrize("mismatch_kind", ["approval", "plan"])
def test_request_approval_or_plan_mismatch_is_zero_call_to_phase305(
    tmp_path: Path, mismatch_kind: str
) -> None:
    path, request, authorization, _, _, _ = _valid_injected_lineage(tmp_path)
    if mismatch_kind == "approval":
        digest_function = _Recorder("0" * 64)
    else:
        bad_approval = _forged(
            request.approval,
            publication_plan_sha256="e" * 64,
        )
        request = _forged(request, approval=bad_approval)  # type: ignore[assignment]
        digest_function = _Recorder(authorization.publication_approval_sha256)
    phase305 = _Recorder()
    with pytest.raises(ValueError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=path,
            request=request,  # type: ignore[arg-type]
            authorization_loader=_Recorder(authorization),
            approval_digest_function=digest_function,
            phase305_function=phase305,
        )
    _assert_error(caught.value, "request_lineage")
    assert digest_function.call_count == 1
    assert phase305.call_count == 0


def test_malformed_approval_digest_is_zero_call_to_phase305(tmp_path: Path) -> None:
    path, request, authorization, _, _, _ = _valid_injected_lineage(tmp_path)
    digest_function = _Recorder(_StringChild("a" * 64))
    phase305 = _Recorder()
    with pytest.raises(ValueError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=path,
            request=request,
            authorization_loader=_Recorder(authorization),
            approval_digest_function=digest_function,
            phase305_function=phase305,
        )
    _assert_error(caught.value, "approval_digest")
    assert phase305.call_count == 0


def test_phase305_is_called_once_with_only_exact_path_and_errors_are_handled(
    tmp_path: Path,
) -> None:
    path, request, authorization, _, _, _ = _valid_injected_lineage(tmp_path)
    known_errors = (
        ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingError(
            "dependency_error"
        ),
        ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError(
            "dependency_error"
        ),
        ExternalPublicationOperationStartError("dependency_error"),
    )
    for error in known_errors:
        phase305 = _Recorder(fault=error)
        with pytest.raises(type(error)) as caught:
            handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
                start_authorization_path=path,
                request=request,
                authorization_loader=_Recorder(authorization),
                approval_digest_function=_Recorder(
                    authorization.publication_approval_sha256
                ),
                phase305_function=phase305,
            )
        assert caught.value is error
        assert phase305.calls == [((), {"start_authorization_path": path})]

    phase305 = _Recorder(fault=RuntimeError("route secret"))
    with pytest.raises(ValueError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=path,
            request=request,
            authorization_loader=_Recorder(authorization),
            approval_digest_function=_Recorder(
                authorization.publication_approval_sha256
            ),
            phase305_function=phase305,
        )
    _assert_error(caught.value, "dependency_error")
    assert phase305.call_count == 1


def test_recovery_required_returns_exact_route_and_phase288_is_zero_call(
    tmp_path: Path,
) -> None:
    path, request, authorization, start, _, _ = _valid_injected_lineage(tmp_path)
    route = _route(start, status="already_acquired", route="recovery_required")
    phase288 = _Recorder()
    result = handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=path,
        request=request,
        authorization_loader=_Recorder(authorization),
        approval_digest_function=_Recorder(authorization.publication_approval_sha256),
        start_digest_function=_Recorder(authorization.expected_operation_start_sha256),
        phase305_function=_Recorder(route),
        phase288_function=phase288,
    )
    assert result is route
    assert phase288.call_count == 0


@pytest.mark.parametrize(
    "route_object",
    [
        "route_subclass",
        "acquisition_subclass",
        "start_subclass",
        "route_status_mismatch",
    ],
)
def test_malformed_route_is_rejected_and_phase288_is_zero_call(
    tmp_path: Path, route_object: str
) -> None:
    path, request, authorization, start, route, _ = _valid_injected_lineage(tmp_path)
    if route_object == "route_subclass":
        route_value = object.__new__(_RouteChild)
        for field in dataclasses.fields(route):
            object.__setattr__(route_value, field.name, getattr(route, field.name))
    elif route_object == "acquisition_subclass":
        acquisition = route.acquisition
        forged_acquisition = object.__new__(_AcquisitionChild)
        for field in dataclasses.fields(acquisition):
            object.__setattr__(
                forged_acquisition, field.name, getattr(acquisition, field.name)
            )
        route_value = _forged(route, acquisition=forged_acquisition)
    elif route_object == "start_subclass":
        start_value = object.__new__(_StartChild)
        for field in dataclasses.fields(start):
            object.__setattr__(start_value, field.name, getattr(start, field.name))
        route_value = _forged(
            route,
            acquisition=_forged(route.acquisition, start=start_value),
        )
    else:
        route_value = _forged(
            route,
            route="recovery_required",
        )
    phase288 = _Recorder()
    with pytest.raises(ValueError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=path,
            request=request,
            authorization_loader=_Recorder(authorization),
            approval_digest_function=_Recorder(
                authorization.publication_approval_sha256
            ),
            phase305_function=_Recorder(route_value),
            phase288_function=phase288,
        )
    _assert_error(caught.value, "route_contract")
    assert phase288.call_count == 0


@pytest.mark.parametrize(
    "field_name, replacement",
    [
        ("operation_intent_sha256", "7" * 64),
        ("publication_approval_sha256", "8" * 64),
        ("publication_plan_sha256", "9" * 64),
        ("operation", "fresh"),
    ],
)
def test_route_lineage_mismatch_is_zero_call_to_phase288(
    tmp_path: Path, field_name: str, replacement: str
) -> None:
    path, request, authorization, start, route, _ = _valid_injected_lineage(tmp_path)
    bad_start = _forged(start, **{field_name: replacement})
    bad_route = _forged(route, acquisition=_forged(route.acquisition, start=bad_start))
    phase288 = _Recorder()
    with pytest.raises(ValueError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=path,
            request=request,
            authorization_loader=_Recorder(authorization),
            approval_digest_function=_Recorder(
                authorization.publication_approval_sha256
            ),
            start_digest_function=_Recorder(
                authorization.expected_operation_start_sha256
            ),
            phase305_function=_Recorder(bad_route),
            phase288_function=phase288,
        )
    _assert_error(caught.value, "route_lineage")
    assert phase288.call_count == 0


def test_start_digest_is_called_once_with_exact_route_start_identity(
    tmp_path: Path,
) -> None:
    path, request, authorization, start, route, _ = _valid_injected_lineage(tmp_path)
    start_digest = _Recorder(authorization.expected_operation_start_sha256)
    result = handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=path,
        request=request,
        authorization_loader=_Recorder(authorization),
        approval_digest_function=_Recorder(authorization.publication_approval_sha256),
        start_digest_function=start_digest,
        phase305_function=_Recorder(
            _route(start, status="already_acquired", route="recovery_required")
        ),
    )
    assert result.route == "recovery_required"  # type: ignore[union-attr]
    assert start_digest.calls == [((route.acquisition.start,), {})]
    assert start_digest.calls[0][0][0] is route.acquisition.start


def test_start_digest_mismatch_is_zero_call_to_phase288(tmp_path: Path) -> None:
    path, request, authorization, start, route, _ = _valid_injected_lineage(tmp_path)
    phase288 = _Recorder()
    with pytest.raises(ValueError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=path,
            request=request,
            authorization_loader=_Recorder(authorization),
            approval_digest_function=_Recorder(
                authorization.publication_approval_sha256
            ),
            start_digest_function=_Recorder("a" * 64),
            phase305_function=_Recorder(route),
            phase288_function=phase288,
        )
    _assert_error(caught.value, "route_lineage")
    assert phase288.call_count == 0


def test_fresh_start_acquired_calls_phase288_once_positionally_and_returns_identity(
    tmp_path: Path,
) -> None:
    path, request, authorization, _, route, reconciliation = _valid_injected_lineage(
        tmp_path
    )
    phase288 = _Recorder(reconciliation)
    result = handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=path,
        request=request,
        authorization_loader=_Recorder(authorization),
        approval_digest_function=_Recorder(authorization.publication_approval_sha256),
        start_digest_function=_Recorder(authorization.expected_operation_start_sha256),
        phase305_function=_Recorder(route),
        phase288_function=phase288,
    )
    assert result is reconciliation
    assert phase288.calls == [((request,), {})]
    assert phase288.calls[0][0][0] is request


def test_phase288_known_error_identity_is_preserved_without_retry(
    tmp_path: Path,
) -> None:
    path, request, authorization, _, route, _ = _valid_injected_lineage(tmp_path)
    errors = (
        ExternalPublicationOperationError("dependency_error"),
        ExternalPublicationExecutionReconciliationResumeError("dependency_error"),
        ExternalPublicationExecutionReconciliationOrchestrationError(
            "dependency_error"
        ),
        ExternalPublicationExecutionReconciliationError("result"),
        ExternalPublicationExecutionReconciliationEvidenceError("dependency_error"),
    )
    for error in errors:
        phase288 = _Recorder(fault=error)
        with pytest.raises(type(error)) as caught:
            handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
                start_authorization_path=path,
                request=request,
                authorization_loader=_Recorder(authorization),
                approval_digest_function=_Recorder(
                    authorization.publication_approval_sha256
                ),
                start_digest_function=_Recorder(
                    authorization.expected_operation_start_sha256
                ),
                phase305_function=_Recorder(route),
                phase288_function=phase288,
            )
        assert caught.value is error
        assert phase288.call_count == 1


def test_phase288_unexpected_error_is_sanitized_and_not_retried(tmp_path: Path) -> None:
    path, request, authorization, _, route, _ = _valid_injected_lineage(tmp_path)
    phase288 = _Recorder(fault=RuntimeError("credential/provider/network detail"))
    with pytest.raises(ValueError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=path,
            request=request,
            authorization_loader=_Recorder(authorization),
            approval_digest_function=_Recorder(
                authorization.publication_approval_sha256
            ),
            start_digest_function=_Recorder(
                authorization.expected_operation_start_sha256
            ),
            phase305_function=_Recorder(route),
            phase288_function=phase288,
        )
    _assert_error(caught.value, "dependency_error")
    assert phase288.call_count == 1
    assert "credential" not in str(caught.value)


def test_malformed_phase288_result_is_rejected_after_one_call_without_retry(
    tmp_path: Path,
) -> None:
    path, request, authorization, _, route, reconciliation = _valid_injected_lineage(
        tmp_path
    )
    malformed = object.__new__(_ReconciliationChild)
    for field in dataclasses.fields(reconciliation):
        object.__setattr__(malformed, field.name, getattr(reconciliation, field.name))
    phase288 = _Recorder(malformed)
    with pytest.raises(ValueError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=path,
            request=request,
            authorization_loader=_Recorder(authorization),
            approval_digest_function=_Recorder(
                authorization.publication_approval_sha256
            ),
            start_digest_function=_Recorder(
                authorization.expected_operation_start_sha256
            ),
            phase305_function=_Recorder(route),
            phase288_function=phase288,
        )
    _assert_error(caught.value, "reconciliation_contract")
    assert phase288.call_count == 1


def _persist_lineage(
    root: Path,
    approval: ExternalPublicationApproval,
    *,
    binding_digest: str = "1" * 64,
    previous: str = "already_acquired",
    recovery: str = "reconciliation_mismatch",
    result_kind: str = "reconciliation",
    result_sha256: str | None = "4" * 64,
) -> Path:
    root.mkdir()
    path, authorization, _ = _authorization_lineage(
        root,
        approval,
        binding_digest=binding_digest,
        previous=previous,
        recovery=recovery,
        result_kind=result_kind,
        result_sha256=result_sha256,
    )
    intent = ExternalPublicationOperationIntent(
        schema_version=_INTENT_SCHEMA,
        publication_approval_sha256=authorization.publication_approval_sha256,
        publication_plan_sha256=authorization.publication_plan_sha256,
        operation="resume",
    )
    intent_path = (
        root / f"{_INTENT_PREFIX}{authorization.operation_intent_sha256}{_SUFFIX}"
    )
    persist_external_publication_operation_intent(intent_path, intent)
    persist_external_publication_recovery_resume_decision_preparation_start_authorization(
        path, authorization
    )
    return path


def _persist_real_provider_free_resume_lineage(
    root: Path,
    *,
    execution_provider: str = "future-provider",
    binding_digest: str = "1" * 64,
    previous: str = "already_acquired",
    recovery: str = "reconciliation_mismatch",
) -> tuple[
    Path, ExternalPublicationResumeOperationRequest, ExternalPublicationApproval
]:
    """Build local claim/evidence inputs consumed by the real Phase 287 closure."""
    approval = _approval()
    authorization_path = _persist_lineage(
        root,
        approval,
        binding_digest=binding_digest,
        previous=previous,
        recovery=recovery,
        result_kind="none" if recovery == "already_acquired" else "reconciliation",
        result_sha256=None if recovery == "already_acquired" else "4" * 64,
    )

    plan = _plan()
    ledger_directory = root / "ledger"
    ledger_directory.mkdir()
    claim = claim_external_publication_attempt(ledger_directory, plan, approval)
    claim_path = external_publication_attempt_claim_path(
        ledger_directory,
        external_publication_consumption_key(approval),
    )
    assert claim_path.exists()

    output = b"phase-306-provider-free-output"
    execution_result = ExternalPublicationExecutionResult(
        schema_version="external-publication-execution-result.v1",
        regeneration_id=claim.regeneration_id,
        publication_attempt_claim_sha256=claim.digest,
        publication_plan_sha256=claim.publication_plan_sha256,
        publication_approval_sha256=claim.publication_approval_sha256,
        business_output_sha256=hashlib.sha256(output).hexdigest(),
        output_byte_length=len(output),
        provider=execution_provider,
        publication_target_sha256=claim.publication_target_sha256,
        publication_id="phase306-local-publication",
        status="published",
    )
    execution_evidence_path = root / "execution-evidence.json"
    persist_external_publication_execution_result(
        execution_evidence_path,
        execution_result,
    )
    return authorization_path, _request(root, approval), approval


def _persist_same_namespace_cycles(
    root: Path,
) -> tuple[
    Path,
    Path,
    ExternalPublicationResumeOperationRequest,
    ExternalPublicationApproval,
]:
    """Persist two Phase 303 authorizations sharing intent/start identity."""
    root.mkdir()
    approval = _approval()
    path_a, authorization_a, _ = _authorization_lineage(
        root,
        approval,
        binding_digest="7" * 64,
    )
    path_b, authorization_b, _ = _authorization_lineage(
        root,
        approval,
        binding_digest="8" * 64,
    )
    assert (
        authorization_a.operation_intent_sha256
        == authorization_b.operation_intent_sha256
    )
    assert (
        authorization_a.expected_operation_start_sha256
        == authorization_b.expected_operation_start_sha256
    )
    intent = ExternalPublicationOperationIntent(
        schema_version=_INTENT_SCHEMA,
        publication_approval_sha256=authorization_a.publication_approval_sha256,
        publication_plan_sha256=authorization_a.publication_plan_sha256,
        operation="resume",
    )
    persist_external_publication_operation_intent(
        root / f"{_INTENT_PREFIX}{authorization_a.operation_intent_sha256}{_SUFFIX}",
        intent,
    )
    persist_external_publication_recovery_resume_decision_preparation_start_authorization(
        path_a,
        authorization_a,
    )
    persist_external_publication_recovery_resume_decision_preparation_start_authorization(
        path_b,
        authorization_b,
    )

    plan = _plan()
    ledger_directory = root / "ledger"
    ledger_directory.mkdir()
    claim = claim_external_publication_attempt(ledger_directory, plan, approval)
    execution_result = ExternalPublicationExecutionResult(
        schema_version="external-publication-execution-result.v1",
        regeneration_id=claim.regeneration_id,
        publication_attempt_claim_sha256=claim.digest,
        publication_plan_sha256=claim.publication_plan_sha256,
        publication_approval_sha256=claim.publication_approval_sha256,
        business_output_sha256=hashlib.sha256(b"phase-306-cycle-output").hexdigest(),
        output_byte_length=len(b"phase-306-cycle-output"),
        provider="future-provider",
        publication_target_sha256=claim.publication_target_sha256,
        publication_id="phase306-cycle-publication",
        status="published",
    )
    persist_external_publication_execution_result(
        root / "execution-evidence.json",
        execution_result,
    )
    return path_a, path_b, _request(root, approval), approval


def test_marker_only_crash_window_is_recovery_required_and_phase288_zero_call(
    tmp_path: Path,
) -> None:
    approval = _approval()
    path = _persist_lineage(tmp_path / "crash-window", approval)
    run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
        start_authorization_path=path
    )
    phase288 = _Recorder()
    result = handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=path,
        request=_request(path.parent, approval),
        phase288_function=phase288,
    )
    assert result.route == "recovery_required"  # type: ignore[union-attr]
    assert result.acquisition.status == "already_acquired"  # type: ignore[union-attr]
    assert phase288.call_count == 0


def test_failure_after_acquisition_keeps_marker_and_second_call_is_recovery_required(
    tmp_path: Path,
) -> None:
    approval = _approval()
    path = _persist_lineage(tmp_path / "failure-after-acquisition", approval)
    marker_prefix = "external-publication-recovery-resume-start-"
    marker_before = set(path.parent.glob(f"{marker_prefix}*.json"))
    known_error = ExternalPublicationOperationError("dependency_error")
    phase288_first = _Recorder(fault=known_error)
    with pytest.raises(type(known_error)) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
            start_authorization_path=path,
            request=_request(path.parent, approval),
            phase288_function=phase288_first,
        )
    assert caught.value is known_error
    assert phase288_first.call_count == 1
    marker_after = set(path.parent.glob(f"{marker_prefix}*.json"))
    assert len(marker_after) == len(marker_before) + 1
    marker_bytes = {marker: marker.read_bytes() for marker in marker_after}

    phase288_second = _Recorder()
    result = handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=path,
        request=_request(path.parent, approval),
        phase288_function=phase288_second,
    )
    assert result.route == "recovery_required"  # type: ignore[union-attr]
    assert phase288_second.call_count == 0
    assert {marker: marker.read_bytes() for marker in marker_after} == marker_bytes


def test_two_cycles_with_same_start_identity_have_independent_authorization_namespaces(
    tmp_path: Path,
) -> None:
    approval = _approval()
    cycle_a = _persist_lineage(tmp_path / "cycle-a", approval, binding_digest="7" * 64)
    cycle_b = _persist_lineage(tmp_path / "cycle-b", approval, binding_digest="8" * 64)
    phase288_a = _Recorder(_reconciliation())
    phase288_b = _Recorder(_reconciliation())
    first_a = handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=cycle_a,
        request=_request(cycle_a.parent, approval),
        phase288_function=phase288_a,
    )
    first_b = handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=cycle_b,
        request=_request(cycle_b.parent, approval),
        phase288_function=phase288_b,
    )
    assert first_a is phase288_a.result
    assert first_b is phase288_b.result
    assert phase288_a.call_count == phase288_b.call_count == 1

    second_a = handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=cycle_a,
        request=_request(cycle_a.parent, approval),
        phase288_function=_Recorder(),
    )
    second_b = handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=cycle_b,
        request=_request(cycle_b.parent, approval),
        phase288_function=_Recorder(),
    )
    assert second_a.route == second_b.route == "recovery_required"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "previous,recovery",
    [
        ("already_acquired", "reconciliation_mismatch"),
        ("reconciliation_mismatch", "already_acquired"),
    ],
)
def test_provenance_variants_do_not_reconstruct_fresh_authority(
    tmp_path: Path, previous: str, recovery: str
) -> None:
    approval = _approval()
    path = _persist_lineage(
        tmp_path / f"{previous}-{recovery}",
        approval,
        previous=previous,
        recovery=recovery,
        result_kind="none" if recovery == "already_acquired" else "reconciliation",
        result_sha256=None if recovery == "already_acquired" else "4" * 64,
    )
    first = handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=path,
        request=_request(path.parent, approval),
        phase288_function=_Recorder(_reconciliation()),
    )
    second_phase288 = _Recorder()
    second = handoff_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=path,
        request=_request(path.parent, approval),
        phase288_function=second_phase288,
    )
    assert first.status == "matched"  # type: ignore[union-attr]
    assert second.route == "recovery_required"  # type: ignore[union-attr]
    assert second_phase288.call_count == 0


def test_source_audit_excludes_forbidden_lower_boundaries_and_ambient_state() -> None:
    tree = ast.parse(_SOURCE)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_from = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    forbidden_imports = {
        "time",
        "random",
        "uuid",
        "socket",
        "subprocess",
        "os",
        "load_external_publication_operation_start",
        "persist_external_publication_operation_start",
        "acquire_external_publication_operation_start",
        "run_external_publication_reconciliation",
        "resume_external_publication_reconciliation_closure",
        "execute_and_persist_approved_external_publication",
        "persist_external_publication_execution_reconciliation",
        "load_external_publication_execution_reconciliation",
    }
    assert not forbidden_imports.intersection(imported_names | imported_from)
    forbidden_fragments = (
        ".resolve(",
        ".absolute(",
        "realpath(",
        "normpath(",
        "abspath(",
        "samefile(",
        "readlink(",
        "os.environ",
        "os.getenv",
        "socket.",
        "subprocess.",
        "uuid.",
        "time.",
        "random.",
        "persist_",
        "serialize_",
        "load_external_publication_operation_start",
        "load_external_publication_execution_reconciliation",
    )
    assert not any(fragment in _SOURCE for fragment in forbidden_fragments)
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "run_external_publication_reconciliation" not in called_names
    assert "resume_external_publication_reconciliation_closure" not in called_names
    assert "execute_and_persist_approved_external_publication" not in called_names
    assert "acquire_external_publication_operation_start" not in called_names
    assert "persist_external_publication_operation_start" not in called_names
    assert "persist_external_publication_execution_reconciliation" not in called_names
    assert "classify_external_publication" not in _SOURCE
    assert "lifecycle_outcome" not in _SOURCE


def test_engine_exports_exact_phase306_public_symbols() -> None:
    from ai_office import engine

    expected = set(handoff_module.__all__)
    assert expected == {
        "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffCompatibilityError",
        "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffError",
        "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffFailureDetail",
        "run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff",
    }
    for name in expected:
        assert getattr(engine, name) is getattr(handoff_module, name)
