"""Focused and integration regressions for the Phase 299 routing boundary."""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_resume_outcome_routing as routing_module  # noqa: E501
from ai_office.engine import (
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartError,
    ExternalPublicationRecoveryResumeDecisionRequired,
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeIntentBindingError,
    ExternalPublicationRecoveryResumeOutcome,
    ExternalPublicationRecoveryResumeOutcomeError,
    ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError,
    ExternalPublicationRecoveryResumeOutcomeRoutingError,
    ExternalPublicationRecoveryResumeStartAuthorization,
    ExternalPublicationRecoveryResumeStartAuthorizationError,
    external_publication_operation_start_canonical_bytes,
    external_publication_operation_start_digest,
    external_publication_recovery_resume_intent_binding_digest,
    external_publication_recovery_resume_outcome_digest,
    external_publication_recovery_resume_start_authorization_digest,
    load_external_publication_operation_start,
    load_external_publication_recovery_resume_intent_binding,
    load_external_publication_recovery_resume_outcome,
    load_external_publication_recovery_resume_start_authorization,
    persist_external_publication_recovery_resume_intent_binding,
    persist_external_publication_recovery_resume_outcome,
    persist_external_publication_recovery_resume_start_authorization,
    route_external_publication_recovery_resume_outcome,
)

_BINDING_SCHEMA = "external-publication-recovery-resume-intent-binding.v1"
_AUTHORIZATION_SCHEMA = "external-publication-recovery-resume-start-authorization.v1"
_START_SCHEMA = "external-publication-operation-start.v1"
_OUTCOME_SCHEMA = "external-publication-recovery-resume-outcome.v1"
_DECISION_SCHEMA = "external-publication-recovery-resume-decision-required.v1"
_ROUTING_MESSAGE = "external publication recovery resume outcome routing is blocked"
_AUTHORIZATION_PREFIX = "external-publication-recovery-resume-start-authorization-"
_START_PREFIX = "external-publication-recovery-resume-start-"
_OUTCOME_PREFIX = "external-publication-recovery-resume-outcome-"
_SUFFIX = ".json"


class _StringChild(str):
    pass


class _CallRecorder:
    """Record exact calls while optionally returning or raising a fixed value."""

    def __init__(
        self,
        *,
        result: object = None,
        fault: BaseException | None = None,
        delegate: object = None,
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
        if self.delegate is not None:
            value = self.delegate(*args, **kwargs)  # type: ignore[operator]
        else:
            value = self.result
        self.results.append(value)
        return value

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def first_positional(self) -> object:
        return self.calls[0][0][0]


def _assert_routing_error(error: ValueError, classification: str) -> None:
    assert (
        type(error) is ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    )
    assert isinstance(error, ExternalPublicationRecoveryResumeOutcomeRoutingError)
    assert isinstance(error, ValueError)
    assert str(error) == _ROUTING_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _field_names(cls: type[object]) -> set[str]:
    return {field.name for field in dataclasses.fields(cls)}  # type: ignore[arg-type]


def _forged_instance(
    source: object, *, cls: type[object] | None = None, **overrides: object
) -> object:
    """Allocate a dataclass without running validation and override real fields."""
    target_type = type(source) if cls is None else cls
    names = _field_names(type(source))
    for name in overrides:
        assert name in names, name
    value = object.__new__(target_type)  # type: ignore[call-overload]
    for name in names:
        object.__setattr__(value, name, getattr(source, name))
    for name, replacement in overrides.items():
        object.__setattr__(value, name, replacement)
    return value


def _binding(**overrides: object) -> ExternalPublicationRecoveryResumeIntentBinding:
    values: dict[str, object] = {
        "schema_version": _BINDING_SCHEMA,
        "resume_preparation_sha256": "a" * 64,
        "recovery_decision_sha256": "b" * 64,
        "publication_approval_sha256": "c" * 64,
        "publication_plan_sha256": "d" * 64,
        "operation_intent_sha256": "e" * 64,
        "source_operation": "resume",
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
        "source_operation": "resume",
        "recovery_kind": "already_acquired",
        "operation": "resume",
        "state": "authorized",
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeStartAuthorization(**values)  # type: ignore[arg-type]


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


def _outcome(**overrides: object) -> ExternalPublicationRecoveryResumeOutcome:
    values: dict[str, object] = {
        "schema_version": _OUTCOME_SCHEMA,
        "resume_start_authorization_sha256": "1" * 64,
        "resume_intent_binding_sha256": "2" * 64,
        "operation_intent_sha256": "e" * 64,
        "operation_start_sha256": "6" * 64,
        "publication_approval_sha256": "c" * 64,
        "publication_plan_sha256": "d" * 64,
        "source_operation": "resume",
        "recovery_kind": "already_acquired",
        "operation": "resume",
        "state": "recovery_required",
        "result_kind": "none",
        "result_sha256": None,
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeOutcome(**values)  # type: ignore[arg-type]


class _Lineage:
    """One exactly self-consistent Phase 295/296/298/290 lineage."""

    def __init__(
        self, root: Path, *, previous_recovery_kind: str = "already_acquired"
    ) -> None:
        self.root = root
        self.binding_path = root / "binding.json"
        self.binding = _binding(recovery_kind=previous_recovery_kind)
        self.binding_digest = (
            external_publication_recovery_resume_intent_binding_digest(self.binding)
        )
        self.start = _start()
        self.start_digest = external_publication_operation_start_digest(self.start)
        self.authorization = _authorization(
            resume_intent_binding_sha256=self.binding_digest,
            expected_operation_start_sha256=self.start_digest,
            recovery_kind=previous_recovery_kind,
        )
        self.authorization_digest = (
            external_publication_recovery_resume_start_authorization_digest(
                self.authorization
            )
        )
        self.outcome = self.build_outcome()

    @property
    def authorization_path(self) -> Path:
        return self.binding_path.parent / (
            f"{_AUTHORIZATION_PREFIX}{self.binding_digest}{_SUFFIX}"
        )

    @property
    def start_path(self) -> Path:
        return self.binding_path.parent / (
            f"{_START_PREFIX}{self.authorization_digest}{_SUFFIX}"
        )

    @property
    def outcome_path(self) -> Path:
        return self.binding_path.parent / (
            f"{_OUTCOME_PREFIX}{self.authorization_digest}{_SUFFIX}"
        )

    def build_outcome(
        self,
        *,
        state: str = "recovery_required",
        result_kind: str = "none",
        result_sha256: object = None,
        **overrides: object,
    ) -> ExternalPublicationRecoveryResumeOutcome:
        values: dict[str, object] = {
            "resume_start_authorization_sha256": self.authorization_digest,
            "resume_intent_binding_sha256": self.binding_digest,
            "operation_intent_sha256": self.binding.operation_intent_sha256,
            "operation_start_sha256": self.start_digest,
            "publication_approval_sha256": self.binding.publication_approval_sha256,
            "publication_plan_sha256": self.binding.publication_plan_sha256,
            "source_operation": self.binding.source_operation,
            "recovery_kind": self.binding.recovery_kind,
            "state": state,
            "result_kind": result_kind,
            "result_sha256": result_sha256,
        }
        values.update(overrides)
        return _outcome(**values)


class _Harness:
    """Fully injected boundary with exact-once recorders."""

    def __init__(
        self,
        root: Path,
        *,
        outcome: object | None = None,
        lineage: _Lineage | None = None,
    ) -> None:
        self.lineage = _Lineage(root) if lineage is None else lineage
        self.outcome = self.lineage.outcome if outcome is None else outcome
        self.binding_loader = _CallRecorder(result=self.lineage.binding)
        self.binding_digest = _CallRecorder(result=self.lineage.binding_digest)
        self.authorization_loader = _CallRecorder(result=self.lineage.authorization)
        self.authorization_digest = _CallRecorder(
            result=self.lineage.authorization_digest
        )
        self.outcome_loader = _CallRecorder(result=self.outcome)
        self.outcome_digest = _CallRecorder(
            result=external_publication_recovery_resume_outcome_digest(
                self.outcome  # type: ignore[arg-type]
            )
        )
        self.start_loader = _CallRecorder(result=self.lineage.start)
        self.start_digest = _CallRecorder(result=self.lineage.start_digest)

    def kwargs(self) -> dict[str, object]:
        return {
            "resume_intent_binding_path": self.lineage.binding_path,
            "binding_loader": self.binding_loader,
            "binding_digest_function": self.binding_digest,
            "authorization_loader": self.authorization_loader,
            "authorization_digest_function": self.authorization_digest,
            "outcome_loader": self.outcome_loader,
            "outcome_digest_function": self.outcome_digest,
            "start_loader": self.start_loader,
            "start_digest_function": self.start_digest,
        }

    @property
    def all_recorders(self) -> tuple[_CallRecorder, ...]:
        return (
            self.binding_loader,
            self.binding_digest,
            self.authorization_loader,
            self.authorization_digest,
            self.outcome_loader,
            self.outcome_digest,
            self.start_loader,
            self.start_digest,
        )


def _run(harness: _Harness, **overrides: object) -> object:
    values = harness.kwargs()
    values.update(overrides)
    return route_external_publication_recovery_resume_outcome(**values)  # type: ignore[arg-type]


# --- public model and surface -------------------------------------------


def _decision(**overrides: object) -> ExternalPublicationRecoveryResumeDecisionRequired:
    values: dict[str, object] = {
        "schema_version": _DECISION_SCHEMA,
        "recovery_resume_outcome_sha256": "0" * 64,
        "resume_start_authorization_sha256": "1" * 64,
        "resume_intent_binding_sha256": "2" * 64,
        "operation_intent_sha256": "3" * 64,
        "operation_start_sha256": "4" * 64,
        "publication_approval_sha256": "5" * 64,
        "publication_plan_sha256": "6" * 64,
        "source_operation": "resume",
        "previous_recovery_kind": "already_acquired",
        "recovery_kind": "reconciliation_mismatch",
        "operation": "resume",
        "result_kind": "reconciliation",
        "result_sha256": "7" * 64,
        "state": "decision_required",
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeDecisionRequired(**values)  # type: ignore[arg-type]


def test_decision_model_field_order_and_frozen() -> None:
    expected = (
        "schema_version",
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
        "operation",
        "result_kind",
        "result_sha256",
        "state",
    )
    assert tuple(field.name for field in dataclasses.fields(_decision())) == expected
    with pytest.raises(dataclasses.FrozenInstanceError):
        _decision().state = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("result_kind", "result_sha256", "recovery_kind"),
    [
        ("reconciliation", "7" * 64, "already_acquired"),
        ("none", None, "reconciliation_mismatch"),
        ("none", "7" * 64, "already_acquired"),
        ("reconciliation", None, "reconciliation_mismatch"),
    ],
)
def test_decision_result_and_current_recovery_kind_coupling(
    result_kind: str, result_sha256: object, recovery_kind: str
) -> None:
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    ) as caught:
        _decision(
            result_kind=result_kind,
            result_sha256=result_sha256,
            recovery_kind=recovery_kind,
        )
    _assert_routing_error(caught.value, "configuration")


def test_decision_previous_mismatch_requires_resume_source() -> None:
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    ) as caught:
        _decision(
            source_operation="fresh", previous_recovery_kind="reconciliation_mismatch"
        )
    _assert_routing_error(caught.value, "configuration")


def test_decision_rejects_string_subclasses_and_bad_digests() -> None:
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    ) as caught:
        _decision(operation=_StringChild("resume"))
    _assert_routing_error(caught.value, "configuration")

    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    ) as caught:
        _decision(operation_intent_sha256="A" * 64)
    _assert_routing_error(caught.value, "configuration")


def test_public_signature_defaults_and_only_binding_path() -> None:
    signature = inspect.signature(route_external_publication_recovery_resume_outcome)
    expected = (
        "resume_intent_binding_path",
        "binding_loader",
        "binding_digest_function",
        "authorization_loader",
        "authorization_digest_function",
        "outcome_loader",
        "outcome_digest_function",
        "start_loader",
        "start_digest_function",
    )
    assert tuple(signature.parameters) == expected
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    forbidden = {
        "authorization_path",
        "outcome_path",
        "start_path",
        "intent_path",
        "request",
        "runtime_request",
        "operator_decision",
        "operator_metadata",
        "provider",
        "transport",
        "credential",
        "phase298_function",
        "phase297_function",
        "phase293_function",
        "phase291_function",
        "phase290_function",
    }
    assert forbidden.isdisjoint(signature.parameters)
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
    assert (
        defaults["outcome_loader"] is load_external_publication_recovery_resume_outcome
    )
    assert (
        defaults["outcome_digest_function"]
        is external_publication_recovery_resume_outcome_digest
    )
    assert defaults["start_loader"] is load_external_publication_operation_start
    assert (
        defaults["start_digest_function"] is external_publication_operation_start_digest
    )


def test_preflight_rejects_non_path_without_loader_calls(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    ) as caught:
        _run(harness, resume_intent_binding_path=object())
    _assert_routing_error(caught.value, "path_type")
    assert all(recorder.call_count == 0 for recorder in harness.all_recorders)

    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    ) as caught:
        _run(harness, resume_intent_binding_path="binding.json")
    _assert_routing_error(caught.value, "path_type")
    assert all(recorder.call_count == 0 for recorder in harness.all_recorders)


@pytest.mark.parametrize(
    "dependency",
    [
        "binding_loader",
        "binding_digest_function",
        "authorization_loader",
        "authorization_digest_function",
        "outcome_loader",
        "outcome_digest_function",
        "start_loader",
        "start_digest_function",
    ],
)
def test_preflight_rejects_non_callable_dependency_before_any_load(
    tmp_path: Path, dependency: str
) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    ) as caught:
        _run(harness, **{dependency: object()})
    _assert_routing_error(caught.value, "configuration")
    assert all(recorder.call_count == 0 for recorder in harness.all_recorders)


# --- exact lineage and routing ------------------------------------------


def test_completed_returns_exact_loaded_outcome_and_does_not_digest_it(
    tmp_path: Path,
) -> None:
    lineage = _Lineage(tmp_path)
    outcome = lineage.build_outcome(
        state="completed", result_kind="reconciliation", result_sha256="7" * 64
    )
    harness = _Harness(tmp_path, outcome=outcome)
    result = _run(harness)
    assert result is outcome
    assert harness.outcome_loader.call_count == 1
    assert harness.outcome_digest.call_count == 0
    assert harness.lineage.start_path.parent == tmp_path


def test_reconciliation_recovery_derives_current_reason_only_from_result(
    tmp_path: Path,
) -> None:
    lineage = _Lineage(tmp_path, previous_recovery_kind="already_acquired")
    outcome = lineage.build_outcome(
        state="recovery_required",
        result_kind="reconciliation",
        result_sha256="7" * 64,
    )
    harness = _Harness(tmp_path, outcome=outcome)
    decision = _run(harness)
    assert type(decision) is ExternalPublicationRecoveryResumeDecisionRequired
    assert decision.previous_recovery_kind == "already_acquired"
    assert decision.recovery_kind == "reconciliation_mismatch"
    assert decision.result_kind == "reconciliation"
    assert decision.result_sha256 == "7" * 64
    assert harness.outcome_digest.call_count == 1
    assert harness.outcome_digest.first_positional is outcome


def test_none_recovery_derives_current_reason_only_from_result(
    tmp_path: Path,
) -> None:
    lineage = _Lineage(tmp_path, previous_recovery_kind="reconciliation_mismatch")
    outcome = lineage.build_outcome(
        state="recovery_required", result_kind="none", result_sha256=None
    )
    harness = _Harness(tmp_path, outcome=outcome, lineage=lineage)
    decision = _run(harness)
    assert type(decision) is ExternalPublicationRecoveryResumeDecisionRequired
    assert decision.previous_recovery_kind == "reconciliation_mismatch"
    assert decision.recovery_kind == "already_acquired"
    assert decision.result_kind == "none"
    assert decision.result_sha256 is None
    assert harness.outcome_digest.call_count == 1
    assert harness.outcome_digest.first_positional is outcome


def test_paths_are_derived_from_exact_binding_parent_and_digests(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    result = _run(harness)
    assert isinstance(result, ExternalPublicationRecoveryResumeDecisionRequired)
    assert harness.binding_loader.first_positional is harness.lineage.binding_path
    assert (
        harness.authorization_loader.first_positional
        == harness.lineage.authorization_path
    )
    assert harness.outcome_loader.first_positional == harness.lineage.outcome_path
    assert harness.start_loader.first_positional == harness.lineage.start_path
    assert harness.binding_digest.first_positional is harness.lineage.binding
    assert (
        harness.authorization_digest.first_positional is harness.lineage.authorization
    )
    assert harness.outcome_loader.call_count == 1
    assert harness.start_loader.call_count == 1
    assert harness.start_digest.first_positional is harness.lineage.start


@pytest.mark.parametrize(
    ("stage", "error_type", "classification"),
    [
        (
            "binding_loader",
            ExternalPublicationRecoveryResumeIntentBindingError,
            "configuration",
        ),
        (
            "binding_digest",
            ExternalPublicationRecoveryResumeIntentBindingError,
            "configuration",
        ),
        (
            "authorization_loader",
            ExternalPublicationRecoveryResumeStartAuthorizationError,
            "configuration",
        ),
        (
            "authorization_digest",
            ExternalPublicationRecoveryResumeStartAuthorizationError,
            "configuration",
        ),
        (
            "outcome_loader",
            ExternalPublicationRecoveryResumeOutcomeError,
            "configuration",
        ),
        ("start_loader", ExternalPublicationOperationStartError, "configuration"),
        ("start_digest", ExternalPublicationOperationStartError, "configuration"),
    ],
)
def test_known_dependency_errors_preserve_exact_identity(
    tmp_path: Path,
    stage: str,
    error_type: type[ValueError],
    classification: str,
) -> None:
    harness = _Harness(tmp_path)
    error = error_type(classification)  # type: ignore[call-arg]
    recorder_name = {
        "binding_loader": "binding_loader",
        "binding_digest": "binding_digest",
        "authorization_loader": "authorization_loader",
        "authorization_digest": "authorization_digest",
        "outcome_loader": "outcome_loader",
        "start_loader": "start_loader",
        "start_digest": "start_digest",
    }[stage]
    getattr(harness, recorder_name).fault = error
    with pytest.raises(error_type) as caught:
        _run(harness)
    assert caught.value is error
    if stage == "binding_loader":
        assert harness.binding_digest.call_count == 0
    elif stage == "binding_digest":
        assert harness.authorization_loader.call_count == 0
    elif stage == "authorization_loader":
        assert harness.authorization_digest.call_count == 0
        assert harness.outcome_loader.call_count == 0
    elif stage == "authorization_digest":
        assert harness.outcome_loader.call_count == 0
    elif stage == "outcome_loader":
        assert harness.start_loader.call_count == 0
    elif stage == "start_loader":
        assert harness.start_digest.call_count == 0
        assert harness.outcome_digest.call_count == 0
    else:
        assert harness.outcome_digest.call_count == 0


def test_known_outcome_digest_error_preserves_identity(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    error = ExternalPublicationRecoveryResumeOutcomeError("configuration")
    harness.outcome_digest.fault = error
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeError) as caught:
        _run(harness)
    assert caught.value is error


@pytest.mark.parametrize(
    "dependency",
    [
        "binding_loader",
        "binding_digest_function",
        "authorization_loader",
        "authorization_digest_function",
        "outcome_loader",
        "outcome_digest_function",
        "start_loader",
        "start_digest_function",
    ],
)
def test_unexpected_dependency_error_is_sanitized(
    tmp_path: Path, dependency: str
) -> None:
    harness = _Harness(tmp_path)
    fault = RuntimeError("secret path and credential must not escape")
    getattr(
        harness,
        {
            "binding_digest_function": "binding_digest",
            "authorization_digest_function": "authorization_digest",
            "outcome_digest_function": "outcome_digest",
            "start_digest_function": "start_digest",
        }.get(dependency, dependency),
    ).fault = fault
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    ) as caught:
        _run(harness, **({} if dependency == "outcome_digest_function" else {}))
    _assert_routing_error(caught.value, "dependency_error")
    assert "secret path" not in str(caught.value)


# --- local predecessor validation and lineage fences --------------------


@pytest.mark.parametrize(
    ("stage", "classification"),
    [
        ("binding", "binding_contract"),
        ("authorization", "authorization_contract"),
        ("outcome", "outcome_contract"),
        ("start", "start_contract"),
    ],
)
def test_forged_exact_predecessor_is_revalidated_before_digest_or_downstream(
    tmp_path: Path, stage: str, classification: str
) -> None:
    harness = _Harness(tmp_path)
    source = {
        "binding": harness.lineage.binding,
        "authorization": harness.lineage.authorization,
        "outcome": harness.outcome,
        "start": harness.lineage.start,
    }[stage]
    field = {
        "binding": "operation_intent_sha256",
        "authorization": "expected_operation_start_sha256",
        "outcome": "operation_start_sha256",
        "start": "operation_intent_sha256",
    }[stage]
    forged = _forged_instance(source, **{field: "F" * 64})
    getattr(harness, f"{stage}_loader").result = forged
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    ) as caught:
        _run(harness)
    _assert_routing_error(caught.value, classification)
    if stage == "binding":
        assert harness.binding_digest.call_count == 0
        assert harness.authorization_loader.call_count == 0
    elif stage == "authorization":
        assert harness.authorization_digest.call_count == 0
        assert harness.outcome_loader.call_count == 0
    elif stage == "outcome":
        assert harness.start_loader.call_count == 0
        assert harness.outcome_digest.call_count == 0
    else:
        assert harness.start_digest.call_count == 0
        assert harness.outcome_digest.call_count == 0


def test_subclass_predecessor_is_rejected_before_digest(tmp_path: Path) -> None:
    class BindingChild(ExternalPublicationRecoveryResumeIntentBinding):
        pass

    harness = _Harness(tmp_path)
    forged = _forged_instance(harness.lineage.binding, cls=BindingChild)
    harness.binding_loader.result = forged
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    ) as caught:
        _run(harness)
    _assert_routing_error(caught.value, "binding_contract")
    assert harness.binding_digest.call_count == 0


@pytest.mark.parametrize(
    ("field", "classification"),
    [
        ("resume_preparation_sha256", "authorization_lineage"),
        ("recovery_decision_sha256", "authorization_lineage"),
        ("operation_intent_sha256", "authorization_lineage"),
        ("publication_approval_sha256", "authorization_lineage"),
        ("publication_plan_sha256", "authorization_lineage"),
        ("source_operation", "authorization_lineage"),
        ("recovery_kind", "authorization_lineage"),
    ],
)
def test_authorization_lineage_mismatch_stops_before_outcome(
    tmp_path: Path, field: str, classification: str
) -> None:
    harness = _Harness(tmp_path)
    replacement: object = {
        "source_operation": "fresh",
        "recovery_kind": "reconciliation_mismatch",
    }.get(field, "f" * 64)
    harness.authorization_loader.result = _authorization(
        resume_intent_binding_sha256=harness.lineage.binding_digest,
        expected_operation_start_sha256=harness.lineage.start_digest,
        **{field: replacement},
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    ) as caught:
        _run(harness)
    _assert_routing_error(caught.value, classification)
    assert harness.outcome_loader.call_count == 0


def test_outcome_lineage_mismatch_stops_before_start(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.outcome_loader.result = harness.lineage.build_outcome(
        operation_intent_sha256="f" * 64
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    ) as caught:
        _run(harness)
    _assert_routing_error(caught.value, "outcome_lineage")
    assert harness.start_loader.call_count == 0


def test_start_lineage_mismatch_stops_before_outcome_digest(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.start_loader.result = _start(operation_intent_sha256="f" * 64)
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    ) as caught:
        _run(harness)
    _assert_routing_error(caught.value, "start_lineage")
    assert harness.outcome_digest.call_count == 0


def test_malformed_digest_is_fixed_and_downstream_stops(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.binding_digest.result = "not-a-digest"
    with pytest.raises(
        ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError
    ) as caught:
        _run(harness)
    _assert_routing_error(caught.value, "binding_digest")
    assert harness.authorization_loader.call_count == 0


# --- durable local integration ------------------------------------------


def _persist_lineage(
    root: Path,
    *,
    state: str,
    result_kind: str,
    result_sha256: object,
    previous_recovery_kind: str,
) -> _Lineage:
    lineage = _Lineage(root, previous_recovery_kind=previous_recovery_kind)
    outcome = lineage.build_outcome(
        state=state, result_kind=result_kind, result_sha256=result_sha256
    )
    persist_external_publication_recovery_resume_intent_binding(
        lineage.binding_path, lineage.binding
    )
    persist_external_publication_recovery_resume_start_authorization(
        lineage.authorization_path, lineage.authorization
    )
    lineage.start_path.write_bytes(
        external_publication_operation_start_canonical_bytes(lineage.start)
    )
    persist_external_publication_recovery_resume_outcome(lineage.outcome_path, outcome)
    lineage.outcome = outcome
    return lineage


@pytest.mark.parametrize(
    ("state", "result_kind", "result_sha256", "expected_kind"),
    [
        ("completed", "reconciliation", "7" * 64, None),
        ("recovery_required", "reconciliation", "7" * 64, "reconciliation_mismatch"),
        ("recovery_required", "none", None, "already_acquired"),
    ],
)
def test_default_loaders_route_local_durable_lineage_without_mutation(
    tmp_path: Path,
    state: str,
    result_kind: str,
    result_sha256: object,
    expected_kind: str | None,
) -> None:
    lineage = _persist_lineage(
        tmp_path,
        state=state,
        result_kind=result_kind,
        result_sha256=result_sha256,
        previous_recovery_kind=(
            "reconciliation_mismatch"
            if expected_kind == "already_acquired"
            else "already_acquired"
        ),
    )
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.iterdir()
        if path.is_file()
    }
    result = route_external_publication_recovery_resume_outcome(
        resume_intent_binding_path=lineage.binding_path
    )
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.iterdir()
        if path.is_file()
    }
    assert after == before
    if expected_kind is None:
        assert result is not None
        assert type(result) is ExternalPublicationRecoveryResumeOutcome
        assert result.state == "completed"
    else:
        assert type(result) is ExternalPublicationRecoveryResumeDecisionRequired
        assert result.recovery_kind == expected_kind
        assert (
            result.recovery_resume_outcome_sha256
            == external_publication_recovery_resume_outcome_digest(lineage.outcome)
        )


def test_default_loaders_preserve_completed_loader_identity_via_injected_loader(
    tmp_path: Path,
) -> None:
    lineage = _persist_lineage(
        tmp_path,
        state="completed",
        result_kind="reconciliation",
        result_sha256="7" * 64,
        previous_recovery_kind="already_acquired",
    )
    loaded = load_external_publication_recovery_resume_outcome(lineage.outcome_path)
    harness = _Harness(tmp_path, outcome=loaded)
    result = _run(harness)
    assert result is loaded


# --- source audit --------------------------------------------------------


def _module_tree() -> ast.Module:
    return ast.parse(Path(routing_module.__file__).read_text(encoding="utf-8"))


def _imported_modules() -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
        elif isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
    return modules


def _called_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def test_source_audit_uses_only_loaders_and_digests() -> None:
    modules = _imported_modules()
    assert "os" not in modules
    assert "socket" not in modules
    assert "subprocess" not in modules
    assert "time" not in modules
    assert "uuid" not in modules
    forbidden_names = {
        "persist_external_publication_recovery_resume_outcome",
        "persist_external_publication_recovery_resume_intent_binding",
        "persist_external_publication_recovery_resume_start_authorization",
        "run_and_persist_external_publication_recovery_resume_outcome",
        "run_external_publication_recovery_resume_start_handoff",
        "authorize_and_persist_external_publication_recovery_resume_start",
        "materialize_and_bind_external_publication_recovery_resume_intent",
        "prepare_and_persist_external_publication_recovery_resume_lineage",
        "decide_and_persist_external_publication_recovery",
        "acquire_external_publication_operation_start",
        "run_external_publication_operation",
        "socket",
        "subprocess",
        "resolve",
        "realpath",
        "normpath",
        "abspath",
        "samefile",
    }
    assert forbidden_names.isdisjoint(_called_names())
    source = Path(routing_module.__file__).read_text(encoding="utf-8")
    for token in (
        "os.environ",
        "os.getenv",
        "uuid4",
        "token_hex",
        "Path.resolve",
        "realpath",
        "normpath",
        "abspath",
        "samefile",
        "readlink",
    ):
        assert token not in source


def test_public_engine_exports_are_available() -> None:
    import ai_office.engine as engine

    assert (
        engine.route_external_publication_recovery_resume_outcome
        is route_external_publication_recovery_resume_outcome
    )
    assert (
        engine.ExternalPublicationRecoveryResumeDecisionRequired
        is ExternalPublicationRecoveryResumeDecisionRequired
    )
