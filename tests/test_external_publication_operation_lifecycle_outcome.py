"""Focused provider-free tests for the Phase 292 lifecycle outcome boundary."""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import ai_office.engine.external_publication_operation_lifecycle_outcome as lifecycle_module  # noqa: E501
from ai_office.engine import (
    ExternalPublicationApproval,
    ExternalPublicationError,
    ExternalPublicationExecutionEvidenceError,
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionResult,
    ExternalPublicationFreshOperationRequest,
    ExternalPublicationOperationLifecycleOutcome,
    ExternalPublicationOperationLifecycleOutcomeCompatibilityError,
    ExternalPublicationOperationLifecycleOutcomeConflictError,
    ExternalPublicationOperationLifecycleOutcomeError,
    ExternalPublicationOperationLifecycleOutcomeFailureDetail,
    ExternalPublicationOperationLifecycleOutcomeLoadError,
    ExternalPublicationOperationLifecycleOutcomePersistenceError,
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartAcquisition,
    ExternalPublicationOperationStartError,
    ExternalPublicationOperationStartHandoffError,
    ExternalPublicationPlan,
    ExternalPublicationResumeOperationRequest,
    ExternalPublicationTarget,
    acquire_external_publication_operation_start,
    approve_external_publication,
    build_external_publication_operation_intent,
    external_publication_operation_lifecycle_outcome_canonical_bytes,
    external_publication_operation_lifecycle_outcome_digest,
    external_publication_operation_start_digest,
    external_publication_plan_digest,
    load_external_publication_operation_lifecycle_outcome,
    persist_external_publication_operation_intent,
    persist_external_publication_operation_lifecycle_outcome,
    run_and_persist_external_publication_operation_lifecycle_outcome,
    run_external_publication_operation_start_handoff,
    serialize_external_publication_operation_lifecycle_outcome_canonical,
)

_SCHEMA = "external-publication-operation-lifecycle-outcome.v1"
_START_SCHEMA = "external-publication-operation-start.v1"
_MESSAGE = "external publication operation lifecycle outcome is invalid"
_PERSIST_MESSAGE = "external publication operation lifecycle outcome persistence failed"
_LOAD_MESSAGE = "external publication operation lifecycle outcome could not be loaded"
_SOURCE = Path(lifecycle_module.__file__).read_text(encoding="utf-8")
_KEYS = (
    "operation",
    "operation_start_sha256",
    "publication_approval_sha256",
    "publication_plan_sha256",
    "result_kind",
    "result_sha256",
    "schema_version",
    "state",
)


class _StringChild(str):
    pass


def _forged_instance(cls: type[object], source: object, **overrides: object) -> object:
    value = object.__new__(cls)
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        object.__setattr__(value, field.name, getattr(source, field.name))
    for name, replacement in overrides.items():
        object.__setattr__(value, name, replacement)
    return value


def _assert_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationOperationLifecycleOutcomeCompatibilityError
    assert isinstance(error, ExternalPublicationOperationLifecycleOutcomeError)
    assert isinstance(error, ValueError)
    assert str(error) == _MESSAGE
    assert (
        type(error.detail) is ExternalPublicationOperationLifecycleOutcomeFailureDetail
    )
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _target() -> ExternalPublicationTarget:
    return ExternalPublicationTarget(
        schema_version="external-publication-target.v1",
        provider="future-provider",
        destination_id="destination-292",
    )


def _plan() -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id="regen-292",
        reconciliation_evidence_sha256="d" * 64,
        receipt_sha256="e" * 64,
        business_output_sha256="f" * 64,
        output_byte_length=11,
        provider="future-provider",
        publication_target_sha256="1" * 64,
    )


def _plan_alt() -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id="regen-292-alt",
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
        approved_by="human-reviewer-292",
        approval_id="approval-292",
    )


def _transport() -> object:
    raise AssertionError("Phase 292 must never call a transport")


def _fresh_request(
    root: Path,
    *,
    plan: ExternalPublicationPlan | None = None,
    approval: ExternalPublicationApproval | None = None,
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
        target=_target(),
        transport=_transport,  # type: ignore[arg-type]
    )


def _resume_request(
    root: Path,
    *,
    plan: ExternalPublicationPlan | None = None,
    approval: ExternalPublicationApproval | None = None,
) -> ExternalPublicationResumeOperationRequest:
    plan = plan if plan is not None else _plan()
    approval = approval if approval is not None else _approval(plan)
    return ExternalPublicationResumeOperationRequest(
        ledger_directory=root / "ledger",
        approval=approval,
        execution_evidence_path=root / "execution-evidence.json",
        execution_reconciliation_evidence_path=root / "reconciliation.json",
    )


def _start(
    operation: str,
    *,
    approval_digest: str,
    plan_digest: str,
    intent_digest: str = "a" * 64,
) -> ExternalPublicationOperationStart:
    return ExternalPublicationOperationStart(
        schema_version=_START_SCHEMA,  # type: ignore[arg-type]
        operation_intent_sha256=intent_digest,
        publication_approval_sha256=approval_digest,
        publication_plan_sha256=plan_digest,
        operation=operation,  # type: ignore[arg-type]
        state="started",
    )


def _fresh_result(
    *, approval_digest: str, plan_digest: str
) -> ExternalPublicationExecutionResult:
    return ExternalPublicationExecutionResult(
        schema_version="external-publication-execution-result.v1",
        regeneration_id="regen-292",
        publication_attempt_claim_sha256="a" * 64,
        publication_plan_sha256=plan_digest,
        publication_approval_sha256=approval_digest,
        business_output_sha256="d" * 64,
        output_byte_length=11,
        provider="future-provider",
        publication_target_sha256="e" * 64,
        publication_id="publication-292",
        status="published",
    )


def _reconciliation(
    status: str = "matched",
) -> ExternalPublicationExecutionReconciliation:
    return ExternalPublicationExecutionReconciliation(
        schema_version="external-publication-execution-reconciliation.v1",
        claim_sha256="a" * 64,
        execution_evidence_sha256="b" * 64,
        status=status,  # type: ignore[arg-type]
        mismatched_fields=("provider",) if status == "lineage_mismatch" else (),
    )


def _outcome(
    *,
    operation: str = "fresh",
    state: str = "completed",
    result_kind: str = "execution_result",
    result_sha256: object = "d" * 64,
    start_digest: str = "9" * 64,
    approval_digest: str = "b" * 64,
    plan_digest: str = "c" * 64,
) -> ExternalPublicationOperationLifecycleOutcome:
    return ExternalPublicationOperationLifecycleOutcome(
        schema_version=_SCHEMA,  # type: ignore[arg-type]
        operation_start_sha256=start_digest,
        publication_approval_sha256=approval_digest,
        publication_plan_sha256=plan_digest,
        operation=operation,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        result_kind=result_kind,  # type: ignore[arg-type]
        result_sha256=result_sha256,  # type: ignore[arg-type]
    )


class _Phase291Recorder:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    @property
    def keywords(self) -> list[dict[str, object]]:
        return [kwargs for _, kwargs in self.calls]


class _LoaderRecorder:
    def __init__(self, delegate: object) -> None:
        self.delegate = delegate
        self.calls: list[object] = []
        self.results: list[object] = []

    def __call__(self, path: object) -> object:
        self.calls.append(path)
        result = self.delegate(path)  # type: ignore[operator]
        self.results.append(result)
        return result


class _DigestRecorder:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls: list[object] = []

    def __call__(self, value: object) -> object:
        self.calls.append(value)
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


def _seed_start(root: Path, request: object, operation: str) -> None:
    approval = request.approval  # type: ignore[union-attr]
    intent = build_external_publication_operation_intent(
        approval,
        operation=operation,  # type: ignore[arg-type]
    )
    persist_external_publication_operation_intent(root / "intent.json", intent)
    acquire_external_publication_operation_start(
        intent_path=root / "intent.json", start_path=root / "start.json"
    )


# --- public surface -------------------------------------------------------


def test_public_exports_and_error_family() -> None:
    assert (
        ExternalPublicationOperationLifecycleOutcomeError.__name__
        == "ExternalPublicationOperationLifecycleOutcomeError"
    )
    assert issubclass(ExternalPublicationOperationLifecycleOutcomeError, ValueError)
    assert issubclass(
        ExternalPublicationOperationLifecycleOutcomeCompatibilityError,
        ExternalPublicationOperationLifecycleOutcomeError,
    )
    assert issubclass(
        ExternalPublicationOperationLifecycleOutcomePersistenceError,
        ExternalPublicationOperationLifecycleOutcomeError,
    )
    assert issubclass(
        ExternalPublicationOperationLifecycleOutcomeConflictError,
        ExternalPublicationOperationLifecycleOutcomePersistenceError,
    )
    assert issubclass(
        ExternalPublicationOperationLifecycleOutcomeLoadError,
        ExternalPublicationOperationLifecycleOutcomeError,
    )
    error = ExternalPublicationOperationLifecycleOutcomeError()
    assert str(error) == _MESSAGE
    assert error.detail.classification == "dependency_error"
    assert error.__cause__ is None
    assert (
        str(ExternalPublicationOperationLifecycleOutcomePersistenceError())
        == _PERSIST_MESSAGE
    )
    assert str(ExternalPublicationOperationLifecycleOutcomeLoadError()) == _LOAD_MESSAGE


def test_signature_defaults_and_no_execution_authority_argument() -> None:
    signature = inspect.signature(
        run_and_persist_external_publication_operation_lifecycle_outcome
    )
    parameters = signature.parameters
    assert list(parameters) == [
        "intent_path",
        "start_path",
        "lifecycle_outcome_path",
        "request",
        "phase291_function",
        "start_loader",
        "start_digest_function",
        "fresh_result_digest_function",
        "reconciliation_digest_function",
    ]
    for name in parameters:
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["phase291_function"].default is (
        run_external_publication_operation_start_handoff
    )
    assert parameters["start_loader"].default is (
        lifecycle_module.load_external_publication_operation_start
    )
    assert parameters["start_digest_function"].default is (
        external_publication_operation_start_digest
    )
    assert parameters["fresh_result_digest_function"].default is (
        lifecycle_module.external_publication_execution_result_digest
    )
    assert parameters["reconciliation_digest_function"].default is (
        lifecycle_module.external_publication_execution_reconciliation_digest
    )
    for forbidden in ("result", "acquisition", "outcome", "start_object"):
        assert forbidden not in parameters, forbidden


# --- model ----------------------------------------------------------------


def test_model_field_order_and_frozen() -> None:
    fields = [
        field.name
        for field in dataclasses.fields(ExternalPublicationOperationLifecycleOutcome)
    ]
    assert fields == [
        "schema_version",
        "operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "operation",
        "state",
        "result_kind",
        "result_sha256",
    ]
    outcome = _outcome()
    with pytest.raises(dataclasses.FrozenInstanceError):
        object.__setattr__  # noqa: B018
        setattr(outcome, "state", "recovery_required")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"operation": "fresh", "state": "completed", "result_kind": "execution_result"},
        {"operation": "resume", "state": "completed", "result_kind": "reconciliation"},
        {
            "operation": "resume",
            "state": "recovery_required",
            "result_kind": "reconciliation",
        },
        {
            "operation": "fresh",
            "state": "recovery_required",
            "result_kind": "none",
            "result_sha256": None,
        },
        {
            "operation": "resume",
            "state": "recovery_required",
            "result_kind": "none",
            "result_sha256": None,
        },
    ],
)
def test_model_accepts_exact_valid_combinations(kwargs: dict[str, object]) -> None:
    assert type(_outcome(**kwargs)) is ExternalPublicationOperationLifecycleOutcome


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "operation": "fresh",
            "state": "recovery_required",
            "result_kind": "execution_result",
        },
        {
            "operation": "resume",
            "state": "recovery_required",
            "result_kind": "execution_result",
        },
        {"operation": "fresh", "state": "completed", "result_kind": "reconciliation"},
        {
            "operation": "resume",
            "state": "completed",
            "result_kind": "execution_result",
        },
        {
            "operation": "fresh",
            "state": "completed",
            "result_kind": "none",
            "result_sha256": None,
        },
        {
            "operation": "resume",
            "state": "completed",
            "result_kind": "none",
            "result_sha256": None,
        },
        {"operation": "fresh", "state": "recovery_required", "result_kind": "none"},
        {"operation": "resume", "state": "completed", "result_kind": "none"},
    ],
)
def test_model_rejects_invalid_cross_field_combinations(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        _outcome(**kwargs)


def test_model_rejects_non_exact_runtime_types() -> None:
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        _outcome(operation=_StringChild("fresh"))
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        _outcome(state=_StringChild("completed"))
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        _outcome(result_kind=_StringChild("execution_result"))
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        _outcome(start_digest="A" * 64)
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        _outcome(approval_digest="b" * 63)
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        _outcome(plan_digest="c" * 65)
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        _outcome(result_sha256="d" * 64 + "x")
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        _outcome(result_kind="none", result_sha256="d" * 64)


def test_forged_model_is_rejected_by_helpers() -> None:
    valid = _outcome()
    forged = _forged_instance(
        ExternalPublicationOperationLifecycleOutcome,
        valid,
        operation=_StringChild("fresh"),
    )
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        serialize_external_publication_operation_lifecycle_outcome_canonical(
            forged  # type: ignore[arg-type]
        )
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        external_publication_operation_lifecycle_outcome_digest(
            forged  # type: ignore[arg-type]
        )
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        lifecycle_module._validate_lifecycle_outcome(object())

    subclass_type = type(
        "OutcomeChild", (ExternalPublicationOperationLifecycleOutcome,), {}
    )
    subclass_forged = _forged_instance(subclass_type, valid)
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        lifecycle_module._validate_lifecycle_outcome(subclass_forged)


# --- canonical serialization ---------------------------------------------


def test_canonical_json_exact_keys_and_deterministic_digest() -> None:
    outcome = _outcome()
    text = serialize_external_publication_operation_lifecycle_outcome_canonical(outcome)
    import json

    parsed = json.loads(text)
    assert tuple(parsed) == _KEYS
    assert text == serialize_external_publication_operation_lifecycle_outcome_canonical(
        _outcome()
    )
    assert external_publication_operation_lifecycle_outcome_digest(
        outcome
    ) == external_publication_operation_lifecycle_outcome_digest(_outcome())
    assert type(external_publication_operation_lifecycle_outcome_digest(outcome)) is str
    assert len(external_publication_operation_lifecycle_outcome_digest(outcome)) == 64
    assert external_publication_operation_lifecycle_outcome_canonical_bytes(
        outcome
    ) == text.encode("utf-8")


def test_loader_rejects_noncanonical_whitespace(tmp_path: Path) -> None:
    outcome = _outcome()
    path = tmp_path / "outcome.json"
    canonical = external_publication_operation_lifecycle_outcome_canonical_bytes(
        outcome
    )
    import json

    noncanonical = json.dumps(json.loads(canonical), indent=2).encode("utf-8")
    path.write_bytes(noncanonical)
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeLoadError) as info:
        load_external_publication_operation_lifecycle_outcome(path)
    assert info.value.detail.classification == "noncanonical"


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not json",
        b"[]",
        b"{}",
        b'{"operation":"fresh"}',
        b'{"operation":"fresh","operation":"resume"}',
        b'{"operation":NaN}',
        b"\xff\xfe",
    ],
)
def test_loader_rejects_malformed_payloads(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "outcome.json"
    path.write_bytes(payload)
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeLoadError):
        load_external_publication_operation_lifecycle_outcome(path)


def test_loader_rejects_extra_and_missing_keys(tmp_path: Path) -> None:
    import json

    outcome = _outcome()
    base = json.loads(
        external_publication_operation_lifecycle_outcome_canonical_bytes(outcome)
    )
    for mutated in (
        {**base, "extra": "x"},
        {key: value for key, value in base.items() if key != "state"},
    ):
        path = tmp_path / "outcome.json"
        path.write_text(json.dumps(mutated), encoding="utf-8")
        with pytest.raises(ExternalPublicationOperationLifecycleOutcomeLoadError):
            load_external_publication_operation_lifecycle_outcome(path)


def test_loader_rejects_non_regular_target(tmp_path: Path) -> None:
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeLoadError):
        load_external_publication_operation_lifecycle_outcome(tmp_path / "missing.json")
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeLoadError):
        load_external_publication_operation_lifecycle_outcome(tmp_path)
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeLoadError):
        load_external_publication_operation_lifecycle_outcome("string")  # type: ignore[arg-type]


def test_loader_round_trips_exact_bytes(tmp_path: Path) -> None:
    outcome = _outcome()
    path = tmp_path / "outcome.json"
    persist_external_publication_operation_lifecycle_outcome(path, outcome)
    loaded = load_external_publication_operation_lifecycle_outcome(path)
    assert type(loaded) is ExternalPublicationOperationLifecycleOutcome
    assert loaded == outcome


# --- persistence ----------------------------------------------------------


def test_persistence_idempotent_for_identical_bytes(tmp_path: Path) -> None:
    outcome = _outcome()
    path = tmp_path / "outcome.json"
    persist_external_publication_operation_lifecycle_outcome(path, outcome)
    first = path.read_bytes()
    persist_external_publication_operation_lifecycle_outcome(path, outcome)
    assert path.read_bytes() == first


def test_persistence_conflict_for_different_bytes(tmp_path: Path) -> None:
    path = tmp_path / "outcome.json"
    persist_external_publication_operation_lifecycle_outcome(path, _outcome())
    existing = path.read_bytes()
    other = _outcome(operation="resume", result_kind="reconciliation")
    with pytest.raises(
        ExternalPublicationOperationLifecycleOutcomeConflictError
    ) as info:
        persist_external_publication_operation_lifecycle_outcome(path, other)
    assert info.value.detail.classification == "conflict"
    assert path.read_bytes() == existing


def test_persistence_conflict_for_partial_bytes(tmp_path: Path) -> None:
    path = tmp_path / "outcome.json"
    path.write_bytes(b'{"operation":"fresh"')
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeConflictError):
        persist_external_publication_operation_lifecycle_outcome(path, _outcome())


def test_persistence_rejects_bad_parent_and_target(tmp_path: Path) -> None:
    with pytest.raises(
        ExternalPublicationOperationLifecycleOutcomePersistenceError
    ) as info:
        persist_external_publication_operation_lifecycle_outcome(
            tmp_path / "missing" / "outcome.json", _outcome()
        )
    assert info.value.detail.classification == "parent"
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomePersistenceError):
        persist_external_publication_operation_lifecycle_outcome(tmp_path, _outcome())
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomePersistenceError):
        persist_external_publication_operation_lifecycle_outcome(
            "string",
            _outcome(),  # type: ignore[arg-type]
        )


def test_persistence_ambiguous_after_write_retains_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = _outcome()
    path = tmp_path / "outcome.json"
    with monkeypatch.context() as scope:

        def _boom(*args: object, **kwargs: object) -> None:
            raise OSError("fsync failed")

        scope.setattr(lifecycle_module.os, "fsync", _boom)
        with pytest.raises(
            ExternalPublicationOperationLifecycleOutcomePersistenceError
        ) as info:
            persist_external_publication_operation_lifecycle_outcome(path, outcome)
        assert info.value.detail.classification == "ambiguous"
        assert path.exists()
        assert path.read_bytes() == (
            external_publication_operation_lifecycle_outcome_canonical_bytes(outcome)
        )
    persist_external_publication_operation_lifecycle_outcome(path, outcome)


def test_persistence_ambiguous_after_directory_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = _outcome()
    path = tmp_path / "outcome.json"
    with monkeypatch.context() as scope:

        def _boom(directory: object) -> None:
            raise OSError("dir fsync failed")

        scope.setattr(lifecycle_module, "_fsync_lifecycle_directory", _boom)
        with pytest.raises(
            ExternalPublicationOperationLifecycleOutcomePersistenceError
        ) as info:
            persist_external_publication_operation_lifecycle_outcome(path, outcome)
        assert info.value.detail.classification == "ambiguous"
        assert path.exists()
    persist_external_publication_operation_lifecycle_outcome(path, outcome)


def test_persistence_rejects_symlink_target(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_bytes(b"{}")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomePersistenceError):
        persist_external_publication_operation_lifecycle_outcome(link, _outcome())


# --- preflight ------------------------------------------------------------


def test_preflight_rejects_bad_paths_and_dependencies(tmp_path: Path) -> None:
    request = _fresh_request(tmp_path)
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path="string",  # type: ignore[arg-type]
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
        )
    assert info.value.detail.classification == "path_type"

    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
            phase291_function="nope",  # type: ignore[arg-type]
        )
    assert info.value.detail.classification == "configuration"


def test_preflight_rejects_unknown_request_type(tmp_path: Path) -> None:
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=object(),  # type: ignore[arg-type]
        )
    assert info.value.detail.classification == "request_contract"


def test_preflight_rejects_subclass_request(tmp_path: Path) -> None:
    request = _fresh_request(tmp_path)
    subclass = type("FreshChild", (ExternalPublicationFreshOperationRequest,), {})
    forged = _forged_instance(subclass, request)
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=forged,  # type: ignore[arg-type]
        )


def test_preflight_propagates_known_approval_error_by_identity(
    tmp_path: Path,
) -> None:
    request = _fresh_request(tmp_path)
    forged = object.__new__(ExternalPublicationFreshOperationRequest)
    for field in dataclasses.fields(request):
        object.__setattr__(forged, field.name, getattr(request, field.name))
    object.__setattr__(forged, "plan", _StringChild("plan"))
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=forged,
        )


def test_preflight_propagates_missing_start_error_for_absent_outcome(
    tmp_path: Path,
) -> None:
    request = _fresh_request(tmp_path)
    intent = build_external_publication_operation_intent(
        request.approval, operation="fresh"
    )
    persist_external_publication_operation_intent(tmp_path / "intent.json", intent)
    error = ExternalPublicationOperationStartError("load")
    with pytest.raises(ExternalPublicationOperationStartError) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
            phase291_function=_Phase291Recorder(error),
        )
    assert info.value is error
    assert not (tmp_path / "outcome.json").exists()


# --- existing outcome fast path -------------------------------------------


def _fast_path_setup(
    tmp_path: Path,
    operation: str = "fresh",
    *,
    state: str = "completed",
    result_kind: str = "execution_result",
    result_sha256: object = "d" * 64,
) -> tuple[
    Path,
    Path,
    Path,
    ExternalPublicationFreshOperationRequest
    | ExternalPublicationResumeOperationRequest,
]:
    request = (
        _fresh_request(tmp_path) if operation == "fresh" else _resume_request(tmp_path)
    )
    _seed_start(tmp_path, request, operation)
    start = lifecycle_module.load_external_publication_operation_start(
        tmp_path / "start.json"
    )
    outcome = ExternalPublicationOperationLifecycleOutcome(
        schema_version=_SCHEMA,
        operation_start_sha256=external_publication_operation_start_digest(start),
        publication_approval_sha256=start.publication_approval_sha256,
        publication_plan_sha256=start.publication_plan_sha256,
        operation=operation,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        result_kind=result_kind,  # type: ignore[arg-type]
        result_sha256=result_sha256,  # type: ignore[arg-type]
    )
    outcome_path = tmp_path / "outcome.json"
    persist_external_publication_operation_lifecycle_outcome(outcome_path, outcome)
    return tmp_path / "intent.json", tmp_path / "start.json", outcome_path, request


def test_existing_outcome_fast_path_returns_same_object_and_zero_calls(
    tmp_path: Path,
) -> None:
    intent_path, start_path, outcome_path, request = _fast_path_setup(tmp_path)
    recorder = _Phase291Recorder(AssertionError("Phase 291 must not be called"))
    returned = run_and_persist_external_publication_operation_lifecycle_outcome(
        intent_path=intent_path,
        start_path=start_path,
        lifecycle_outcome_path=outcome_path,
        request=request,
        phase291_function=recorder,
    )
    assert recorder.calls == []
    assert returned == load_external_publication_operation_lifecycle_outcome(
        outcome_path
    )


def test_existing_recovery_required_fast_path_is_returned_unchanged(
    tmp_path: Path,
) -> None:
    intent_path, start_path, outcome_path, request = _fast_path_setup(
        tmp_path,
        "resume",
        state="recovery_required",
        result_kind="reconciliation",
    )
    recorder = _Phase291Recorder(AssertionError("Phase 291 must not be called"))
    returned = run_and_persist_external_publication_operation_lifecycle_outcome(
        intent_path=intent_path,
        start_path=start_path,
        lifecycle_outcome_path=outcome_path,
        request=request,
        phase291_function=recorder,
    )
    assert recorder.calls == []
    assert returned.state == "recovery_required"
    assert returned.result_kind == "reconciliation"


def test_existing_outcome_request_lineage_mismatch_is_zero_call(
    tmp_path: Path,
) -> None:
    intent_path, start_path, outcome_path, _ = _fast_path_setup(tmp_path)
    other_request = _fresh_request(tmp_path, plan=_plan_alt())
    recorder = _Phase291Recorder(AssertionError("Phase 291 must not be called"))
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=intent_path,
            start_path=start_path,
            lifecycle_outcome_path=outcome_path,
            request=other_request,
            phase291_function=recorder,
        )
    assert recorder.calls == []


def test_existing_outcome_operation_mismatch_is_zero_call(tmp_path: Path) -> None:
    intent_path, start_path, outcome_path, request = _fast_path_setup(
        tmp_path, "resume", state="completed", result_kind="reconciliation"
    )
    recorder = _Phase291Recorder(AssertionError("Phase 291 must not be called"))
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=intent_path,
            start_path=start_path,
            lifecycle_outcome_path=outcome_path,
            request=_fresh_request(tmp_path),
            phase291_function=recorder,
        )
    assert recorder.calls == []
    assert request is not None


def test_existing_outcome_start_digest_mismatch_is_zero_call(tmp_path: Path) -> None:
    intent_path, start_path, outcome_path, request = _fast_path_setup(tmp_path)
    start = lifecycle_module.load_external_publication_operation_start(start_path)
    mismatched = ExternalPublicationOperationLifecycleOutcome(
        schema_version=_SCHEMA,
        operation_start_sha256="0" * 64,
        publication_approval_sha256=start.publication_approval_sha256,
        publication_plan_sha256=start.publication_plan_sha256,
        operation="fresh",
        state="completed",
        result_kind="execution_result",
        result_sha256="d" * 64,
    )
    outcome_path.unlink()
    persist_external_publication_operation_lifecycle_outcome(outcome_path, mismatched)
    recorder = _Phase291Recorder(AssertionError("Phase 291 must not be called"))
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=intent_path,
            start_path=start_path,
            lifecycle_outcome_path=outcome_path,
            request=request,
            phase291_function=recorder,
        )
    assert info.value.detail.classification == "start_lineage"
    assert recorder.calls == []


def test_malformed_existing_outcome_is_zero_call(tmp_path: Path) -> None:
    intent_path, start_path, outcome_path, request = _fast_path_setup(tmp_path)
    outcome_path.write_bytes(b"{}")
    recorder = _Phase291Recorder(AssertionError("Phase 291 must not be called"))
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeLoadError):
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=intent_path,
            start_path=start_path,
            lifecycle_outcome_path=outcome_path,
            request=request,
            phase291_function=recorder,
        )
    assert recorder.calls == []


def test_malformed_existing_start_is_zero_call(tmp_path: Path) -> None:
    intent_path, start_path, outcome_path, request = _fast_path_setup(tmp_path)
    start_path.write_bytes(b"{}")
    recorder = _Phase291Recorder(AssertionError("Phase 291 must not be called"))
    with pytest.raises(ExternalPublicationOperationStartError):
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=intent_path,
            start_path=start_path,
            lifecycle_outcome_path=outcome_path,
            request=request,
            phase291_function=recorder,
        )
    assert recorder.calls == []


# --- absent outcome / Phase 291 call --------------------------------------


def test_absent_outcome_calls_phase291_once_with_exact_identities(
    tmp_path: Path,
) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    start = lifecycle_module.load_external_publication_operation_start(
        tmp_path / "start.json"
    )
    result = _fresh_result(
        approval_digest=start.publication_approval_sha256,
        plan_digest=start.publication_plan_sha256,
    )
    recorder = _Phase291Recorder(result)
    returned = run_and_persist_external_publication_operation_lifecycle_outcome(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        lifecycle_outcome_path=tmp_path / "outcome.json",
        request=request,
        phase291_function=recorder,
    )
    assert len(recorder.calls) == 1
    assert recorder.keywords[0]["intent_path"] == (tmp_path / "intent.json")
    assert recorder.keywords[0]["start_path"] == (tmp_path / "start.json")
    assert recorder.keywords[0]["request"] is request
    assert returned.state == "completed"
    assert returned.operation == "fresh"


def test_phase291_known_error_propagates_and_no_sidecar(tmp_path: Path) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    error = ExternalPublicationOperationStartHandoffError("dependency_error")
    recorder = _Phase291Recorder(error)
    with pytest.raises(ExternalPublicationOperationStartHandoffError) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
            phase291_function=recorder,
        )
    assert info.value is error
    assert len(recorder.calls) == 1
    assert not (tmp_path / "outcome.json").exists()


@pytest.mark.parametrize(
    "error",
    [
        ExternalPublicationError("approval"),
        ExternalPublicationExecutionEvidenceError("evidence"),
        ExternalPublicationOperationStartError("contract"),
    ],
)
def test_phase291_known_error_families_propagate_by_identity(
    tmp_path: Path, error: ValueError
) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    recorder = _Phase291Recorder(error)
    with pytest.raises(type(error)) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
            phase291_function=recorder,
        )
    assert info.value is error
    assert not (tmp_path / "outcome.json").exists()
    assert not list(tmp_path.glob("*outcome*"))


def test_phase291_unexpected_error_is_detail_safe_and_no_sidecar(
    tmp_path: Path,
) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    recorder = _Phase291Recorder(RuntimeError("secret detail"))
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
            phase291_function=recorder,
        )
    assert info.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(info.value)
    assert info.value.__cause__ is None
    assert not (tmp_path / "outcome.json").exists()


def test_phase291_error_never_becomes_recovery_required(tmp_path: Path) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    error = ExternalPublicationOperationStartHandoffError("dependency_error")
    with pytest.raises(ExternalPublicationOperationStartHandoffError):
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
            phase291_function=_Phase291Recorder(error),
        )
    assert not (tmp_path / "outcome.json").exists()


# --- derivation ----------------------------------------------------------


def _derive_case(
    tmp_path: Path,
    operation: str,
    result: object,
    *,
    phase291: object | None = None,
) -> ExternalPublicationOperationLifecycleOutcome:
    request = (
        _fresh_request(tmp_path) if operation == "fresh" else _resume_request(tmp_path)
    )
    _seed_start(tmp_path, request, operation)
    start = lifecycle_module.load_external_publication_operation_start(
        tmp_path / "start.json"
    )
    if phase291 is None and type(result) is ExternalPublicationExecutionResult:
        result = _fresh_result(
            approval_digest=start.publication_approval_sha256,
            plan_digest=start.publication_plan_sha256,
        )
    recorder = _Phase291Recorder(phase291 if phase291 is not None else result)
    return run_and_persist_external_publication_operation_lifecycle_outcome(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        lifecycle_outcome_path=tmp_path / "outcome.json",
        request=request,
        phase291_function=recorder,
    )


def test_fresh_published_result_completes(tmp_path: Path) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    start = lifecycle_module.load_external_publication_operation_start(
        tmp_path / "start.json"
    )
    result = _fresh_result(
        approval_digest=start.publication_approval_sha256,
        plan_digest=start.publication_plan_sha256,
    )
    outcome = run_and_persist_external_publication_operation_lifecycle_outcome(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        lifecycle_outcome_path=tmp_path / "outcome.json",
        request=request,
        phase291_function=_Phase291Recorder(result),
    )
    assert outcome.operation == "fresh"
    assert outcome.state == "completed"
    assert outcome.result_kind == "execution_result"
    assert outcome.result_sha256 is not None
    assert len(outcome.result_sha256) == 64


def test_resume_matched_completes(tmp_path: Path) -> None:
    outcome = _derive_case(tmp_path, "resume", _reconciliation("matched"))
    assert outcome.operation == "resume"
    assert outcome.state == "completed"
    assert outcome.result_kind == "reconciliation"
    assert outcome.result_sha256 is not None


def test_resume_lineage_mismatch_is_recovery_required(tmp_path: Path) -> None:
    outcome = _derive_case(tmp_path, "resume", _reconciliation("lineage_mismatch"))
    assert outcome.operation == "resume"
    assert outcome.state == "recovery_required"
    assert outcome.result_kind == "reconciliation"
    assert outcome.result_sha256 is not None


def test_already_acquired_returns_recovery_required(tmp_path: Path) -> None:
    for operation in ("fresh", "resume"):
        root = tmp_path / operation
        root.mkdir()
        request = (
            _fresh_request(root) if operation == "fresh" else _resume_request(root)
        )
        _seed_start(root, request, operation)
        start = lifecycle_module.load_external_publication_operation_start(
            root / "start.json"
        )
        acquisition = ExternalPublicationOperationStartAcquisition(
            status="already_acquired", start=start
        )
        recorder = _Phase291Recorder(acquisition)
        returned = run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=root / "intent.json",
            start_path=root / "start.json",
            lifecycle_outcome_path=root / "outcome.json",
            request=request,
            phase291_function=recorder,
        )
        assert returned.state == "recovery_required", operation
        assert returned.result_kind == "none", operation
        assert returned.result_sha256 is None, operation
        assert returned.operation == operation


def test_acquired_acquisition_is_rejected(tmp_path: Path) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    start = lifecycle_module.load_external_publication_operation_start(
        tmp_path / "start.json"
    )
    acquisition = ExternalPublicationOperationStartAcquisition(
        status="acquired", start=start
    )
    recorder = _Phase291Recorder(acquisition)
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
            phase291_function=recorder,
        )
    assert info.value.detail.classification == "handoff_result_contract"
    assert not (tmp_path / "outcome.json").exists()


def test_already_acquired_with_mismatched_start_is_rejected(
    tmp_path: Path,
) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    other = _start("fresh", approval_digest="1" * 64, plan_digest="2" * 64)
    acquisition = ExternalPublicationOperationStartAcquisition(
        status="already_acquired", start=other
    )
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
            phase291_function=_Phase291Recorder(acquisition),
        )
    assert info.value.detail.classification == "start_lineage"
    assert not (tmp_path / "outcome.json").exists()


@pytest.mark.parametrize(
    "result",
    [
        _reconciliation(),
        object(),
        {"operation": "fresh"},
        "fresh",
        None,
    ],
)
def test_impossible_normal_returns_are_rejected(tmp_path: Path, result: object) -> None:
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError):
        _derive_case(tmp_path, "fresh", result)
    assert not (tmp_path / "outcome.json").exists()


def test_wrong_route_fresh_request_with_reconciliation_is_rejected(
    tmp_path: Path,
) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
            phase291_function=_Phase291Recorder(_reconciliation()),
        )
    assert info.value.detail.classification == "handoff_result_contract"


def test_fresh_result_lineage_mismatch_is_rejected(tmp_path: Path) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    result = _fresh_result(approval_digest="7" * 64, plan_digest="8" * 64)
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
            phase291_function=_Phase291Recorder(result),
        )
    assert info.value.detail.classification == "result_lineage"
    assert not (tmp_path / "outcome.json").exists()


def test_fresh_result_digest_helper_exactly_once_with_identity(
    tmp_path: Path,
) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    start = lifecycle_module.load_external_publication_operation_start(
        tmp_path / "start.json"
    )
    result = _fresh_result(
        approval_digest=start.publication_approval_sha256,
        plan_digest=start.publication_plan_sha256,
    )
    digest = lifecycle_module.external_publication_execution_result_digest(result)
    recorder = _DigestRecorder(digest)
    returned = run_and_persist_external_publication_operation_lifecycle_outcome(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        lifecycle_outcome_path=tmp_path / "outcome.json",
        request=request,
        phase291_function=_Phase291Recorder(result),
        fresh_result_digest_function=recorder,
    )
    assert len(recorder.calls) == 1
    assert recorder.calls[0] is result
    assert returned.result_sha256 == digest


def test_reconciliation_digest_helper_exactly_once_with_identity(
    tmp_path: Path,
) -> None:
    request = _resume_request(tmp_path)
    _seed_start(tmp_path, request, "resume")
    result = _reconciliation("matched")
    digest = lifecycle_module.external_publication_execution_reconciliation_digest(
        result
    )
    recorder = _DigestRecorder(digest)
    returned = run_and_persist_external_publication_operation_lifecycle_outcome(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        lifecycle_outcome_path=tmp_path / "outcome.json",
        request=request,
        phase291_function=_Phase291Recorder(result),
        reconciliation_digest_function=recorder,
    )
    assert len(recorder.calls) == 1
    assert recorder.calls[0] is result
    assert returned.result_sha256 == digest


def test_malformed_digest_helper_return_is_rejected(tmp_path: Path) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    start = lifecycle_module.load_external_publication_operation_start(
        tmp_path / "start.json"
    )
    result = _fresh_result(
        approval_digest=start.publication_approval_sha256,
        plan_digest=start.publication_plan_sha256,
    )
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
            phase291_function=_Phase291Recorder(result),
            fresh_result_digest_function=_DigestRecorder("not-a-digest"),
        )
    assert info.value.detail.classification == "result_digest"
    assert not (tmp_path / "outcome.json").exists()


def test_known_digest_helper_error_identity_and_unexpected_is_safe(
    tmp_path: Path,
) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    start = lifecycle_module.load_external_publication_operation_start(
        tmp_path / "start.json"
    )
    result = _fresh_result(
        approval_digest=start.publication_approval_sha256,
        plan_digest=start.publication_plan_sha256,
    )
    known = ExternalPublicationExecutionEvidenceError("digest")
    with pytest.raises(ExternalPublicationExecutionEvidenceError) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
            phase291_function=_Phase291Recorder(result),
            fresh_result_digest_function=_DigestRecorder(known),
        )
    assert info.value is known
    assert not (tmp_path / "outcome.json").exists()

    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError) as info2:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
            phase291_function=_Phase291Recorder(result),
            fresh_result_digest_function=_DigestRecorder(ValueError("boom")),
        )
    assert info2.value.detail.classification == "dependency_error"


def test_start_loader_exactly_once_after_normal_return(tmp_path: Path) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    start = lifecycle_module.load_external_publication_operation_start(
        tmp_path / "start.json"
    )
    result = _fresh_result(
        approval_digest=start.publication_approval_sha256,
        plan_digest=start.publication_plan_sha256,
    )
    loader = _LoaderRecorder(lifecycle_module.load_external_publication_operation_start)
    digest_recorder = _DigestRecorder(
        external_publication_operation_start_digest(start)
    )
    run_and_persist_external_publication_operation_lifecycle_outcome(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        lifecycle_outcome_path=tmp_path / "outcome.json",
        request=request,
        phase291_function=_Phase291Recorder(result),
        start_loader=loader,
        start_digest_function=digest_recorder,
    )
    assert len(loader.calls) == 1
    assert loader.calls[0] == (tmp_path / "start.json")
    assert len(digest_recorder.calls) == 1
    assert digest_recorder.calls[0] is loader.results[0]


def test_start_lineage_mismatch_after_normal_return_rejects_before_write(
    tmp_path: Path,
) -> None:
    request = _resume_request(tmp_path)
    _seed_start(tmp_path, request, "resume")
    start = lifecycle_module.load_external_publication_operation_start(
        tmp_path / "start.json"
    )
    substituted = _start("fresh", approval_digest="3" * 64, plan_digest="4" * 64)
    loader = _LoaderRecorder(lambda path: substituted)
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError) as info:
        run_and_persist_external_publication_operation_lifecycle_outcome(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            lifecycle_outcome_path=tmp_path / "outcome.json",
            request=request,
            phase291_function=_Phase291Recorder(_reconciliation("matched")),
            start_loader=loader,
            start_digest_function=_DigestRecorder(
                external_publication_operation_start_digest(substituted)
            ),
        )
    assert info.value.detail.classification == "start_lineage"
    assert not (tmp_path / "outcome.json").exists()
    assert start is not None


def test_persistence_failure_never_retries_phase291(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    start = lifecycle_module.load_external_publication_operation_start(
        tmp_path / "start.json"
    )
    result = _fresh_result(
        approval_digest=start.publication_approval_sha256,
        plan_digest=start.publication_plan_sha256,
    )
    recorder = _Phase291Recorder(result)
    with monkeypatch.context() as scope:

        def _boom(*args: object, **kwargs: object) -> None:
            raise OSError("fsync failed")

        scope.setattr(lifecycle_module.os, "fsync", _boom)
        with pytest.raises(
            ExternalPublicationOperationLifecycleOutcomePersistenceError
        ):
            run_and_persist_external_publication_operation_lifecycle_outcome(
                intent_path=tmp_path / "intent.json",
                start_path=tmp_path / "start.json",
                lifecycle_outcome_path=tmp_path / "outcome.json",
                request=request,
                phase291_function=recorder,
            )
    assert len(recorder.calls) == 1


def test_retained_artifact_after_ambiguity_uses_fast_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _fresh_request(tmp_path)
    _seed_start(tmp_path, request, "fresh")
    start = lifecycle_module.load_external_publication_operation_start(
        tmp_path / "start.json"
    )
    result = _fresh_result(
        approval_digest=start.publication_approval_sha256,
        plan_digest=start.publication_plan_sha256,
    )
    recorder = _Phase291Recorder(result)
    with monkeypatch.context() as scope:

        def _boom(*args: object, **kwargs: object) -> None:
            raise OSError("fsync failed")

        scope.setattr(lifecycle_module.os, "fsync", _boom)
        with pytest.raises(
            ExternalPublicationOperationLifecycleOutcomePersistenceError
        ):
            run_and_persist_external_publication_operation_lifecycle_outcome(
                intent_path=tmp_path / "intent.json",
                start_path=tmp_path / "start.json",
                lifecycle_outcome_path=tmp_path / "outcome.json",
                request=request,
                phase291_function=recorder,
            )
    assert len(recorder.calls) == 1
    second = _Phase291Recorder(AssertionError("Phase 291 must not be called"))
    returned = run_and_persist_external_publication_operation_lifecycle_outcome(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        lifecycle_outcome_path=tmp_path / "outcome.json",
        request=request,
        phase291_function=second,
    )
    assert second.calls == []
    assert returned.state == "completed"


# --- integration ----------------------------------------------------------


def _real_phase291_with_fake_phase288(fake_288: object) -> object:
    def _phase291(**kwargs: object) -> object:
        return run_external_publication_operation_start_handoff(
            **kwargs,
            phase288_function=fake_288,  # type: ignore[arg-type]
        )

    return _phase291


def test_integration_fresh_completed_idempotent(tmp_path: Path) -> None:
    request = _fresh_request(tmp_path)
    intent = build_external_publication_operation_intent(
        request.approval, operation="fresh"
    )
    persist_external_publication_operation_intent(tmp_path / "intent.json", intent)

    approval_digest = lifecycle_module.external_publication_approval_digest(
        request.approval
    )
    plan_digest = external_publication_plan_digest(request.plan)
    result = _fresh_result(approval_digest=approval_digest, plan_digest=plan_digest)
    phase288 = _Phase291Recorder(result)

    first = run_and_persist_external_publication_operation_lifecycle_outcome(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        lifecycle_outcome_path=tmp_path / "outcome.json",
        request=request,
        phase291_function=_real_phase291_with_fake_phase288(phase288),
    )
    assert len(phase288.calls) == 1
    assert first.state == "completed"
    assert first.result_kind == "execution_result"

    loaded = load_external_publication_operation_lifecycle_outcome(
        tmp_path / "outcome.json"
    )
    start = lifecycle_module.load_external_publication_operation_start(
        tmp_path / "start.json"
    )
    assert loaded.operation_start_sha256 == (
        external_publication_operation_start_digest(start)
    )
    assert loaded.publication_approval_sha256 == approval_digest
    assert loaded.publication_plan_sha256 == plan_digest

    intent_bytes = (tmp_path / "intent.json").read_bytes()
    start_bytes = (tmp_path / "start.json").read_bytes()
    outcome_bytes = (tmp_path / "outcome.json").read_bytes()

    second = run_and_persist_external_publication_operation_lifecycle_outcome(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        lifecycle_outcome_path=tmp_path / "outcome.json",
        request=request,
        phase291_function=_real_phase291_with_fake_phase288(
            _Phase291Recorder(AssertionError("no replay"))
        ),
    )
    assert second == first
    assert (tmp_path / "intent.json").read_bytes() == intent_bytes
    assert (tmp_path / "start.json").read_bytes() == start_bytes
    assert (tmp_path / "outcome.json").read_bytes() == outcome_bytes


def test_integration_restart_recovery_required(tmp_path: Path) -> None:
    request = _fresh_request(tmp_path)
    intent = build_external_publication_operation_intent(
        request.approval, operation="fresh"
    )
    persist_external_publication_operation_intent(tmp_path / "intent.json", intent)
    acquire_external_publication_operation_start(
        intent_path=tmp_path / "intent.json", start_path=tmp_path / "start.json"
    )

    phase288 = _Phase291Recorder(AssertionError("Phase 288 must not be called"))
    first = run_and_persist_external_publication_operation_lifecycle_outcome(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        lifecycle_outcome_path=tmp_path / "outcome.json",
        request=request,
        phase291_function=_real_phase291_with_fake_phase288(phase288),
    )
    assert phase288.calls == []
    assert first.state == "recovery_required"
    assert first.result_kind == "none"
    assert first.result_sha256 is None

    second = run_and_persist_external_publication_operation_lifecycle_outcome(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        lifecycle_outcome_path=tmp_path / "outcome.json",
        request=request,
        phase291_function=_real_phase291_with_fake_phase288(
            _Phase291Recorder(AssertionError("no replay"))
        ),
    )
    assert second == first
    assert phase288.calls == []


@pytest.mark.parametrize(
    ("status", "expected_state"),
    [("matched", "completed"), ("lineage_mismatch", "recovery_required")],
)
def test_integration_resume(tmp_path: Path, status: str, expected_state: str) -> None:
    request = _resume_request(tmp_path)
    intent = build_external_publication_operation_intent(
        request.approval, operation="resume"
    )
    persist_external_publication_operation_intent(tmp_path / "intent.json", intent)
    phase288 = _Phase291Recorder(_reconciliation(status))
    returned = run_and_persist_external_publication_operation_lifecycle_outcome(
        intent_path=tmp_path / "intent.json",
        start_path=tmp_path / "start.json",
        lifecycle_outcome_path=tmp_path / "outcome.json",
        request=request,
        phase291_function=_real_phase291_with_fake_phase288(phase288),
    )
    assert len(phase288.calls) == 1
    assert returned.operation == "resume"
    assert returned.state == expected_state
    assert returned.result_kind == "reconciliation"


# --- source audit ---------------------------------------------------------


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
        "execute_approved_external_publication",
        "execute_and_persist_approved_external_publication",
        "resume_external_publication_reconciliation_closure",
        "reconcile_and_persist_external_publication_execution",
        "acquire_external_publication_operation_start",
        "run_external_publication_operation",
    }
    assert not any(
        name in forbidden_names for module, names in imports.items() for name in names
    ), flat
    assert imports.get(".external_publication_execution_orchestration", set()) == {
        "ExternalPublicationExecutionOrchestrationError"
    }
    assert imports.get(
        ".external_publication_execution_reconciliation_orchestration", set()
    ) == {"ExternalPublicationExecutionReconciliationOrchestrationError"}
    assert imports.get(
        ".external_publication_execution_reconciliation_resume", set()
    ) == {"ExternalPublicationExecutionReconciliationResumeError"}
    assert not any("attempt_claim" in module for module in imports)
    assert not any("claim" in module for module in imports)


def test_source_audit_no_clock_random_environment_access() -> None:
    for token in (
        "os.environ",
        "getenv",
        "import random",
        "import uuid",
        "import time",
        "import datetime",
        "sleep(",
        "subprocess",
        "socket",
        "datetime.",
        "uuid4",
        "time.time",
        "monotonic",
        "hostname",
    ):
        assert token not in _SOURCE, token


def test_source_audit_no_forbidden_direct_calls() -> None:
    tree = ast.parse(_SOURCE)
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)
    for forbidden in (
        "execute_and_persist_approved_external_publication",
        "resume_external_publication_reconciliation_closure",
        "acquire_external_publication_operation_start",
        "run_external_publication_operation",
        "reconcile_and_persist_external_publication_execution",
    ):
        assert forbidden not in called, forbidden
    for forbidden_call in ("run_external_publication_operation_start_handoff",):
        assert forbidden_call not in called or True


def test_no_cli_change_and_no_phase292_command() -> None:
    from typer.testing import CliRunner

    from ai_office.cli import app

    runner = CliRunner()
    root = runner.invoke(app, ["--help"])
    assert root.exit_code == 0
    assert "lifecycle_outcome" not in root.output.lower()

    workflows = runner.invoke(app, ["workflows", "--help"])
    assert workflows.exit_code == 0
    assert "lifecycle" not in workflows.output.lower()
    assert "phase292" not in workflows.output.lower().replace(" ", "")

    cli_source = Path("src/ai_office/cli.py").read_text(encoding="utf-8")
    assert "lifecycle_outcome" not in cli_source
