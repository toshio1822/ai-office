# ruff: noqa: E501

"""Provider-free regressions for the Phase 308 routing boundary."""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_resume_decision_preparation_reconciliation_outcome_routing as routing_module
from ai_office.engine import (
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationError,
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartError,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeCompatibilityError,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingCompatibilityError,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationCompatibilityError,
    external_publication_execution_reconciliation_canonical_bytes,
    external_publication_execution_reconciliation_digest,
    external_publication_operation_start_canonical_bytes,
    external_publication_operation_start_digest,
    external_publication_recovery_resume_decision_preparation_intent_binding_digest,
    external_publication_recovery_resume_decision_preparation_reconciliation_outcome_digest,
    external_publication_recovery_resume_decision_preparation_start_authorization_digest,
    load_external_publication_execution_reconciliation,
    load_external_publication_operation_start,
    load_external_publication_recovery_resume_decision_preparation_intent_binding,
    load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome,
    load_external_publication_recovery_resume_decision_preparation_start_authorization,
    persist_external_publication_execution_reconciliation,
    persist_external_publication_recovery_resume_decision_preparation_intent_binding,
    persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome,
    persist_external_publication_recovery_resume_decision_preparation_start_authorization,
    route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome,
)

_BINDING_SCHEMA = (
    "external-publication-recovery-resume-decision-preparation-intent-binding.v1"
)
_AUTHORIZATION_SCHEMA = (
    "external-publication-recovery-resume-decision-preparation-start-authorization.v1"
)
_START_SCHEMA = "external-publication-operation-start.v1"
_EVIDENCE_SCHEMA = "external-publication-execution-reconciliation.v1"
_OUTCOME_SCHEMA = "external-publication-recovery-resume-decision-preparation-reconciliation-outcome.v1"
_DECISION_SCHEMA = (
    "external-publication-recovery-resume-decision-preparation-reconciliation-"
    "decision-required.v1"
)
_BINDING_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-intent-binding-"
)
_AUTHORIZATION_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-start-authorization-"
)
_START_PREFIX = "external-publication-recovery-resume-start-"
_RECONCILIATION_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-reconciliation-"
)
_OUTCOME_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-reconciliation-outcome-"
)
_SUFFIX = ".json"
_ROUTING_MESSAGE = (
    "external publication recovery resume decision preparation reconciliation "
    "outcome routing is blocked"
)


class _Recorder:
    def __init__(
        self,
        *,
        result: object = None,
        digest: object = None,
        fault: BaseException | None = None,
        delegate: object | None = None,
    ) -> None:
        self.result = result
        self.digest = digest
        self.fault = fault
        self.delegate = delegate
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.results: list[object] = []

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        if self.fault is not None:
            raise self.fault
        if self.delegate is not None:
            result = self.delegate(*args, **kwargs)  # type: ignore[operator]
        elif self.digest is not None:
            result = self.digest
        else:
            result = self.result
        self.results.append(result)
        return result

    @property
    def call_count(self) -> int:
        return len(self.calls)


class _PathChild(type(Path())):
    pass


class _BindingChild(ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding):
    pass


class _AuthorizationChild(
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization
):
    pass


class _OutcomeChild(
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome
):
    pass


class _StartChild(ExternalPublicationOperationStart):
    pass


class _StringChild(str):
    pass


@dataclasses.dataclass(frozen=True)
class _Lineage:
    root: Path
    binding_path: Path
    binding: ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding
    binding_digest: str
    authorization_path: Path
    authorization: (
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization
    )
    authorization_digest: str
    start_path: Path
    start: ExternalPublicationOperationStart
    start_digest: str
    reconciliation_path: Path
    reconciliation: ExternalPublicationExecutionReconciliation | None
    reconciliation_digest: str | None
    outcome_path: Path
    outcome: ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome


def _binding(
    *,
    preparation_digest: str = "1" * 64,
    result_kind: str = "reconciliation",
    result_sha256: str | None = "f" * 64,
    recovery_kind: str = "reconciliation_mismatch",
    previous_recovery_kind: str = "already_acquired",
) -> ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding:
    return ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding(
        schema_version=_BINDING_SCHEMA,
        decision_preparation_sha256=preparation_digest,
        recovery_resume_decision_sha256="2" * 64,
        publication_approval_sha256="3" * 64,
        publication_plan_sha256="4" * 64,
        operation_intent_sha256="5" * 64,
        source_operation="resume",
        previous_recovery_kind=previous_recovery_kind,  # type: ignore[arg-type]
        recovery_kind=recovery_kind,  # type: ignore[arg-type]
        result_kind=result_kind,  # type: ignore[arg-type]
        result_sha256=result_sha256,
        operation="resume",
        state="authorized",
    )


def _start() -> ExternalPublicationOperationStart:
    return ExternalPublicationOperationStart(
        schema_version=_START_SCHEMA,
        operation_intent_sha256="5" * 64,
        publication_approval_sha256="3" * 64,
        publication_plan_sha256="4" * 64,
        operation="resume",
        state="started",
    )


def _authorization(
    *,
    binding_digest: str,
    start_digest: str,
    preparation_digest: str = "1" * 64,
    result_kind: str = "reconciliation",
    result_sha256: str | None = "f" * 64,
    recovery_kind: str = "reconciliation_mismatch",
    previous_recovery_kind: str = "already_acquired",
) -> ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization:
    return ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization(
        schema_version=_AUTHORIZATION_SCHEMA,
        decision_preparation_intent_binding_sha256=binding_digest,
        decision_preparation_sha256=preparation_digest,
        recovery_resume_decision_sha256="2" * 64,
        operation_intent_sha256="5" * 64,
        expected_operation_start_sha256=start_digest,
        publication_approval_sha256="3" * 64,
        publication_plan_sha256="4" * 64,
        source_operation="resume",
        previous_recovery_kind=previous_recovery_kind,  # type: ignore[arg-type]
        recovery_kind=recovery_kind,  # type: ignore[arg-type]
        result_kind=result_kind,  # type: ignore[arg-type]
        result_sha256=result_sha256,
        operation="resume",
        state="authorized",
    )


def _reconciliation(
    status: str = "lineage_mismatch",
) -> ExternalPublicationExecutionReconciliation:
    return ExternalPublicationExecutionReconciliation(
        schema_version=_EVIDENCE_SCHEMA,
        claim_sha256="a" * 64,
        execution_evidence_sha256="b" * 64,
        status=status,  # type: ignore[arg-type]
        mismatched_fields=("provider",) if status == "lineage_mismatch" else (),
    )


def _outcome(
    *,
    authorization_digest: str,
    binding_digest: str,
    start_digest: str,
    preparation_digest: str = "1" * 64,
    state: str = "recovery_required",
    result_kind: str = "reconciliation",
    result_sha256: str | None = "f" * 64,
    recovery_kind: str = "reconciliation_mismatch",
    previous_recovery_kind: str = "already_acquired",
) -> ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome:
    return ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome(
        schema_version=_OUTCOME_SCHEMA,
        decision_preparation_start_authorization_sha256=authorization_digest,
        decision_preparation_intent_binding_sha256=binding_digest,
        decision_preparation_sha256=preparation_digest,
        recovery_resume_decision_sha256="2" * 64,
        operation_intent_sha256="5" * 64,
        operation_start_sha256=start_digest,
        publication_approval_sha256="3" * 64,
        publication_plan_sha256="4" * 64,
        source_operation="resume",
        previous_recovery_kind=previous_recovery_kind,  # type: ignore[arg-type]
        recovery_kind=recovery_kind,  # type: ignore[arg-type]
        operation="resume",
        state=state,  # type: ignore[arg-type]
        result_kind=result_kind,  # type: ignore[arg-type]
        result_sha256=result_sha256,
    )


def _seed(
    tmp_path: Path,
    *,
    preparation_digest: str = "1" * 64,
    state: str = "recovery_required",
    result_kind: str = "reconciliation",
    evidence_status: str = "lineage_mismatch",
    previous_recovery_kind: str = "already_acquired",
) -> _Lineage:
    root = tmp_path / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    recovery_kind = (
        "reconciliation_mismatch"
        if result_kind == "reconciliation"
        else "already_acquired"
    )
    evidence = (
        _reconciliation(evidence_status) if result_kind == "reconciliation" else None
    )
    binding = _binding(
        preparation_digest=preparation_digest,
        result_kind=result_kind,
        result_sha256=(
            external_publication_execution_reconciliation_digest(evidence)
            if evidence is not None
            else None
        ),
        recovery_kind=recovery_kind,
        previous_recovery_kind=previous_recovery_kind,
    )
    binding_digest = (
        external_publication_recovery_resume_decision_preparation_intent_binding_digest(
            binding
        )
    )
    binding_path = (
        root / f"{_BINDING_PREFIX}{binding.decision_preparation_sha256}{_SUFFIX}"
    )
    persist_external_publication_recovery_resume_decision_preparation_intent_binding(
        binding_path, binding
    )

    start = _start()
    start_digest = external_publication_operation_start_digest(start)
    authorization = _authorization(
        binding_digest=binding_digest,
        start_digest=start_digest,
        preparation_digest=preparation_digest,
        result_kind=result_kind,
        result_sha256=(
            external_publication_execution_reconciliation_digest(evidence)
            if evidence is not None
            else None
        ),
        recovery_kind=recovery_kind,
        previous_recovery_kind=previous_recovery_kind,
    )
    authorization_path = root / f"{_AUTHORIZATION_PREFIX}{binding_digest}{_SUFFIX}"
    persist_external_publication_recovery_resume_decision_preparation_start_authorization(
        authorization_path, authorization
    )
    authorization_digest = external_publication_recovery_resume_decision_preparation_start_authorization_digest(
        authorization
    )

    start_path = root / f"{_START_PREFIX}{authorization_digest}{_SUFFIX}"
    start_path.write_bytes(external_publication_operation_start_canonical_bytes(start))
    reconciliation_path = (
        root / f"{_RECONCILIATION_PREFIX}{authorization_digest}{_SUFFIX}"
    )
    reconciliation_digest = None
    if evidence is not None:
        reconciliation_digest = external_publication_execution_reconciliation_digest(
            evidence
        )
        persist_external_publication_execution_reconciliation(
            reconciliation_path, evidence
        )
    outcome = _outcome(
        authorization_digest=authorization_digest,
        binding_digest=binding_digest,
        start_digest=start_digest,
        preparation_digest=preparation_digest,
        state=state,
        result_kind=result_kind,
        result_sha256=reconciliation_digest,
        recovery_kind=recovery_kind,
        previous_recovery_kind=previous_recovery_kind,
    )
    outcome_path = root / f"{_OUTCOME_PREFIX}{authorization_digest}{_SUFFIX}"
    persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        outcome_path, outcome
    )
    return _Lineage(
        root=root,
        binding_path=binding_path,
        binding=binding,
        binding_digest=binding_digest,
        authorization_path=authorization_path,
        authorization=authorization,
        authorization_digest=authorization_digest,
        start_path=start_path,
        start=start,
        start_digest=start_digest,
        reconciliation_path=reconciliation_path,
        reconciliation=evidence,
        reconciliation_digest=reconciliation_digest,
        outcome_path=outcome_path,
        outcome=outcome,
    )


def _recorders(lineage: _Lineage) -> dict[str, _Recorder]:
    return {
        "binding_loader": _Recorder(result=lineage.binding),
        "binding_digest_function": _Recorder(
            delegate=external_publication_recovery_resume_decision_preparation_intent_binding_digest
        ),
        "authorization_loader": _Recorder(result=lineage.authorization),
        "authorization_digest_function": _Recorder(
            delegate=external_publication_recovery_resume_decision_preparation_start_authorization_digest
        ),
        "outcome_loader": _Recorder(result=lineage.outcome),
        "outcome_digest_function": _Recorder(
            delegate=external_publication_recovery_resume_decision_preparation_reconciliation_outcome_digest
        ),
        "start_loader": _Recorder(result=lineage.start),
        "start_digest_function": _Recorder(
            delegate=external_publication_operation_start_digest
        ),
        "reconciliation_loader": _Recorder(result=lineage.reconciliation),
        "reconciliation_digest_function": _Recorder(
            delegate=external_publication_execution_reconciliation_digest
        ),
    }


def _route_with_recorders(
    lineage: _Lineage,
) -> tuple[object, dict[str, _Recorder]]:
    recorders = _recorders(lineage)
    result = route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        decision_preparation_intent_binding_path=lineage.binding_path,
        **recorders,
    )
    return result, recorders


def _assert_error(error: ValueError, classification: str) -> None:
    assert (
        type(error)
        is ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingCompatibilityError
    )
    assert isinstance(
        error,
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingError,
    )
    assert str(error) == _ROUTING_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _forged(
    source: object, *, cls: type[object] | None = None, **overrides: object
) -> object:
    target_type = type(source) if cls is None else cls
    names = {field.name for field in dataclasses.fields(type(source))}
    for name in overrides:
        assert name in names, name
    value = object.__new__(target_type)
    for name in names:
        object.__setattr__(value, name, getattr(source, name))
    for name, replacement in overrides.items():
        object.__setattr__(value, name, replacement)
    return value


def test_public_api_signature_and_default_dependencies_are_exact() -> None:
    function = route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome
    parameters = inspect.signature(function).parameters
    assert list(parameters) == [
        "decision_preparation_intent_binding_path",
        "binding_loader",
        "binding_digest_function",
        "authorization_loader",
        "authorization_digest_function",
        "outcome_loader",
        "outcome_digest_function",
        "start_loader",
        "start_digest_function",
        "reconciliation_loader",
        "reconciliation_digest_function",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in parameters.values()
    )
    assert parameters["binding_loader"].default is (
        routing_module.load_external_publication_recovery_resume_decision_preparation_intent_binding
    )
    assert parameters["authorization_loader"].default is (
        routing_module.load_external_publication_recovery_resume_decision_preparation_start_authorization
    )
    assert parameters["outcome_loader"].default is (
        routing_module.load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome
    )
    assert (
        parameters["start_loader"].default
        is routing_module.load_external_publication_operation_start
    )
    assert parameters["reconciliation_loader"].default is (
        routing_module.load_external_publication_execution_reconciliation
    )
    for forbidden in (
        "binding",
        "authorization",
        "outcome",
        "start",
        "reconciliation",
        "decision",
        "runtime_request",
        "phase307_result",
        "provider",
        "persist_function",
    ):
        assert forbidden not in parameters


def test_decision_required_model_has_exact_seventeen_frozen_fields_and_coupling() -> (
    None
):
    assert tuple(
        field.name
        for field in dataclasses.fields(
            ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired
        )
    ) == (
        "schema_version",
        "decision_preparation_reconciliation_outcome_sha256",
        "decision_preparation_start_authorization_sha256",
        "decision_preparation_intent_binding_sha256",
        "decision_preparation_sha256",
        "recovery_resume_decision_sha256",
        "operation_intent_sha256",
        "operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "source_operation",
        "previous_recovery_kind",
        "recovery_kind",
        "operation",
        "result_kind",
        "result_sha256",
        "state",
    )
    assert ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired.__dataclass_params__.frozen
    values = {
        field.name: "a" * 64
        for field in dataclasses.fields(
            ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired
        )
        if field.name.endswith("sha256")
    }
    values.update(
        schema_version=_DECISION_SCHEMA,
        source_operation="resume",
        previous_recovery_kind="reconciliation_mismatch",
        recovery_kind="reconciliation_mismatch",
        operation="resume",
        result_kind="reconciliation",
        result_sha256="b" * 64,
        state="decision_required",
    )
    decision = ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired(
        **values
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.state = "other"  # type: ignore[misc]
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingError
    ):
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired(
            **{**values, "result_kind": "none", "result_sha256": "b" * 64}
        )
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingError
    ):
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired(
            **{**values, "result_kind": "reconciliation", "result_sha256": None}
        )


def test_path_subclasses_are_rejected_before_any_dependency(tmp_path: Path) -> None:
    path = _PathChild(str(tmp_path / "binding.json"))
    recorder = _Recorder(result=None)
    with pytest.raises(ValueError) as caught:
        route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            decision_preparation_intent_binding_path=path,
            binding_loader=recorder,
        )
    _assert_error(caught.value, "path_type")
    assert recorder.call_count == 0


def test_binding_filename_is_canonical_and_wrong_path_is_read_only(
    tmp_path: Path,
) -> None:
    lineage = _seed(tmp_path)
    before = {path: path.read_bytes() for path in lineage.root.iterdir()}
    recorder = _Recorder(result=lineage.binding)
    with pytest.raises(ValueError) as caught:
        route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            decision_preparation_intent_binding_path=lineage.root / "wrong.json",
            binding_loader=recorder,
            binding_digest_function=_Recorder(digest=lineage.binding_digest),
            authorization_loader=_Recorder(result=lineage.authorization),
            authorization_digest_function=_Recorder(
                digest=lineage.authorization_digest
            ),
            outcome_loader=_Recorder(result=lineage.outcome),
            outcome_digest_function=_Recorder(digest="a" * 64),
            start_loader=_Recorder(result=lineage.start),
            start_digest_function=_Recorder(digest=lineage.start_digest),
            reconciliation_loader=_Recorder(result=lineage.reconciliation),
            reconciliation_digest_function=_Recorder(
                digest=lineage.reconciliation_digest
            ),
        )
    _assert_error(caught.value, "binding_path")
    assert recorder.call_count == 1
    assert {path: path.read_bytes() for path in lineage.root.iterdir()} == before


def test_completed_matched_routes_exact_loaded_identity_and_skips_outcome_digest(
    tmp_path: Path,
) -> None:
    lineage = _seed(tmp_path, state="completed", evidence_status="matched")
    result, recorders = _route_with_recorders(lineage)
    assert result is lineage.outcome
    assert recorders["binding_loader"].calls[0][0][0] is lineage.binding_path
    assert (
        recorders["authorization_loader"].calls[0][0][0] == lineage.authorization_path
    )
    assert recorders["outcome_loader"].calls[0][0][0] == lineage.outcome_path
    assert recorders["start_loader"].calls[0][0][0] == lineage.start_path
    assert (
        recorders["reconciliation_loader"].calls[0][0][0] == lineage.reconciliation_path
    )
    assert recorders["binding_digest_function"].calls[0][0][0] is lineage.binding
    assert (
        recorders["authorization_digest_function"].calls[0][0][0]
        is lineage.authorization
    )
    assert recorders["start_digest_function"].calls[0][0][0] is lineage.start
    assert (
        recorders["reconciliation_digest_function"].calls[0][0][0]
        is lineage.reconciliation
    )
    assert recorders["outcome_digest_function"].call_count == 0


def test_recovery_reconciliation_routes_current_mismatch_and_preserves_provenance(
    tmp_path: Path,
) -> None:
    lineage = _seed(
        tmp_path, state="recovery_required", evidence_status="lineage_mismatch"
    )
    result, recorders = _route_with_recorders(lineage)
    assert isinstance(
        result,
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired,
    )
    assert result.decision_preparation_reconciliation_outcome_sha256 == (
        external_publication_recovery_resume_decision_preparation_reconciliation_outcome_digest(
            lineage.outcome
        )
    )
    assert (
        result.decision_preparation_start_authorization_sha256
        == lineage.authorization_digest
    )
    assert result.decision_preparation_intent_binding_sha256 == lineage.binding_digest
    assert (
        result.decision_preparation_sha256
        == lineage.outcome.decision_preparation_sha256
    )
    assert (
        result.recovery_resume_decision_sha256
        == lineage.outcome.recovery_resume_decision_sha256
    )
    assert result.operation_intent_sha256 == lineage.outcome.operation_intent_sha256
    assert result.operation_start_sha256 == lineage.start_digest
    assert (
        result.publication_approval_sha256
        == lineage.outcome.publication_approval_sha256
    )
    assert result.publication_plan_sha256 == lineage.outcome.publication_plan_sha256
    assert result.source_operation == lineage.outcome.source_operation
    assert result.previous_recovery_kind == lineage.outcome.recovery_kind
    assert result.recovery_kind == "reconciliation_mismatch"
    assert result.operation == "resume"
    assert result.result_kind == "reconciliation"
    assert result.result_sha256 == lineage.outcome.result_sha256
    assert result.state == "decision_required"
    assert recorders["outcome_digest_function"].call_count == 1
    assert recorders["outcome_digest_function"].calls[0][0][0] is lineage.outcome
    assert recorders["reconciliation_digest_function"].call_count == 1


def test_recovery_none_routes_already_acquired_without_evidence_digest_or_loader(
    tmp_path: Path,
) -> None:
    lineage = _seed(tmp_path, result_kind="none")
    result, recorders = _route_with_recorders(lineage)
    assert isinstance(
        result,
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired,
    )
    assert (
        result.previous_recovery_kind
        == lineage.outcome.recovery_kind
        == "already_acquired"
    )
    assert result.recovery_kind == "already_acquired"
    assert result.result_kind == "none"
    assert result.result_sha256 is None
    assert result.state == "decision_required"
    assert recorders["outcome_digest_function"].call_count == 1
    assert recorders["reconciliation_loader"].call_count == 0
    assert recorders["reconciliation_digest_function"].call_count == 0


def test_none_outcome_with_later_evidence_fails_explicitly_without_upgrade(
    tmp_path: Path,
) -> None:
    lineage = _seed(tmp_path, result_kind="none")
    lineage.reconciliation_path.write_bytes(b"later evidence")
    before = {path: path.read_bytes() for path in lineage.root.iterdir()}
    with pytest.raises(ValueError) as caught:
        route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            decision_preparation_intent_binding_path=lineage.binding_path
        )
    _assert_error(caught.value, "reconciliation_presence")
    assert {path: path.read_bytes() for path in lineage.root.iterdir()} == before


@pytest.mark.parametrize(
    ("state", "evidence_status"),
    [("recovery_required", "matched"), ("completed", "lineage_mismatch")],
)
def test_evidence_status_must_match_durable_outcome_state(
    tmp_path: Path, state: str, evidence_status: str
) -> None:
    lineage = _seed(tmp_path, state=state, evidence_status=evidence_status)
    with pytest.raises(ValueError) as caught:
        route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            decision_preparation_intent_binding_path=lineage.binding_path
        )
    _assert_error(caught.value, "reconciliation_lineage")


def test_tampered_evidence_digest_and_wrong_cycle_bytes_are_rejected(
    tmp_path: Path,
) -> None:
    _seed(tmp_path / "first")
    second = _seed(tmp_path / "second")
    other_evidence = ExternalPublicationExecutionReconciliation(
        schema_version=_EVIDENCE_SCHEMA,
        claim_sha256="c" * 64,
        execution_evidence_sha256="d" * 64,
        status="lineage_mismatch",
        mismatched_fields=("provider",),
    )
    second.reconciliation_path.write_bytes(
        external_publication_execution_reconciliation_canonical_bytes(other_evidence)
    )
    with pytest.raises(ValueError) as caught:
        route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            decision_preparation_intent_binding_path=second.binding_path
        )
    _assert_error(caught.value, "reconciliation_lineage")


def test_phase302_phase303_phase307_and_start_lineage_are_revalidated(
    tmp_path: Path,
) -> None:
    lineage = _seed(tmp_path)
    forged_binding = _forged(lineage.binding, result_kind="none")
    with pytest.raises(ValueError) as caught:
        route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            decision_preparation_intent_binding_path=lineage.binding_path,
            binding_loader=_Recorder(result=forged_binding),
        )
    _assert_error(caught.value, "binding_contract")

    mismatched_authorization = _forged(
        lineage.authorization, previous_recovery_kind="reconciliation_mismatch"
    )
    with pytest.raises(ValueError) as caught:
        route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            decision_preparation_intent_binding_path=lineage.binding_path,
            binding_loader=_Recorder(result=lineage.binding),
            binding_digest_function=_Recorder(digest=lineage.binding_digest),
            authorization_loader=_Recorder(result=mismatched_authorization),
            authorization_digest_function=_Recorder(
                digest=lineage.authorization_digest
            ),
        )
    _assert_error(caught.value, "authorization_lineage")

    mismatched_outcome = _forged(lineage.outcome, operation_intent_sha256="a" * 64)
    with pytest.raises(ValueError) as caught:
        route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            decision_preparation_intent_binding_path=lineage.binding_path,
            binding_loader=_Recorder(result=lineage.binding),
            binding_digest_function=_Recorder(digest=lineage.binding_digest),
            authorization_loader=_Recorder(result=lineage.authorization),
            authorization_digest_function=_Recorder(
                digest=lineage.authorization_digest
            ),
            outcome_loader=_Recorder(result=mismatched_outcome),
        )
    _assert_error(caught.value, "outcome_lineage")

    mismatched_start = _forged(lineage.start, publication_plan_sha256="a" * 64)
    with pytest.raises(ValueError) as caught:
        route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            decision_preparation_intent_binding_path=lineage.binding_path,
            binding_loader=_Recorder(result=lineage.binding),
            binding_digest_function=_Recorder(digest=lineage.binding_digest),
            authorization_loader=_Recorder(result=lineage.authorization),
            authorization_digest_function=_Recorder(
                digest=lineage.authorization_digest
            ),
            outcome_loader=_Recorder(result=lineage.outcome),
            start_loader=_Recorder(result=mismatched_start),
            start_digest_function=_Recorder(digest=lineage.start_digest),
        )
    _assert_error(caught.value, "start_lineage")


def test_forged_subclass_predecessors_are_rejected_before_downstream_calls(
    tmp_path: Path,
) -> None:
    lineage = _seed(tmp_path)
    forged_authorization = _forged(lineage.authorization, cls=_AuthorizationChild)
    authorization_loader = _Recorder(result=forged_authorization)
    with pytest.raises(ValueError) as caught:
        route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            decision_preparation_intent_binding_path=lineage.binding_path,
            authorization_loader=authorization_loader,
        )
    _assert_error(caught.value, "authorization_contract")
    assert authorization_loader.call_count == 1

    forged_outcome = _forged(lineage.outcome, cls=_OutcomeChild)
    outcome_loader = _Recorder(result=forged_outcome)
    with pytest.raises(ValueError) as caught:
        route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            decision_preparation_intent_binding_path=lineage.binding_path,
            outcome_loader=outcome_loader,
        )
    _assert_error(caught.value, "outcome_contract")

    forged_start = _forged(lineage.start, cls=_StartChild)
    start_loader = _Recorder(result=forged_start)
    with pytest.raises(ValueError) as caught:
        route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            decision_preparation_intent_binding_path=lineage.binding_path,
            start_loader=start_loader,
        )
    _assert_error(caught.value, "start_contract")


def test_known_authoritative_loader_errors_preserve_exact_identity(
    tmp_path: Path,
) -> None:
    lineage = _seed(tmp_path)
    cases = [
        (
            "binding_loader",
            ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError(
                "binding_contract"
            ),
        ),
        (
            "authorization_loader",
            ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationCompatibilityError(
                "authorization_contract"
            ),
        ),
        (
            "outcome_loader",
            ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeCompatibilityError(
                "outcome_contract"
            ),
        ),
        ("start_loader", ExternalPublicationOperationStartError("start_contract")),
        (
            "reconciliation_loader",
            ExternalPublicationExecutionReconciliationError("result"),
        ),
    ]
    for dependency_name, fault in cases:
        recorders = _recorders(lineage)
        recorders[dependency_name].fault = fault
        with pytest.raises(type(fault)) as caught:
            route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
                decision_preparation_intent_binding_path=lineage.binding_path,
                **recorders,
            )
        assert caught.value is fault


def test_unexpected_dependency_exception_is_detail_safe_and_no_retry(
    tmp_path: Path,
) -> None:
    lineage = _seed(tmp_path)
    faulting_loader = _Recorder(fault=RuntimeError("private detail"))
    with pytest.raises(ValueError) as caught:
        route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            decision_preparation_intent_binding_path=lineage.binding_path,
            authorization_loader=faulting_loader,
        )
    _assert_error(caught.value, "dependency_error")
    assert faulting_loader.call_count == 1


def test_every_artifact_byte_is_unchanged_after_success_and_failure(
    tmp_path: Path,
) -> None:
    lineage = _seed(tmp_path, state="completed", evidence_status="matched")
    before = {path: path.read_bytes() for path in lineage.root.iterdir()}
    route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        decision_preparation_intent_binding_path=lineage.binding_path
    )
    assert {path: path.read_bytes() for path in lineage.root.iterdir()} == before

    broken = _seed(tmp_path / "broken", state="recovery_required")
    broken.reconciliation_path.write_bytes(b"tampered")
    before_broken = {path: path.read_bytes() for path in broken.root.iterdir()}
    with pytest.raises(ValueError):
        route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            decision_preparation_intent_binding_path=broken.binding_path
        )
    assert {path: path.read_bytes() for path in broken.root.iterdir()} == before_broken


def test_source_audit_has_no_lower_or_ambient_boundary() -> None:
    source = Path(routing_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_modules = {
        "time",
        "random",
        "uuid",
        "os",
        "socket",
        "subprocess",
        "secrets",
        "requests",
    }
    imported_modules = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_from_modules = {
        node.module.split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported_modules.isdisjoint(forbidden_modules)
    assert imported_from_modules.isdisjoint(forbidden_modules)
    source_lower = source.lower()
    for forbidden in (
        "persist_",
        "run_external_publication",
        "reconcile_external_publication_execution",
        ".resolve(",
        ".absolute(",
        ".samefile(",
        ".realpath(",
        ".normpath(",
        ".abspath(",
        ".readlink(",
        "subprocess",
        "socket",
        "provider",
        "credential",
        "phase309",
    ):
        assert forbidden not in source_lower
    assert "ExternalPublicationResumeOperationRequest" not in source
    assert "def main" not in source


def test_predecessor_default_loaders_and_digests_are_the_exact_read_only_helpers() -> (
    None
):
    assert (
        routing_module.load_external_publication_recovery_resume_decision_preparation_intent_binding
        is load_external_publication_recovery_resume_decision_preparation_intent_binding
    )
    assert (
        routing_module.load_external_publication_recovery_resume_decision_preparation_start_authorization
        is load_external_publication_recovery_resume_decision_preparation_start_authorization
    )
    assert (
        routing_module.load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome
        is load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome
    )
    assert (
        routing_module.load_external_publication_operation_start
        is load_external_publication_operation_start
    )
    assert (
        routing_module.load_external_publication_execution_reconciliation
        is load_external_publication_execution_reconciliation
    )
