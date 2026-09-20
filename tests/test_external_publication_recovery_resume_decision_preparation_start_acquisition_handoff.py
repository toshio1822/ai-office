# ruff: noqa: E501

"""Provider-free direct regressions for the Phase 304 boundary."""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_resume_decision_preparation_start_acquisition_handoff as handoff_module
from ai_office.engine import (
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartAcquisition,
    ExternalPublicationOperationStartError,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffCompatibilityError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffFailureDetail,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeOutcome,
    ExternalPublicationRecoveryResumeStartAuthorization,
    acquire_external_publication_operation_start,
    authorize_and_persist_external_publication_recovery_resume_decision_preparation_start,
    decide_and_persist_external_publication_recovery_resume,
    external_publication_operation_intent_digest,
    external_publication_operation_start_canonical_bytes,
    external_publication_operation_start_digest,
    external_publication_recovery_resume_decision_digest,
    external_publication_recovery_resume_decision_preparation_digest,
    external_publication_recovery_resume_decision_preparation_intent_binding_digest,
    external_publication_recovery_resume_intent_binding_digest,
    external_publication_recovery_resume_start_authorization_digest,
    load_external_publication_operation_intent,
    materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent,
    persist_external_publication_operation_intent,
    persist_external_publication_recovery_resume_decision_preparation_intent_binding,
    persist_external_publication_recovery_resume_decision_preparation_start_authorization,
    persist_external_publication_recovery_resume_intent_binding,
    persist_external_publication_recovery_resume_outcome,
    persist_external_publication_recovery_resume_start_authorization,
    prepare_and_persist_external_publication_recovery_resume_decision_lineage,
)

_AUTHORIZATION_SCHEMA = (
    "external-publication-recovery-resume-decision-preparation-start-authorization.v1"
)
_BINDING_SCHEMA = (
    "external-publication-recovery-resume-decision-preparation-intent-binding.v1"
)
_INTENT_SCHEMA = "external-publication-operation-intent.v1"
_START_SCHEMA = "external-publication-operation-start.v1"
_AUTHORIZATION_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-start-authorization-"
)
_INTENT_PREFIX = "external-publication-operation-intent-"
_START_PREFIX = "external-publication-recovery-resume-start-"
_BINDING_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-intent-binding-"
)
_PHASE295_BINDING_PREFIX = "external-publication-recovery-resume-intent-binding-"
_PHASE296_AUTHORIZATION_PREFIX = (
    "external-publication-recovery-resume-start-authorization-"
)
_OUTCOME_PREFIX = "external-publication-recovery-resume-outcome-"
_SUFFIX = ".json"
_CompatibilityError = ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffCompatibilityError
_Detail = ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffFailureDetail


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


class _AcquisitionChild(ExternalPublicationOperationStartAcquisition):
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


def _intent(
    *,
    approval: str = "c" * 64,
    plan: str = "d" * 64,
    operation: str = "resume",
) -> ExternalPublicationOperationIntent:
    return ExternalPublicationOperationIntent(
        schema_version=_INTENT_SCHEMA,
        publication_approval_sha256=approval,
        publication_plan_sha256=plan,
        operation=operation,  # type: ignore[arg-type]
    )


def _authorization(
    *,
    intent_digest: str,
    start_digest: str,
    binding_digest: str = "a" * 64,
    approval: str = "c" * 64,
    plan: str = "d" * 64,
    previous: str = "already_acquired",
    recovery: str = "reconciliation_mismatch",
    result_kind: str = "reconciliation",
    result_digest: str | None = "f" * 64,
) -> ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization:
    return ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization(
        schema_version=_AUTHORIZATION_SCHEMA,
        decision_preparation_intent_binding_sha256=binding_digest,
        decision_preparation_sha256="1" * 64,
        recovery_resume_decision_sha256="2" * 64,
        operation_intent_sha256=intent_digest,
        expected_operation_start_sha256=start_digest,
        publication_approval_sha256=approval,
        publication_plan_sha256=plan,
        source_operation="resume",
        previous_recovery_kind=previous,  # type: ignore[arg-type]
        recovery_kind=recovery,  # type: ignore[arg-type]
        result_kind=result_kind,  # type: ignore[arg-type]
        result_sha256=result_digest,
        operation="resume",
        state="authorized",
    )


def _expected_start(
    intent: ExternalPublicationOperationIntent, intent_digest: str
) -> ExternalPublicationOperationStart:
    return ExternalPublicationOperationStart(
        schema_version=_START_SCHEMA,
        operation_intent_sha256=intent_digest,
        publication_approval_sha256=intent.publication_approval_sha256,
        publication_plan_sha256=intent.publication_plan_sha256,
        operation="resume",
        state="started",
    )


def _lineage(
    root: Path,
    *,
    binding_digest: str = "a" * 64,
    intent: ExternalPublicationOperationIntent | None = None,
) -> tuple[
    Path,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
    ExternalPublicationOperationIntent,
    str,
    ExternalPublicationOperationStart,
    str,
]:
    actual_intent = _intent() if intent is None else intent
    intent_digest = external_publication_operation_intent_digest(actual_intent)
    expected_start = _expected_start(actual_intent, intent_digest)
    start_digest = external_publication_operation_start_digest(expected_start)
    authorization = _authorization(
        intent_digest=intent_digest,
        start_digest=start_digest,
        binding_digest=binding_digest,
        approval=actual_intent.publication_approval_sha256,
        plan=actual_intent.publication_plan_sha256,
    )
    authorization_path = root / (f"{_AUTHORIZATION_PREFIX}{binding_digest}{_SUFFIX}")
    return (
        authorization_path,
        authorization,
        actual_intent,
        intent_digest,
        expected_start,
        start_digest,
    )


def _seed_real_phase295_lineage(
    root: Path,
    *,
    previous_recovery_kind: str,
    result_kind: str,
    result_sha256: str | None,
) -> Path:
    """Seed only the local Phase295/296/298 durable predecessor records."""
    root.mkdir()
    anchor = root / "phase-295-binding.json"
    phase295_binding = ExternalPublicationRecoveryResumeIntentBinding(
        schema_version="external-publication-recovery-resume-intent-binding.v1",
        resume_preparation_sha256="a" * 64,
        recovery_decision_sha256="b" * 64,
        publication_approval_sha256="c" * 64,
        publication_plan_sha256="d" * 64,
        operation_intent_sha256="e" * 64,
        source_operation="resume",
        recovery_kind=previous_recovery_kind,  # type: ignore[arg-type]
        operation="resume",
        state="authorized",
    )
    phase295_digest = external_publication_recovery_resume_intent_binding_digest(
        phase295_binding
    )
    start = ExternalPublicationOperationStart(
        schema_version=_START_SCHEMA,
        operation_intent_sha256=phase295_binding.operation_intent_sha256,
        publication_approval_sha256=phase295_binding.publication_approval_sha256,
        publication_plan_sha256=phase295_binding.publication_plan_sha256,
        operation="resume",
        state="started",
    )
    start_digest = external_publication_operation_start_digest(start)
    phase296_authorization = ExternalPublicationRecoveryResumeStartAuthorization(
        schema_version="external-publication-recovery-resume-start-authorization.v1",
        resume_intent_binding_sha256=phase295_digest,
        resume_preparation_sha256=phase295_binding.resume_preparation_sha256,
        recovery_decision_sha256=phase295_binding.recovery_decision_sha256,
        operation_intent_sha256=phase295_binding.operation_intent_sha256,
        expected_operation_start_sha256=start_digest,
        publication_approval_sha256=phase295_binding.publication_approval_sha256,
        publication_plan_sha256=phase295_binding.publication_plan_sha256,
        source_operation=phase295_binding.source_operation,
        recovery_kind=phase295_binding.recovery_kind,
        operation="resume",
        state="authorized",
    )
    phase296_digest = external_publication_recovery_resume_start_authorization_digest(
        phase296_authorization
    )
    phase296_path = root / (
        f"{_PHASE296_AUTHORIZATION_PREFIX}{phase295_digest}{_SUFFIX}"
    )
    start_path = root / f"{_START_PREFIX}{phase296_digest}{_SUFFIX}"
    outcome = ExternalPublicationRecoveryResumeOutcome(
        schema_version="external-publication-recovery-resume-outcome.v1",
        resume_start_authorization_sha256=phase296_digest,
        resume_intent_binding_sha256=phase295_digest,
        operation_intent_sha256=phase295_binding.operation_intent_sha256,
        operation_start_sha256=start_digest,
        publication_approval_sha256=phase295_binding.publication_approval_sha256,
        publication_plan_sha256=phase295_binding.publication_plan_sha256,
        source_operation=phase295_binding.source_operation,
        recovery_kind=phase295_binding.recovery_kind,
        operation="resume",
        state="recovery_required",
        result_kind=result_kind,  # type: ignore[arg-type]
        result_sha256=result_sha256,
    )
    outcome_path = root / f"{_OUTCOME_PREFIX}{phase296_digest}{_SUFFIX}"
    persist_external_publication_recovery_resume_intent_binding(
        anchor, phase295_binding
    )
    persist_external_publication_recovery_resume_start_authorization(
        phase296_path, phase296_authorization
    )
    start_path.write_bytes(external_publication_operation_start_canonical_bytes(start))
    persist_external_publication_recovery_resume_outcome(outcome_path, outcome)
    return anchor


def _assert_error(error: ValueError, classification: str) -> None:
    assert type(error) is _CompatibilityError
    assert isinstance(
        error,
        ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError,
    )
    assert isinstance(error, ValueError)
    assert str(error) == handoff_module._HANDOFF_ERROR_MESSAGE
    assert type(error.detail) is _Detail
    assert error.detail.classification == classification
    assert error.__cause__ is None


def test_public_api_signature_defaults_and_exports() -> None:
    expected = set(handoff_module.__all__)
    import ai_office.engine as engine

    assert expected <= set(engine.__all__)
    for name in expected:
        assert getattr(engine, name) is getattr(handoff_module, name)

    function = handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff
    signature = inspect.signature(function)
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    assert list(signature.parameters) == [
        "start_authorization_path",
        "authorization_loader",
        "authorization_digest_function",
        "intent_loader",
        "intent_digest_function",
        "start_digest_function",
        "phase290_function",
    ]
    assert (
        signature.parameters["authorization_loader"].default
        is handoff_module.load_external_publication_recovery_resume_decision_preparation_start_authorization
    )
    assert (
        signature.parameters["authorization_digest_function"].default
        is handoff_module.external_publication_recovery_resume_decision_preparation_start_authorization_digest
    )
    assert (
        signature.parameters["intent_loader"].default
        is load_external_publication_operation_intent
    )
    assert (
        signature.parameters["intent_digest_function"].default
        is external_publication_operation_intent_digest
    )
    assert (
        signature.parameters["start_digest_function"].default
        is external_publication_operation_start_digest
    )
    assert (
        signature.parameters["phase290_function"].default
        is handoff_module.acquire_external_publication_operation_start
    )
    forbidden = {
        "authorization",
        "authorization_digest",
        "binding_path",
        "intent_path",
        "start_path",
        "acquisition",
        "request",
        "provider",
        "transport",
        "credential",
    }
    assert not forbidden.intersection(signature.parameters)


def test_preflight_rejects_noncallable_dependencies_and_path_subclass_before_load(
    tmp_path: Path,
) -> None:
    loader = _Recorder()
    with pytest.raises(_CompatibilityError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            start_authorization_path=tmp_path / "authorization.json",
            authorization_loader=loader,
            phase290_function=None,  # type: ignore[arg-type]
        )
    _assert_error(caught.value, "configuration")
    assert loader.call_count == 0
    assert list(tmp_path.iterdir()) == []

    with pytest.raises(_CompatibilityError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            start_authorization_path=_PathChild(tmp_path / "authorization.json")
        )
    _assert_error(caught.value, "path_type")
    assert list(tmp_path.iterdir()) == []


def test_authorization_intent_start_and_phase290_are_exactly_once_with_identity(
    tmp_path: Path,
) -> None:
    (
        authorization_path,
        authorization,
        intent,
        intent_digest,
        expected_start,
        start_digest,
    ) = _lineage(tmp_path)
    authorization_loader = _Recorder(authorization)
    authorization_digest = _Recorder("b" * 64)
    intent_loader = _Recorder(intent)
    intent_digest_function = _Recorder(intent_digest)
    start_digest_function = _Recorder(start_digest)
    acquisition = ExternalPublicationOperationStartAcquisition(
        status="acquired", start=expected_start
    )
    phase290 = _Recorder(acquisition)

    result = handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
        start_authorization_path=authorization_path,
        authorization_loader=authorization_loader,
        authorization_digest_function=authorization_digest,
        intent_loader=intent_loader,
        intent_digest_function=intent_digest_function,
        start_digest_function=start_digest_function,
        phase290_function=phase290,
    )

    assert result is acquisition
    assert authorization_loader.call_count == 1
    assert authorization_loader.calls[0][0][0] is authorization_path
    assert authorization_digest.call_count == 1
    assert authorization_digest.calls[0][0][0] is authorization
    assert intent_loader.call_count == 1
    assert intent_loader.calls[0][0] == (
        tmp_path / f"{_INTENT_PREFIX}{intent_digest}{_SUFFIX}",
    )
    assert intent_digest_function.call_count == 1
    assert intent_digest_function.calls[0][0][0] is intent
    assert start_digest_function.call_count == 1
    assert start_digest_function.calls[0][0][0] == expected_start
    assert start_digest_function.calls[0][0][0] is not expected_start
    assert phase290.call_count == 1
    assert phase290.calls[0][0] == ()
    assert phase290.calls[0][1] == {
        "intent_path": tmp_path / f"{_INTENT_PREFIX}{intent_digest}{_SUFFIX}",
        "start_path": tmp_path / f"{_START_PREFIX}{'b' * 64}{_SUFFIX}",
    }
    assert not list(tmp_path.glob(f"{_START_PREFIX}*.json"))


@pytest.mark.parametrize("status", ["acquired", "already_acquired"])
def test_both_phase290_stop_statuses_return_exact_result_identity(
    tmp_path: Path, status: str
) -> None:
    (
        authorization_path,
        authorization,
        intent,
        intent_digest,
        expected_start,
        start_digest,
    ) = _lineage(tmp_path)
    acquisition = ExternalPublicationOperationStartAcquisition(
        status=status,
        start=expected_start,  # type: ignore[arg-type]
    )
    phase290 = _Recorder(acquisition)

    result = handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
        start_authorization_path=authorization_path,
        authorization_loader=_Recorder(authorization),
        authorization_digest_function=_Recorder("b" * 64),
        intent_loader=_Recorder(intent),
        intent_digest_function=_Recorder(intent_digest),
        start_digest_function=_Recorder(start_digest),
        phase290_function=phase290,
    )

    assert result is acquisition
    assert result.status == status
    assert phase290.call_count == 1


def test_authorization_filename_mismatch_precedes_digest_intent_and_phase290(
    tmp_path: Path,
) -> None:
    authorization_path, authorization, intent, intent_digest, _, start_digest = (
        _lineage(tmp_path)
    )
    wrong_path = tmp_path / "wrong-authorization-name.json"
    authorization_digest = _Recorder("b" * 64)
    intent_loader = _Recorder(intent)
    start_digest_function = _Recorder(start_digest)
    phase290 = _Recorder()

    with pytest.raises(_CompatibilityError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            start_authorization_path=wrong_path,
            authorization_loader=_Recorder(authorization),
            authorization_digest_function=authorization_digest,
            intent_loader=intent_loader,
            intent_digest_function=_Recorder(intent_digest),
            start_digest_function=start_digest_function,
            phase290_function=phase290,
        )
    _assert_error(caught.value, "authorization_path")
    assert authorization_digest.call_count == 0
    assert intent_loader.call_count == 0
    assert start_digest_function.call_count == 0
    assert phase290.call_count == 0


def test_authorization_local_reconstruction_rejects_forged_exact_instance_before_digest(
    tmp_path: Path,
) -> None:
    authorization_path, authorization, intent, intent_digest, _, start_digest = (
        _lineage(tmp_path)
    )
    forged = _forged(authorization, operation="fresh")
    authorization_digest = _Recorder("b" * 64)
    intent_loader = _Recorder(intent)
    phase290 = _Recorder()

    with pytest.raises(_CompatibilityError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            start_authorization_path=authorization_path,
            authorization_loader=_Recorder(forged),
            authorization_digest_function=authorization_digest,
            intent_loader=intent_loader,
            intent_digest_function=_Recorder(intent_digest),
            start_digest_function=_Recorder(start_digest),
            phase290_function=phase290,
        )
    _assert_error(caught.value, "authorization_contract")
    assert authorization_digest.call_count == 0
    assert intent_loader.call_count == 0
    assert phase290.call_count == 0


@pytest.mark.parametrize("dependency", ["loader", "digest"])
def test_known_authorization_errors_preserve_identity_and_stop_downstream(
    tmp_path: Path, dependency: str
) -> None:
    authorization_path, authorization, intent, intent_digest, _, start_digest = (
        _lineage(tmp_path)
    )
    error = ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError(
        "load"
    )
    authorization_loader = _Recorder(authorization)
    authorization_digest = _Recorder("b" * 64)
    if dependency == "loader":
        authorization_loader = _Recorder(fault=error)
    else:
        authorization_digest = _Recorder(fault=error)
    intent_loader = _Recorder(intent)
    phase290 = _Recorder()

    with pytest.raises(type(error)) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            start_authorization_path=authorization_path,
            authorization_loader=authorization_loader,
            authorization_digest_function=authorization_digest,
            intent_loader=intent_loader,
            intent_digest_function=_Recorder(intent_digest),
            start_digest_function=_Recorder(start_digest),
            phase290_function=phase290,
        )
    assert caught.value is error
    assert intent_loader.call_count == 0
    assert phase290.call_count == 0


@pytest.mark.parametrize("bad_digest", ["not-a-digest", "A" * 64, "9" * 63])
def test_malformed_authorization_digest_fails_closed(
    tmp_path: Path, bad_digest: str
) -> None:
    authorization_path, authorization, intent, intent_digest, _, start_digest = (
        _lineage(tmp_path)
    )
    phase290 = _Recorder()
    with pytest.raises(_CompatibilityError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            start_authorization_path=authorization_path,
            authorization_loader=_Recorder(authorization),
            authorization_digest_function=_Recorder(bad_digest),
            intent_loader=_Recorder(intent),
            intent_digest_function=_Recorder(intent_digest),
            start_digest_function=_Recorder(start_digest),
            phase290_function=phase290,
        )
    _assert_error(caught.value, "authorization_digest")
    assert phase290.call_count == 0


def test_intent_loader_exact_model_and_local_reconstruction_fail_closed(
    tmp_path: Path,
) -> None:
    authorization_path, authorization, intent, intent_digest, _, start_digest = (
        _lineage(tmp_path)
    )
    forged = _forged(intent, operation="fresh")
    intent_digest_function = _Recorder(intent_digest)
    phase290 = _Recorder()
    with pytest.raises(_CompatibilityError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            start_authorization_path=authorization_path,
            authorization_loader=_Recorder(authorization),
            authorization_digest_function=_Recorder("b" * 64),
            intent_loader=_Recorder(forged),
            intent_digest_function=intent_digest_function,
            start_digest_function=_Recorder(start_digest),
            phase290_function=phase290,
        )
    _assert_error(caught.value, "intent_contract")
    assert intent_digest_function.call_count == 0
    assert phase290.call_count == 0


@pytest.mark.parametrize("mismatch", ["digest", "approval", "plan", "operation"])
def test_intent_digest_approval_plan_operation_mismatch_precedes_start_and_phase290(
    tmp_path: Path, mismatch: str
) -> None:
    (
        authorization_path,
        authorization,
        expected_intent,
        intent_digest,
        _,
        start_digest,
    ) = _lineage(tmp_path)
    if mismatch == "digest":
        intent = expected_intent
        returned_digest = "9" * 64
    elif mismatch == "approval":
        intent = _intent(approval="8" * 64)
        returned_digest = intent_digest
    elif mismatch == "plan":
        intent = _intent(plan="8" * 64)
        returned_digest = intent_digest
    else:
        intent = _intent(operation="fresh")
        returned_digest = intent_digest
    intent_digest_function = _Recorder(returned_digest)
    start_digest_function = _Recorder(start_digest)
    phase290 = _Recorder()

    with pytest.raises(_CompatibilityError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            start_authorization_path=authorization_path,
            authorization_loader=_Recorder(authorization),
            authorization_digest_function=_Recorder("b" * 64),
            intent_loader=_Recorder(intent),
            intent_digest_function=intent_digest_function,
            start_digest_function=start_digest_function,
            phase290_function=phase290,
        )
    expected_classification = (
        "intent_contract" if mismatch == "operation" else "intent_lineage"
    )
    _assert_error(caught.value, expected_classification)
    assert start_digest_function.call_count == 0
    assert phase290.call_count == 0


def test_expected_start_digest_mismatch_precedes_phase290(
    tmp_path: Path,
) -> None:
    authorization_path, authorization, intent, intent_digest, expected_start, _ = (
        _lineage(tmp_path)
    )
    start_digest_function = _Recorder("9" * 64)
    phase290 = _Recorder()

    with pytest.raises(_CompatibilityError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            start_authorization_path=authorization_path,
            authorization_loader=_Recorder(authorization),
            authorization_digest_function=_Recorder("b" * 64),
            intent_loader=_Recorder(intent),
            intent_digest_function=_Recorder(intent_digest),
            start_digest_function=start_digest_function,
            phase290_function=phase290,
        )
    _assert_error(caught.value, "start_digest")
    assert start_digest_function.calls[0][0][0] == expected_start
    assert start_digest_function.calls[0][0][0] is not expected_start
    assert phase290.call_count == 0


@pytest.mark.parametrize(
    "dependency", ["intent_loader", "intent_digest", "start_digest"]
)
def test_known_intent_and_start_errors_preserve_identity(
    tmp_path: Path, dependency: str
) -> None:
    authorization_path, authorization, intent, intent_digest, _, start_digest = (
        _lineage(tmp_path)
    )
    if dependency in {"intent_loader", "intent_digest"}:
        error: BaseException = ExternalPublicationOperationIntentError("load")
    else:
        error = ExternalPublicationOperationStartError("digest")
    kwargs: dict[str, object] = {
        "start_authorization_path": authorization_path,
        "authorization_loader": _Recorder(authorization),
        "authorization_digest_function": _Recorder("b" * 64),
        "intent_loader": _Recorder(intent),
        "intent_digest_function": _Recorder(intent_digest),
        "start_digest_function": _Recorder(start_digest),
        "phase290_function": _Recorder(),
    }
    key = {
        "intent_loader": "intent_loader",
        "intent_digest": "intent_digest_function",
        "start_digest": "start_digest_function",
    }[dependency]
    kwargs[key] = _Recorder(fault=error)

    with pytest.raises(type(error)) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            **kwargs  # type: ignore[arg-type]
        )
    assert caught.value is error
    assert kwargs["phase290_function"].call_count == 0  # type: ignore[union-attr]


@pytest.mark.parametrize("dependency", ["authorization_loader", "phase290_function"])
def test_unexpected_dependency_errors_are_sanitized(
    tmp_path: Path, dependency: str
) -> None:
    authorization_path, authorization, intent, intent_digest, _, start_digest = (
        _lineage(tmp_path)
    )
    phase290 = _Recorder()
    kwargs: dict[str, object] = {
        "start_authorization_path": authorization_path,
        "authorization_loader": _Recorder(authorization),
        "authorization_digest_function": _Recorder("b" * 64),
        "intent_loader": _Recorder(intent),
        "intent_digest_function": _Recorder(intent_digest),
        "start_digest_function": _Recorder(start_digest),
        "phase290_function": phase290,
    }
    if dependency == "authorization_loader":
        kwargs[dependency] = _Recorder(fault=RuntimeError("sensitive path"))
    else:
        kwargs[dependency] = _Recorder(fault=RuntimeError("sensitive provider"))

    with pytest.raises(_CompatibilityError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            **kwargs  # type: ignore[arg-type]
        )
    _assert_error(caught.value, "dependency_error")
    assert "sensitive" not in str(caught.value)
    called_phase290 = kwargs["phase290_function"]
    assert called_phase290.call_count == (  # type: ignore[union-attr]
        0 if dependency == "authorization_loader" else 1
    )


def test_phase290_known_error_preserves_identity_and_is_not_retried(
    tmp_path: Path,
) -> None:
    authorization_path, authorization, intent, intent_digest, _, start_digest = (
        _lineage(tmp_path)
    )
    error = ExternalPublicationOperationStartError("ambiguous")
    phase290 = _Recorder(fault=error)

    with pytest.raises(type(error)) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            start_authorization_path=authorization_path,
            authorization_loader=_Recorder(authorization),
            authorization_digest_function=_Recorder("b" * 64),
            intent_loader=_Recorder(intent),
            intent_digest_function=_Recorder(intent_digest),
            start_digest_function=_Recorder(start_digest),
            phase290_function=phase290,
        )
    assert caught.value is error
    assert phase290.call_count == 1
    assert not list(tmp_path.glob(f"{_START_PREFIX}*.json"))


def test_malformed_phase290_result_fails_closed_without_cleanup_or_retry(
    tmp_path: Path,
) -> None:
    (
        authorization_path,
        authorization,
        intent,
        intent_digest,
        expected_start,
        start_digest,
    ) = _lineage(tmp_path)
    malformed = _forged(
        ExternalPublicationOperationStartAcquisition(
            status="acquired", start=expected_start
        ),
        status="finished",
    )
    phase290 = _Recorder(malformed)

    with pytest.raises(_CompatibilityError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            start_authorization_path=authorization_path,
            authorization_loader=_Recorder(authorization),
            authorization_digest_function=_Recorder("b" * 64),
            intent_loader=_Recorder(intent),
            intent_digest_function=_Recorder(intent_digest),
            start_digest_function=_Recorder(start_digest),
            phase290_function=phase290,
        )
    _assert_error(caught.value, "result_contract")
    assert phase290.call_count == 1
    assert not list(tmp_path.glob(f"{_START_PREFIX}*.json"))


def test_durable_phase290_marker_survives_phase304_result_validation_failure(
    tmp_path: Path,
) -> None:
    (
        authorization_path,
        authorization,
        intent,
        intent_digest,
        expected_start,
        start_digest,
    ) = _lineage(tmp_path)
    intent_path = tmp_path / f"{_INTENT_PREFIX}{intent_digest}{_SUFFIX}"
    persist_external_publication_operation_intent(intent_path, intent)
    persist_external_publication_recovery_resume_decision_preparation_start_authorization(
        authorization_path, authorization
    )
    authorization_digest = handoff_module.external_publication_recovery_resume_decision_preparation_start_authorization_digest(
        authorization
    )
    start_path = tmp_path / f"{_START_PREFIX}{authorization_digest}{_SUFFIX}"
    expected_marker_bytes = external_publication_operation_start_canonical_bytes(
        expected_start
    )
    wrapper_calls: list[tuple[Path, Path]] = []
    actual_results: list[ExternalPublicationOperationStartAcquisition] = []
    marker_bytes_created_by_phase290: list[bytes] = []

    def phase290_wrapper(*, intent_path: Path, start_path: Path) -> object:
        wrapper_calls.append((intent_path, start_path))
        actual = acquire_external_publication_operation_start(
            intent_path=intent_path,
            start_path=start_path,
        )
        actual_results.append(actual)
        marker_bytes_created_by_phase290.append(start_path.read_bytes())
        return _forged(actual, status="finished")

    with pytest.raises(_CompatibilityError) as caught:
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            start_authorization_path=authorization_path,
            phase290_function=phase290_wrapper,
        )
    _assert_error(caught.value, "result_contract")

    assert wrapper_calls == [(intent_path, start_path)]
    assert len(actual_results) == 1
    assert len(marker_bytes_created_by_phase290) == 1
    assert actual_results[0].status == "acquired"
    assert actual_results[0].start == expected_start
    assert (
        external_publication_operation_start_digest(actual_results[0].start)
        == start_digest
    )
    assert start_path.exists()
    marker_bytes_after_failure = start_path.read_bytes()
    assert marker_bytes_after_failure == marker_bytes_created_by_phase290[0]
    assert marker_bytes_after_failure == expected_marker_bytes
    assert (
        marker_bytes_after_failure
        == external_publication_operation_start_canonical_bytes(expected_start)
    )

    retry_result = acquire_external_publication_operation_start(
        intent_path=intent_path,
        start_path=start_path,
    )
    assert retry_result.status == "already_acquired"
    assert retry_result.start == expected_start
    assert start_path.read_bytes() == marker_bytes_after_failure
    assert not any("execution" in path.name for path in tmp_path.iterdir())
    assert not any("reconciliation" in path.name for path in tmp_path.iterdir())
    assert not any("provider" in path.name for path in tmp_path.iterdir())


def test_result_start_mismatch_and_subclass_are_rejected_after_one_phase290_call(
    tmp_path: Path,
) -> None:
    (
        authorization_path,
        authorization,
        intent,
        intent_digest,
        expected_start,
        start_digest,
    ) = _lineage(tmp_path)
    different_start = dataclasses.replace(
        expected_start, publication_plan_sha256="8" * 64
    )
    mismatch = ExternalPublicationOperationStartAcquisition(
        status="already_acquired", start=different_start
    )
    for malformed in (
        mismatch,
        _forged_subclass(
            ExternalPublicationOperationStartAcquisition(
                status="acquired", start=expected_start
            )
        ),
    ):
        phase290 = _Recorder(malformed)
        with pytest.raises(_CompatibilityError) as caught:
            handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
                start_authorization_path=authorization_path,
                authorization_loader=_Recorder(authorization),
                authorization_digest_function=_Recorder("b" * 64),
                intent_loader=_Recorder(intent),
                intent_digest_function=_Recorder(intent_digest),
                start_digest_function=_Recorder(start_digest),
                phase290_function=phase290,
            )
        _assert_error(caught.value, "result_contract")
        assert phase290.call_count == 1


def _forged_subclass(source: ExternalPublicationOperationStartAcquisition) -> object:
    value = object.__new__(_AcquisitionChild)
    object.__setattr__(value, "status", source.status)
    object.__setattr__(value, "start", source.start)
    return value


def test_cycle_separation_uses_authorization_digest_for_distinct_acquired_targets(
    tmp_path: Path,
) -> None:
    intent = _intent()
    intent_digest = external_publication_operation_intent_digest(intent)
    intent_path = tmp_path / f"{_INTENT_PREFIX}{intent_digest}{_SUFFIX}"
    persist_external_publication_operation_intent(intent_path, intent)
    authorization_path_a, authorization_a, _, _, expected_start, start_digest = (
        _lineage(tmp_path, binding_digest="a" * 64, intent=intent)
    )
    authorization_path_b, authorization_b, _, _, _, _ = _lineage(
        tmp_path, binding_digest="b" * 64, intent=intent
    )
    persist_external_publication_recovery_resume_decision_preparation_start_authorization(
        authorization_path_a, authorization_a
    )
    persist_external_publication_recovery_resume_decision_preparation_start_authorization(
        authorization_path_b, authorization_b
    )

    result_a = handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
        start_authorization_path=authorization_path_a
    )
    result_b = handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
        start_authorization_path=authorization_path_b
    )

    authorization_digest_a = handoff_module.external_publication_recovery_resume_decision_preparation_start_authorization_digest(
        authorization_a
    )
    authorization_digest_b = handoff_module.external_publication_recovery_resume_decision_preparation_start_authorization_digest(
        authorization_b
    )
    assert (
        intent_digest
        == authorization_a.operation_intent_sha256
        == authorization_b.operation_intent_sha256
    )
    assert start_digest == external_publication_operation_start_digest(expected_start)
    assert authorization_digest_a != authorization_digest_b
    assert result_a.status == "acquired"
    assert result_b.status == "acquired"
    assert result_a.start == result_b.start == expected_start
    assert (tmp_path / f"{_START_PREFIX}{authorization_digest_a}{_SUFFIX}").exists()
    assert (tmp_path / f"{_START_PREFIX}{authorization_digest_b}{_SUFFIX}").exists()


def _phase302_binding(
    intent: ExternalPublicationOperationIntent,
    *,
    previous: str,
    recovery: str,
    result_kind: str,
    result_digest: str | None,
) -> ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding:
    intent_digest = external_publication_operation_intent_digest(intent)
    return ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding(
        schema_version=_BINDING_SCHEMA,
        decision_preparation_sha256="1" * 64,
        recovery_resume_decision_sha256="2" * 64,
        publication_approval_sha256=intent.publication_approval_sha256,
        publication_plan_sha256=intent.publication_plan_sha256,
        operation_intent_sha256=intent_digest,
        source_operation="resume",
        previous_recovery_kind=previous,  # type: ignore[arg-type]
        recovery_kind=recovery,  # type: ignore[arg-type]
        result_kind=result_kind,  # type: ignore[arg-type]
        result_sha256=result_digest,
        operation="resume",
        state="authorized",
    )


@pytest.mark.parametrize(
    "previous,result_kind,result_digest,recovery",
    [
        ("already_acquired", "reconciliation", "7" * 64, "reconciliation_mismatch"),
        ("reconciliation_mismatch", "none", None, "already_acquired"),
    ],
)
def test_real_phase303_authorization_phase304_phase290_acquired_then_already(
    tmp_path: Path,
    previous: str,
    result_kind: str,
    result_digest: str | None,
    recovery: str,
) -> None:
    root = tmp_path / f"{previous}-{result_kind}"
    root.mkdir()
    intent = _intent()
    intent_digest = external_publication_operation_intent_digest(intent)
    intent_path = root / f"{_INTENT_PREFIX}{intent_digest}{_SUFFIX}"
    persist_external_publication_operation_intent(intent_path, intent)
    binding = _phase302_binding(
        intent,
        previous=previous,
        recovery=recovery,
        result_kind=result_kind,
        result_digest=result_digest,
    )
    binding_digest = (
        external_publication_recovery_resume_decision_preparation_intent_binding_digest(
            binding
        )
    )
    binding_path = root / f"{_BINDING_PREFIX}{'1' * 64}{_SUFFIX}"
    persist_external_publication_recovery_resume_decision_preparation_intent_binding(
        binding_path, binding
    )

    authorization = authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
        decision_preparation_intent_binding_path=binding_path
    )
    authorization_path = root / f"{_AUTHORIZATION_PREFIX}{binding_digest}{_SUFFIX}"
    assert authorization_path.exists()
    assert authorization.decision_preparation_intent_binding_sha256 == binding_digest
    assert authorization.previous_recovery_kind == previous
    assert authorization.recovery_kind == recovery

    first = handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
        start_authorization_path=authorization_path
    )
    second = handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
        start_authorization_path=authorization_path
    )
    start_digest = authorization.expected_operation_start_sha256
    authorization_digest = handoff_module.external_publication_recovery_resume_decision_preparation_start_authorization_digest(
        authorization
    )

    assert first.status == "acquired"
    assert second.status == "already_acquired"
    assert first.start == second.start
    assert (root / f"{_START_PREFIX}{authorization_digest}{_SUFFIX}").exists()
    assert first.start.operation_intent_sha256 == intent_digest
    assert external_publication_operation_start_digest(first.start) == start_digest
    assert not list(root.glob("*execution*"))
    assert not list(root.glob("*reconciliation*evidence*"))


def test_real_phase295_to_phase304_path_has_no_execution_and_phase290_is_direct(
    tmp_path: Path,
) -> None:
    """Exercise local Phase295/298 seed plus real Phase299-304 boundaries."""
    root = tmp_path / "full-local-lineage"
    root.mkdir()
    intent = _intent()
    intent_digest = external_publication_operation_intent_digest(intent)
    intent_path = root / f"{_INTENT_PREFIX}{intent_digest}{_SUFFIX}"
    persist_external_publication_operation_intent(intent_path, intent)
    binding = _phase302_binding(
        intent,
        previous="already_acquired",
        recovery="reconciliation_mismatch",
        result_kind="reconciliation",
        result_digest="7" * 64,
    )
    binding_digest = (
        external_publication_recovery_resume_decision_preparation_intent_binding_digest(
            binding
        )
    )
    binding_path = root / f"{_BINDING_PREFIX}{'1' * 64}{_SUFFIX}"
    persist_external_publication_recovery_resume_decision_preparation_intent_binding(
        binding_path, binding
    )
    before = {path.name for path in root.iterdir()}
    authorization = authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
        decision_preparation_intent_binding_path=binding_path
    )
    authorization_path = root / f"{_AUTHORIZATION_PREFIX}{binding_digest}{_SUFFIX}"
    after_phase303 = {path.name for path in root.iterdir()}
    first = handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
        start_authorization_path=authorization_path
    )
    after_phase304 = {path.name for path in root.iterdir()}

    assert after_phase303 == before | {authorization_path.name}
    assert first.status == "acquired"
    assert after_phase304 == after_phase303 | {
        f"{_START_PREFIX}{handoff_module.external_publication_recovery_resume_decision_preparation_start_authorization_digest(authorization)}{_SUFFIX}"
    }
    assert not any("execution" in name for name in after_phase304)
    assert not any("reconciliation-evidence" in name for name in after_phase304)


def test_real_phase295_to_phase304_lineage_preserves_provenance_and_stop_statuses(
    tmp_path: Path,
) -> None:
    """Run the actual Phase295 -> Phase303 -> Phase304 -> Phase290 chain."""
    cases = (
        (
            "already_acquired",
            "reconciliation",
            "7" * 64,
            "reconciliation_mismatch",
        ),
        ("reconciliation_mismatch", "none", None, "already_acquired"),
    )
    for previous, result_kind, result_sha256, current in cases:
        root = tmp_path / f"real-{previous}-{result_kind}"
        anchor = _seed_real_phase295_lineage(
            root,
            previous_recovery_kind=previous,
            result_kind=result_kind,
            result_sha256=result_sha256,
        )
        decision = decide_and_persist_external_publication_recovery_resume(
            resume_intent_binding_path=anchor,
            decision="authorize_resume_preparation",
            decided_by="operator@example.test",
            decision_id=f"phase304-{previous}-{result_kind}",
        )
        preparation = (
            prepare_and_persist_external_publication_recovery_resume_decision_lineage(
                resume_intent_binding_path=anchor
            )
        )
        phase302_binding = materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent(
            resume_intent_binding_path=anchor
        )

        decision_digest = external_publication_recovery_resume_decision_digest(decision)
        preparation_digest = (
            external_publication_recovery_resume_decision_preparation_digest(
                preparation
            )
        )
        binding_digest = external_publication_recovery_resume_decision_preparation_intent_binding_digest(
            phase302_binding
        )
        binding_path = root / f"{_BINDING_PREFIX}{preparation_digest}{_SUFFIX}"
        intent_digest = phase302_binding.operation_intent_sha256
        intent_path = root / f"{_INTENT_PREFIX}{intent_digest}{_SUFFIX}"
        expected_intent = load_external_publication_operation_intent(intent_path)
        expected_start = _expected_start(expected_intent, intent_digest)
        start_digest = external_publication_operation_start_digest(expected_start)

        before_phase303 = {path.name for path in root.iterdir()}
        before_start_markers = {
            path.name for path in root.iterdir() if path.name.startswith(_START_PREFIX)
        }
        authorization = authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
            decision_preparation_intent_binding_path=binding_path
        )
        after_phase303 = {path.name for path in root.iterdir()}
        after_phase303_start_markers = {
            path.name for path in root.iterdir() if path.name.startswith(_START_PREFIX)
        }
        authorization_path = root / (
            f"{_AUTHORIZATION_PREFIX}{binding_digest}{_SUFFIX}"
        )

        assert (
            authorization.decision_preparation_intent_binding_sha256 == binding_digest
        )
        assert authorization.decision_preparation_sha256 == preparation_digest
        assert authorization.recovery_resume_decision_sha256 == decision_digest
        assert authorization.operation_intent_sha256 == intent_digest
        assert authorization.expected_operation_start_sha256 == start_digest
        assert authorization.previous_recovery_kind == previous
        assert authorization.recovery_kind == current
        assert authorization.result_kind == result_kind
        assert authorization.result_sha256 == result_sha256
        assert after_phase303 == before_phase303 | {authorization_path.name}
        assert after_phase303_start_markers == before_start_markers

        first = handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            start_authorization_path=authorization_path
        )
        second = handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
            start_authorization_path=authorization_path
        )
        authorization_digest = handoff_module.external_publication_recovery_resume_decision_preparation_start_authorization_digest(
            authorization
        )
        start_path = root / f"{_START_PREFIX}{authorization_digest}{_SUFFIX}"

        assert first.status == "acquired"
        assert second.status == "already_acquired"
        assert first.start == second.start
        assert start_path.exists()
        assert first.start.operation_intent_sha256 == intent_digest
        assert external_publication_operation_start_digest(first.start) == start_digest
        assert not any("execution" in name for name in {p.name for p in root.iterdir()})


def test_source_audit_excludes_forbidden_boundaries_and_ambient_state() -> None:
    source = Path(handoff_module.__file__).read_text()
    tree = ast.parse(source)
    imported = {
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
        "run_external_publication_recovery_resume_start_handoff",
        "run_external_publication_operation_start_handoff",
        "run_external_publication_operation",
        "reconcile_external_publication_execution",
    }
    assert not forbidden_imports.intersection(imported | imported_from)
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
    )
    assert not any(fragment in source for fragment in forbidden_fragments)
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "load_external_publication_operation_start" not in called_names
    assert "persist_external_publication_operation_start" not in called_names
    assert "run_external_publication_recovery_resume_start_handoff" not in called_names
    assert "run_external_publication_operation_start_handoff" not in called_names
    assert "reconcile_external_publication_execution" not in called_names
