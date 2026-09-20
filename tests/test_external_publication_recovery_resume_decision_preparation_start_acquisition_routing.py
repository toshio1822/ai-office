# ruff: noqa: E501

"""Provider-free direct regressions for the Phase 305 routing boundary."""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_resume_decision_preparation_start_acquisition_handoff as handoff_module
import ai_office.engine.external_publication_recovery_resume_decision_preparation_start_acquisition_routing as routing_module
from ai_office.engine import (
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartAcquisition,
    ExternalPublicationOperationStartError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingCompatibilityError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingFailureDetail,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeOutcome,
    ExternalPublicationRecoveryResumeStartAuthorization,
    authorize_and_persist_external_publication_recovery_resume_decision_preparation_start,
    decide_and_persist_external_publication_recovery_resume,
    external_publication_operation_start_canonical_bytes,
    external_publication_operation_start_digest,
    external_publication_recovery_resume_decision_digest,
    external_publication_recovery_resume_decision_preparation_digest,
    external_publication_recovery_resume_decision_preparation_intent_binding_digest,
    external_publication_recovery_resume_decision_preparation_start_authorization_digest,
    external_publication_recovery_resume_intent_binding_digest,
    external_publication_recovery_resume_start_authorization_digest,
    materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent,
    persist_external_publication_recovery_resume_intent_binding,
    persist_external_publication_recovery_resume_outcome,
    persist_external_publication_recovery_resume_start_authorization,
    prepare_and_persist_external_publication_recovery_resume_decision_lineage,
)

_ROUTE_SCHEMA = "external-publication-recovery-resume-decision-preparation-start-acquisition-route.v1"
_AUTHORIZATION_SCHEMA = (
    "external-publication-recovery-resume-decision-preparation-start-authorization.v1"
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
_CompatibilityError = ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingCompatibilityError
_Detail = ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingFailureDetail


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


class _StartChild(ExternalPublicationOperationStart):
    pass


class _StringChild(str):
    pass


class _RouteChild(
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute
):
    pass


@dataclasses.dataclass(frozen=True)
class _LookalikeAcquisition:
    status: str
    start: ExternalPublicationOperationStart


def _forged(source: object, **overrides: object) -> object:
    return _forged_as(source, type(source), **overrides)


def _forged_as(
    source: object, target_type: type[object], **overrides: object
) -> object:
    value = object.__new__(target_type)
    names = {field.name for field in dataclasses.fields(source)}  # type: ignore[arg-type]
    for name in overrides:
        assert name in names, name
    for name in names:
        object.__setattr__(value, name, getattr(source, name))
    for name, replacement in overrides.items():
        object.__setattr__(value, name, replacement)
    return value


def _start(
    *,
    operation: str = "resume",
    intent_digest: str = "e" * 64,
    approval: str = "c" * 64,
    plan: str = "d" * 64,
) -> ExternalPublicationOperationStart:
    return ExternalPublicationOperationStart(
        schema_version=_START_SCHEMA,
        operation_intent_sha256=intent_digest,
        publication_approval_sha256=approval,
        publication_plan_sha256=plan,
        operation=operation,  # type: ignore[arg-type]
        state="started",
    )


def _acquisition(
    status: str = "acquired",
) -> ExternalPublicationOperationStartAcquisition:
    return ExternalPublicationOperationStartAcquisition(
        status=status,  # type: ignore[arg-type]
        start=_start(),
    )


def _assert_error(error: ValueError, classification: str) -> None:
    assert type(error) is _CompatibilityError
    assert isinstance(
        error,
        ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingError,
    )
    assert isinstance(error, ValueError)
    assert str(error) == routing_module._ROUTE_ERROR_MESSAGE
    assert type(error.detail) is _Detail
    assert error.detail.classification == classification
    assert error.__cause__ is None


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
) -> object:
    return routing_module.ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization(
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


def _seed_real_phase295_lineage(
    root: Path,
    *,
    previous_recovery_kind: str,
    result_kind: str,
    result_sha256: str | None,
) -> Path:
    """Seed provider-free Phase295/296/298 durable records for Phase303."""
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


def _real_phase303_authorization(
    root: Path,
    *,
    previous: str = "already_acquired",
    result_kind: str = "reconciliation",
    result_sha256: str | None = "7" * 64,
    decision_id: str = "phase305",
) -> tuple[Path, object, str, str]:
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
        decision_id=decision_id,
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
        external_publication_recovery_resume_decision_preparation_digest(preparation)
    )
    binding_digest = (
        external_publication_recovery_resume_decision_preparation_intent_binding_digest(
            phase302_binding
        )
    )
    binding_path = root / f"{_BINDING_PREFIX}{preparation_digest}{_SUFFIX}"
    authorization = authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
        decision_preparation_intent_binding_path=binding_path
    )
    authorization_path = root / f"{_AUTHORIZATION_PREFIX}{binding_digest}{_SUFFIX}"
    assert authorization.decision_preparation_sha256 == preparation_digest
    assert authorization.recovery_resume_decision_sha256 == decision_digest
    return (
        authorization_path,
        authorization,
        phase302_binding.operation_intent_sha256,
        preparation_digest,
    )


def test_public_api_model_signature_defaults_and_exports() -> None:
    route_type = (
        ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute
    )
    assert dataclasses.is_dataclass(route_type)
    assert route_type.__dataclass_params__.frozen
    assert tuple(field.name for field in dataclasses.fields(route_type)) == (
        "schema_version",
        "acquisition",
        "route",
    )
    assert tuple(route_type.__annotations__) == (
        "schema_version",
        "acquisition",
        "route",
    )
    assert routing_module.__all__ == [
        "ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute",
        "ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingCompatibilityError",
        "ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingError",
        "ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingFailureDetail",
        "route_external_publication_recovery_resume_decision_preparation_start_acquisition",
    ]
    function = routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition
    signature = inspect.signature(function)
    assert tuple(signature.parameters) == (
        "start_authorization_path",
        "phase304_function",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    assert signature.parameters["phase304_function"].default is (
        handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff
    )
    assert routing_module.ExternalPublicationOperationStartAcquisition is (
        ExternalPublicationOperationStartAcquisition
    )


def test_injected_acquired_and_already_acquired_routes_keep_exact_identity_and_kwargs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authorization.json"
    for status, expected_route in (
        ("acquired", "fresh_start_acquired"),
        ("already_acquired", "recovery_required"),
    ):
        acquisition = _acquisition(status)
        phase304 = _Recorder(acquisition)
        route = routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition(
            start_authorization_path=path,
            phase304_function=phase304,
        )
        assert (
            type(route)
            is ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute
        )
        assert route.schema_version == _ROUTE_SCHEMA
        assert route.route == expected_route
        assert route.acquisition is acquisition
        assert phase304.call_count == 1
        assert phase304.calls == [((), {"start_authorization_path": path})]
        assert phase304.calls[0][1]["start_authorization_path"] is path
        assert not path.exists()


def test_preflight_rejects_path_subclass_and_noncallable_before_phase304_call(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authorization.json"
    phase304 = _Recorder(_acquisition())
    with pytest.raises(ValueError) as path_error:
        routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition(
            start_authorization_path=_PathChild(path),
            phase304_function=phase304,
        )
    _assert_error(path_error.value, "path_type")
    assert phase304.call_count == 0

    with pytest.raises(ValueError) as configuration_error:
        routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition(
            start_authorization_path=path,
            phase304_function=None,  # type: ignore[arg-type]
        )
    _assert_error(configuration_error.value, "configuration")
    assert phase304.call_count == 0


@pytest.mark.parametrize(
    "error",
    [
        ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError(
            "dependency_error"
        ),
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError(
            "load"
        ),
        ExternalPublicationOperationIntentError("load"),
        ExternalPublicationOperationStartError("ambiguous"),
    ],
)
def test_known_phase304_error_identity_is_preserved_without_retry(
    tmp_path: Path, error: ValueError
) -> None:
    path = tmp_path / "authorization.json"
    phase304 = _Recorder(fault=error)
    with pytest.raises(type(error)) as raised:
        routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition(
            start_authorization_path=path,
            phase304_function=phase304,
        )
    assert raised.value is error
    assert phase304.call_count == 1


def test_unexpected_phase304_error_is_sanitized_and_not_retried(tmp_path: Path) -> None:
    path = tmp_path / "authorization.json"
    phase304 = _Recorder(fault=RuntimeError("provider credential must not leak"))
    with pytest.raises(ValueError) as raised:
        routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition(
            start_authorization_path=path,
            phase304_function=phase304,
        )
    _assert_error(raised.value, "dependency_error")
    assert "provider" not in str(raised.value)
    assert "credential" not in str(raised.value)
    assert phase304.call_count == 1


@pytest.mark.parametrize(
    "bad_result",
    [
        _LookalikeAcquisition("acquired", _start()),
        _forged(_acquisition(), status="forged"),
        _forged(_acquisition(), status=_StringChild("acquired")),
        _forged(_acquisition(), start=_forged(_start(), operation="bad")),
        _forged(_acquisition(), start=_forged_as(_start(), _StartChild)),
    ],
)
def test_malformed_phase304_results_are_rejected_after_one_call_without_retry(
    tmp_path: Path, bad_result: object
) -> None:
    path = tmp_path / "authorization.json"
    phase304 = _Recorder(bad_result)
    with pytest.raises(ValueError) as raised:
        routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition(
            start_authorization_path=path,
            phase304_function=phase304,
        )
    _assert_error(raised.value, "acquisition_contract")
    assert phase304.call_count == 1


def test_acquisition_subclass_and_forged_invalid_start_are_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authorization.json"
    valid = _acquisition()
    subclass = _forged_as(valid, _AcquisitionChild)
    phase304 = _Recorder(subclass)
    with pytest.raises(ValueError) as raised:
        routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition(
            start_authorization_path=path,
            phase304_function=phase304,
        )
    _assert_error(raised.value, "acquisition_contract")
    assert phase304.call_count == 1

    forged_start = _forged(valid.start, operation="not-resume")
    forged_acquisition = _forged(valid, start=forged_start)
    phase304 = _Recorder(forged_acquisition)
    with pytest.raises(ValueError) as raised:
        routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition(
            start_authorization_path=path,
            phase304_function=phase304,
        )
    _assert_error(raised.value, "acquisition_contract")
    assert phase304.call_count == 1


def test_route_model_rejects_subclass_invalid_route_and_status_coupling() -> None:
    acquired = _acquisition("acquired")
    already = _acquisition("already_acquired")
    valid = ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute(
        schema_version=_ROUTE_SCHEMA,
        acquisition=acquired,
        route="fresh_start_acquired",
    )
    assert valid.acquisition is acquired

    for acquisition, route in (
        (acquired, "recovery_required"),
        (already, "fresh_start_acquired"),
        (acquired, "invalid"),
    ):
        with pytest.raises(ValueError) as raised:
            ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute(
                schema_version=_ROUTE_SCHEMA,
                acquisition=acquisition,
                route=route,  # type: ignore[arg-type]
            )
        _assert_error(raised.value, "route_contract")

    forged_route = _forged(valid, route="recovery_required")
    with pytest.raises(ValueError) as raised:
        routing_module._validate_route(forged_route)
    _assert_error(raised.value, "route_contract")

    with pytest.raises(ValueError) as raised:
        ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute(
            schema_version=_StringChild(_ROUTE_SCHEMA),
            acquisition=acquired,
            route="fresh_start_acquired",
        )
    _assert_error(raised.value, "route_contract")

    forged_subclass = object.__new__(_RouteChild)
    object.__setattr__(forged_subclass, "schema_version", _ROUTE_SCHEMA)
    object.__setattr__(forged_subclass, "acquisition", acquired)
    object.__setattr__(forged_subclass, "route", "fresh_start_acquired")
    with pytest.raises(ValueError) as raised:
        routing_module._validate_route(forged_subclass)
    _assert_error(raised.value, "route_contract")


def test_source_audit_has_no_lower_acquisition_execution_persistence_or_ambient_state() -> (
    None
):
    source = Path(routing_module.__file__).read_text()
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module or "")
            imported_names.update(alias.name for alias in node.names)

    forbidden_import_tokens = {
        "os",
        "json",
        "hashlib",
        "time",
        "random",
        "uuid",
        "socket",
        "subprocess",
        "external_publication_execution",
        "external_publication_execution_reconciliation",
        "external_publication_recovery_resume_outcome",
        "external_publication_recovery_resume_start_handoff",
    }
    assert not any(
        token == module or token in module
        for module in imported_modules
        for token in forbidden_import_tokens
    )
    forbidden_names = {
        "acquire_external_publication_operation_start",
        "run_external_publication_operation_start_handoff",
        "run_external_publication_recovery_resume_outcome",
        "run_external_publication_operation",
        "reconcile_external_publication_execution",
        "persist_external_publication_operation_start",
        "load_external_publication_operation_start",
        "serialize_external_publication_operation_start_canonical",
        "external_publication_operation_start_digest",
    }
    assert not forbidden_names & imported_names
    assert not any(
        isinstance(node, ast.Name) and node.id in forbidden_names
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Attribute) and node.attr in forbidden_names
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and (
            node.func.id.startswith("persist_")
            or node.func.id.startswith("serialize_")
            or node.func.id.startswith("load_")
            or node.func.id.endswith("_digest")
        )
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr in {"resolve", "absolute", "expanduser", "expandvars"}
        for node in ast.walk(tree)
    )


@pytest.mark.parametrize(
    "previous,result_kind,result_sha256,expected_current",
    [
        ("already_acquired", "reconciliation", "7" * 64, "reconciliation_mismatch"),
        ("reconciliation_mismatch", "none", None, "already_acquired"),
    ],
)
def test_real_phase295_to_phase305_first_fresh_second_recovery_and_provenance(
    tmp_path: Path,
    previous: str,
    result_kind: str,
    result_sha256: str | None,
    expected_current: str,
) -> None:
    root = tmp_path / f"{previous}-{result_kind}"
    authorization_path, authorization, intent_digest, _ = _real_phase303_authorization(
        root,
        previous=previous,
        result_kind=result_kind,
        result_sha256=result_sha256,
        decision_id=f"phase305-{previous}-{result_kind}",
    )
    assert authorization.previous_recovery_kind == previous
    assert authorization.recovery_kind == expected_current
    before = {path.name for path in root.iterdir()}
    phase304 = _Recorder(
        delegate=handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff
    )
    first = routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition(
        start_authorization_path=authorization_path,
        phase304_function=phase304,
    )
    after_first = {path.name for path in root.iterdir()}
    second = routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition(
        start_authorization_path=authorization_path,
        phase304_function=phase304,
    )
    after_second = {path.name for path in root.iterdir()}

    assert first.route == "fresh_start_acquired"
    assert second.route == "recovery_required"
    assert first.acquisition is phase304.results[0]
    assert second.acquisition is phase304.results[1]
    assert phase304.call_count == 2
    assert phase304.calls[0] == ((), {"start_authorization_path": authorization_path})
    assert phase304.calls[1] == ((), {"start_authorization_path": authorization_path})
    assert first.acquisition.status == "acquired"
    assert second.acquisition.status == "already_acquired"
    assert first.acquisition.start == second.acquisition.start
    assert first.acquisition.start.operation_intent_sha256 == intent_digest
    assert after_first - before == {
        f"{_START_PREFIX}{external_publication_recovery_resume_decision_preparation_start_authorization_digest(authorization)}{_SUFFIX}"
    }
    assert after_second == after_first
    assert not any("execution" in name for name in after_second)
    assert not any(
        "reconciliation" in name and "outcome" not in name for name in after_second
    )


def test_crash_window_marker_only_never_restores_fresh_authority(
    tmp_path: Path,
) -> None:
    root = tmp_path / "crash-window"
    authorization_path, authorization, _, _ = _real_phase303_authorization(root)
    lost = handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
        start_authorization_path=authorization_path
    )
    assert lost.status == "acquired"
    del lost
    marker = (
        root
        / f"{_START_PREFIX}{external_publication_recovery_resume_decision_preparation_start_authorization_digest(authorization)}{_SUFFIX}"
    )
    marker_before = marker.read_bytes()
    phase304 = _Recorder(
        delegate=handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff
    )
    route = routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition(
        start_authorization_path=authorization_path,
        phase304_function=phase304,
    )
    assert route.route == "recovery_required"
    assert route.acquisition.status == "already_acquired"
    assert phase304.call_count == 1
    assert marker.read_bytes() == marker_before
    assert not any("execution" in path.name for path in root.iterdir())


def test_cycle_separation_allows_two_fresh_lineages_with_same_intent_and_start(
    tmp_path: Path,
) -> None:
    cycles = []
    for name in ("cycle-a", "cycle-b"):
        authorization_path, authorization, intent_digest, _ = (
            _real_phase303_authorization(
                tmp_path / name,
                decision_id=f"phase305-{name}",
            )
        )
        cycles.append((authorization_path, authorization, intent_digest))

    assert cycles[0][1].operation_intent_sha256 == cycles[1][1].operation_intent_sha256
    assert (
        cycles[0][1].expected_operation_start_sha256
        == cycles[1][1].expected_operation_start_sha256
    )
    assert (
        external_publication_recovery_resume_decision_preparation_start_authorization_digest(
            cycles[0][1]
        )
        != external_publication_recovery_resume_decision_preparation_start_authorization_digest(
            cycles[1][1]
        )
    )

    for authorization_path, authorization, intent_digest in cycles:
        phase304 = _Recorder(
            delegate=handoff_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff
        )
        first = routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition(
            start_authorization_path=authorization_path,
            phase304_function=phase304,
        )
        second = routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition(
            start_authorization_path=authorization_path,
            phase304_function=phase304,
        )
        assert first.route == "fresh_start_acquired"
        assert second.route == "recovery_required"
        assert first.acquisition.start.operation_intent_sha256 == intent_digest
        assert phase304.call_count == 2


def test_real_phase304_default_is_called_once_by_phase305_and_no_execution_artifact(
    tmp_path: Path,
) -> None:
    root = tmp_path / "default-dependency"
    authorization_path, _, _, _ = _real_phase303_authorization(root)
    calls: list[object] = []
    original = routing_module.run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff

    def recorder(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    # The public default is intentionally bound; this test uses explicit injection
    # to observe the real Phase304 call without changing production resolution.
    route = routing_module.route_external_publication_recovery_resume_decision_preparation_start_acquisition(
        start_authorization_path=authorization_path,
        phase304_function=recorder,
    )
    assert route.route == "fresh_start_acquired"
    assert len(calls) == 1
    assert calls == [((), {"start_authorization_path": authorization_path})]
    assert not any("execution" in path.name for path in root.iterdir())
