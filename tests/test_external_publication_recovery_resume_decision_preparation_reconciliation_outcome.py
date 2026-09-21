# ruff: noqa: E501

"""Provider-free direct regressions for the Phase 307 outcome boundary."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_resume_decision_preparation_reconciliation_outcome as outcome_module
import tests.test_external_publication_recovery_resume_decision_preparation_reconciliation_handoff as phase306_tests
from ai_office.engine import (
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeCompatibilityError,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeConflictError,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeLoadError,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomePersistenceError,
    ExternalPublicationResumeOperationRequest,
    external_publication_attempt_claim_path,
    external_publication_consumption_key,
    external_publication_execution_reconciliation_canonical_bytes,
    external_publication_execution_reconciliation_digest,
    external_publication_recovery_resume_decision_preparation_reconciliation_outcome_canonical_bytes,
    external_publication_recovery_resume_decision_preparation_reconciliation_outcome_digest,
    external_publication_recovery_resume_decision_preparation_start_authorization_digest,
    load_external_publication_execution_reconciliation,
    load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome,
    load_external_publication_recovery_resume_decision_preparation_start_authorization,
    persist_external_publication_execution_reconciliation,
    persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome,
    run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome,
    run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff,
    run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff,
    serialize_external_publication_recovery_resume_decision_preparation_reconciliation_outcome_canonical,
)

_SCHEMA = "external-publication-recovery-resume-decision-preparation-reconciliation-outcome.v1"
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
_INVALID_MESSAGE = (
    "external publication recovery resume decision preparation reconciliation "
    "outcome is invalid"
)
_PERSISTENCE_MESSAGE = (
    "external publication recovery resume decision preparation reconciliation "
    "outcome persistence failed"
)
_LOAD_MESSAGE = (
    "external publication recovery resume decision preparation reconciliation "
    "outcome could not be loaded"
)
_SOURCE = Path(outcome_module.__file__).read_text(encoding="utf-8")


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


class _StringChild(str):
    pass


class _ReconciliationChild(ExternalPublicationExecutionReconciliation):
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


def _outcome(
    **overrides: object,
) -> ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome:
    values: dict[str, object] = {
        "schema_version": _SCHEMA,
        "decision_preparation_start_authorization_sha256": "1" * 64,
        "decision_preparation_intent_binding_sha256": "2" * 64,
        "decision_preparation_sha256": "3" * 64,
        "recovery_resume_decision_sha256": "4" * 64,
        "operation_intent_sha256": "5" * 64,
        "operation_start_sha256": "6" * 64,
        "publication_approval_sha256": "7" * 64,
        "publication_plan_sha256": "8" * 64,
        "source_operation": "resume",
        "previous_recovery_kind": "already_acquired",
        "recovery_kind": "already_acquired",
        "operation": "resume",
        "state": "recovery_required",
        "result_kind": "none",
        "result_sha256": None,
    }
    values.update(overrides)
    if "recovery_kind" not in overrides:
        values["recovery_kind"] = (
            "already_acquired"
            if values["result_kind"] == "none"
            else "reconciliation_mismatch"
        )
    return ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome(
        **values  # type: ignore[arg-type]
    )


def _canonical_request(
    authorization_path: Path,
    request: ExternalPublicationResumeOperationRequest,
) -> ExternalPublicationResumeOperationRequest:
    authorization = load_external_publication_recovery_resume_decision_preparation_start_authorization(
        authorization_path
    )
    authorization_digest = external_publication_recovery_resume_decision_preparation_start_authorization_digest(
        authorization
    )
    return replace(
        request,
        execution_reconciliation_evidence_path=(
            authorization_path.parent
            / f"{_RECONCILIATION_PREFIX}{authorization_digest}{_SUFFIX}"
        ),
    )


def _paths(
    authorization_path: Path,
) -> tuple[Path, Path, Path]:
    authorization = load_external_publication_recovery_resume_decision_preparation_start_authorization(
        authorization_path
    )
    digest = external_publication_recovery_resume_decision_preparation_start_authorization_digest(
        authorization
    )
    root = authorization_path.parent
    return (
        root / f"{_START_PREFIX}{digest}{_SUFFIX}",
        root / f"{_RECONCILIATION_PREFIX}{digest}{_SUFFIX}",
        root / f"{_OUTCOME_PREFIX}{digest}{_SUFFIX}",
    )


def _assert_error(error: ValueError, classification: str) -> None:
    assert (
        type(error)
        is ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeCompatibilityError
    )
    assert isinstance(
        error,
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError,
    )
    assert str(error) == _INVALID_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert (
        type(error)
        is ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomePersistenceError
    )
    assert str(error) == _PERSISTENCE_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_load_error(error: ValueError, classification: str) -> None:
    assert (
        type(error)
        is ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeLoadError
    )
    assert str(error) == _LOAD_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def test_public_api_model_and_default_dependencies_are_exact() -> None:
    function = run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome
    parameters = inspect.signature(function).parameters
    assert list(parameters) == [
        "start_authorization_path",
        "request",
        "authorization_loader",
        "authorization_digest_function",
        "approval_digest_function",
        "start_loader",
        "start_digest_function",
        "reconciliation_loader",
        "reconciliation_digest_function",
        "phase280_consumption_key_function",
        "phase280_claim_path_function",
        "phase283_function",
        "phase306_function",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in parameters.values()
    )
    assert parameters["phase306_function"].default is (
        outcome_module.run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff
    )
    for forbidden in (
        "phase306_result",
        "route",
        "acquisition",
        "authorization",
        "authorization_digest",
        "start_path",
        "outcome_path",
        "phase290_function",
        "phase304_function",
        "phase305_function",
        "phase287_function",
        "phase288_function",
        "phase285_function",
    ):
        assert forbidden not in parameters


def test_model_has_exact_sixteen_frozen_fields_and_coupling() -> None:
    assert tuple(
        field.name
        for field in dataclasses.fields(
            ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome
        )
    ) == (
        "schema_version",
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
        "state",
        "result_kind",
        "result_sha256",
    )
    value = _outcome(
        state="completed", result_kind="reconciliation", result_sha256="a" * 64
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.state = "recovery_required"  # type: ignore[misc]
    assert (
        _outcome(
            state="completed", result_kind="reconciliation", result_sha256="a" * 64
        ).state
        == "completed"
    )
    assert (
        _outcome(
            state="recovery_required",
            result_kind="reconciliation",
            result_sha256="a" * 64,
        ).result_kind
        == "reconciliation"
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        _outcome(state="completed", result_kind="none")
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        _outcome(result_kind="none", result_sha256="a" * 64)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        _outcome(
            state="completed",
            result_kind="reconciliation",
            result_sha256=None,
        )


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("schema_version", _StringChild("wrong")),
        ("decision_preparation_start_authorization_sha256", "A" * 64),
        ("operation_intent_sha256", None),
        ("source_operation", "fresh"),
        ("previous_recovery_kind", "unknown"),
        ("recovery_kind", "unknown"),
        ("operation", "fresh"),
        ("state", "pending"),
        ("result_kind", "execution_result"),
    ],
)
def test_model_rejects_non_exact_values(field_name: str, bad_value: object) -> None:
    overrides: dict[str, object] = {field_name: bad_value}
    if field_name == "source_operation":
        overrides["previous_recovery_kind"] = "reconciliation_mismatch"
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ) as caught:
        _outcome(**overrides)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "configuration"


def test_canonical_serialization_digest_and_strict_roundtrip(tmp_path: Path) -> None:
    value = _outcome(
        state="completed", result_kind="reconciliation", result_sha256="a" * 64
    )
    canonical = external_publication_recovery_resume_decision_preparation_reconciliation_outcome_canonical_bytes(
        value
    )
    payload = json.loads(canonical)
    assert len(payload) == 16
    assert set(payload) == {
        field.name
        for field in dataclasses.fields(
            ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome
        )
    }
    assert b" " not in canonical
    assert b"\n" not in canonical
    assert (
        external_publication_recovery_resume_decision_preparation_reconciliation_outcome_digest(
            value
        )
        == external_publication_recovery_resume_decision_preparation_reconciliation_outcome_digest(
            value
        )
    )
    path = tmp_path / "outcome.json"
    persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        path, value
    )
    loaded = load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        path
    )
    assert loaded == value
    assert (
        type(loaded)
        is ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not json",
        b"{}",
        b"[]",
        b'{"schema_version":null}',
    ],
)
def test_loader_rejects_malformed_payloads(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "outcome.json"
    path.write_bytes(payload)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeLoadError
    ) as caught:
        load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            path
        )
    _assert_load_error(
        caught.value,
        "keys" if payload in {b"{}", b'{"schema_version":null}'} else "parse",
    )


def test_loader_rejects_duplicate_unknown_missing_and_noncanonical_json(
    tmp_path: Path,
) -> None:
    value = _outcome()
    canonical = serialize_external_publication_recovery_resume_decision_preparation_reconciliation_outcome_canonical(
        value
    )
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        canonical[:-1] + ',"state":"recovery_required"}', encoding="utf-8"
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeLoadError
    ) as caught:
        load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            duplicate
        )
    _assert_load_error(caught.value, "parse")

    extra = tmp_path / "extra.json"
    extra.write_text(canonical[:-1] + ',"extra":1}', encoding="utf-8")
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeLoadError
    ) as caught:
        load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            extra
        )
    _assert_load_error(caught.value, "keys")

    payload = json.loads(canonical)
    payload.pop("state")
    missing = tmp_path / "missing.json"
    missing.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeLoadError
    ) as caught:
        load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            missing
        )
    _assert_load_error(caught.value, "keys")

    pretty = tmp_path / "pretty.json"
    pretty.write_text(
        json.dumps(json.loads(canonical), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeLoadError
    ) as caught:
        load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            pretty
        )
    _assert_load_error(caught.value, "noncanonical")

    constant = tmp_path / "constant.json"
    constant.write_text('{"state":NaN}', encoding="utf-8")
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeLoadError
    ) as caught:
        load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            constant
        )
    _assert_load_error(caught.value, "parse")


def test_append_only_persistence_is_idempotent_conflicting_and_safe(
    tmp_path: Path,
) -> None:
    path = tmp_path / "outcome.json"
    first = _outcome()
    persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        path, first
    )
    before = path.read_bytes()
    persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        path, first
    )
    assert path.read_bytes() == before

    conflict = _outcome(
        state="completed", result_kind="reconciliation", result_sha256="a" * 64
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeConflictError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            path, conflict
        )
    assert caught.value.detail.classification == "conflict"
    assert path.read_bytes() == before

    directory = tmp_path / "directory.json"
    directory.mkdir()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomePersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            directory, first
        )
    _assert_persistence_error(caught.value, "target")

    real = tmp_path / "real.json"
    real.write_bytes(b"x")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomePersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            link, first
        )
    _assert_persistence_error(caught.value, "target")
    assert real.read_bytes() == b"x"


def test_ambiguous_persistence_retains_artifact_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "outcome.json"
    value = _outcome()
    monkeypatch.setattr(
        outcome_module,
        "_fsync_outcome_directory",
        lambda _directory: (_ for _ in ()).throw(OSError("directory sync")),
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomePersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            path, value
        )
    _assert_persistence_error(caught.value, "ambiguous")
    retained = path.read_bytes()
    monkeypatch.undo()
    persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        path, value
    )
    assert path.read_bytes() == retained


def _assert_real_phase306_matched_and_lineage_mismatch_are_durable(
    tmp_path: Path, execution_provider: str, expected_state: str
) -> None:
    root = tmp_path / execution_provider
    authorization_path, base_request, _ = (
        phase306_tests._persist_real_provider_free_resume_lineage(
            root, execution_provider=execution_provider
        )
    )
    request = _canonical_request(authorization_path, base_request)
    outcome = run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        start_authorization_path=authorization_path,
        request=request,
    )
    start_path, reconciliation_path, outcome_path = _paths(authorization_path)
    assert outcome.state == expected_state
    assert outcome.result_kind == "reconciliation"
    assert (
        outcome.result_sha256
        == external_publication_execution_reconciliation_digest(
            load_external_publication_execution_reconciliation(reconciliation_path)
        )
    )
    assert start_path.exists()
    assert reconciliation_path.exists()
    assert outcome_path.exists()


@pytest.mark.parametrize(
    ("execution_provider", "expected_state"),
    [("future-provider", "completed"), ("other-provider", "recovery_required")],
)
def test_real_phase306_default_resume_lineage_uses_canonical_cycle_evidence(
    tmp_path: Path, execution_provider: str, expected_state: str
) -> None:
    _assert_real_phase306_matched_and_lineage_mismatch_are_durable(
        tmp_path, execution_provider, expected_state
    )


def test_crash_after_reconciliation_before_outcome_recovers_durable_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "crash-recovery"
    authorization_path, base_request, _ = (
        phase306_tests._persist_real_provider_free_resume_lineage(root)
    )
    request = _canonical_request(authorization_path, base_request)
    direct = run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=authorization_path,
        request=request,
    )
    assert direct.status == "matched"
    start_path, reconciliation_path, outcome_path = _paths(authorization_path)
    assert start_path.exists()
    assert reconciliation_path.exists()
    assert not outcome_path.exists()

    phase306 = _Recorder(
        delegate=run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff
    )
    observer = _Recorder(
        delegate=outcome_module.reconcile_external_publication_execution
    )
    recovered = run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        start_authorization_path=authorization_path,
        request=request,
        phase306_function=phase306,
        phase283_function=observer,
    )
    assert phase306.call_count == 1
    assert phase306.results[0].route == "recovery_required"  # type: ignore[union-attr]
    assert phase306.results[0].acquisition.status == "already_acquired"  # type: ignore[union-attr]
    assert phase306.calls[0][1] == {
        "start_authorization_path": authorization_path,
        "request": request,
    }
    assert recovered.state == "completed"
    assert recovered.result_kind == "reconciliation"
    assert observer.call_count == 1
    assert outcome_path.exists()

    phase288 = _Recorder()
    route = run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=authorization_path,
        request=request,
        phase288_function=phase288,
    )
    assert route.route == "recovery_required"  # type: ignore[union-attr]
    assert phase288.call_count == 0


def test_marker_only_crash_is_recovery_required_none_without_observation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "marker-only"
    approval = phase306_tests._approval()
    authorization_path = phase306_tests._persist_lineage(root, approval)
    request = _canonical_request(
        authorization_path, phase306_tests._request(root, approval)
    )
    run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
        start_authorization_path=authorization_path
    )
    phase306 = _Recorder(
        delegate=run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff
    )
    observer = _Recorder()
    outcome = run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        start_authorization_path=authorization_path,
        request=request,
        phase306_function=phase306,
        phase283_function=observer,
    )
    assert phase306.call_count == 1
    assert outcome.state == "recovery_required"
    assert outcome.result_kind == "none"
    assert outcome.result_sha256 is None
    assert observer.call_count == 0
    assert _paths(authorization_path)[2].exists()


def test_same_namespace_cycles_keep_distinct_authorization_evidence_and_outcomes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "same-namespace"
    path_a, path_b, base_request, approval = (
        phase306_tests._persist_same_namespace_cycles(root)
    )
    request_a = _canonical_request(path_a, base_request)
    request_b = _canonical_request(path_b, base_request)
    auth_a = load_external_publication_recovery_resume_decision_preparation_start_authorization(
        path_a
    )
    auth_b = load_external_publication_recovery_resume_decision_preparation_start_authorization(
        path_b
    )
    digest_a = external_publication_recovery_resume_decision_preparation_start_authorization_digest(
        auth_a
    )
    digest_b = external_publication_recovery_resume_decision_preparation_start_authorization_digest(
        auth_b
    )
    assert auth_a.operation_intent_sha256 == auth_b.operation_intent_sha256
    assert (
        auth_a.expected_operation_start_sha256 == auth_b.expected_operation_start_sha256
    )
    assert digest_a != digest_b

    first_a = run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        start_authorization_path=path_a, request=request_a
    )
    first_b = run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        start_authorization_path=path_b, request=request_b
    )
    start_a, evidence_a, outcome_a = _paths(path_a)
    start_b, evidence_b, outcome_b = _paths(path_b)
    assert first_a.state == first_b.state == "completed"
    assert start_a != start_b and evidence_a != evidence_b and outcome_a != outcome_b
    assert start_a.exists() and start_b.exists()
    assert evidence_a.exists() and evidence_b.exists()
    assert outcome_a.exists() and outcome_b.exists()
    assert request_a.execution_reconciliation_evidence_path == evidence_a
    assert request_b.execution_reconciliation_evidence_path == evidence_b
    assert approval == base_request.approval

    phase288_a = _Recorder()
    phase288_b = _Recorder()
    second_a = run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=path_a,
        request=request_a,
        phase288_function=phase288_a,
    )
    second_b = run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=path_b,
        request=request_b,
        phase288_function=phase288_b,
    )
    assert second_a.route == second_b.route == "recovery_required"  # type: ignore[union-attr]
    assert phase288_a.call_count == phase288_b.call_count == 0


def test_reconciliation_evidence_tampering_fails_read_only_revalidation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "tamper"
    authorization_path, base_request, _ = (
        phase306_tests._persist_real_provider_free_resume_lineage(root)
    )
    request = _canonical_request(authorization_path, base_request)
    run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=authorization_path, request=request
    )
    _, evidence_path, outcome_path = _paths(authorization_path)
    loaded = outcome_module.load_external_publication_execution_reconciliation(
        evidence_path
    )
    tampered = replace(
        loaded, status="lineage_mismatch", mismatched_fields=("provider",)
    )
    evidence_path.write_bytes(
        external_publication_execution_reconciliation_canonical_bytes(tampered)
    )
    phase306 = _Recorder(
        delegate=run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeCompatibilityError
    ) as caught:
        run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            start_authorization_path=authorization_path,
            request=request,
            phase306_function=phase306,
        )
    _assert_error(caught.value, "reconciliation_lineage")
    assert phase306.call_count == 1
    assert not outcome_path.exists()


def test_existing_outcome_fast_path_returns_loaded_identity_and_phase306_zero_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "fast-path"
    authorization_path, base_request, _ = (
        phase306_tests._persist_real_provider_free_resume_lineage(root)
    )
    request = _canonical_request(authorization_path, base_request)
    first = run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        start_authorization_path=authorization_path, request=request
    )
    loaded: list[object] = []
    original_loader = outcome_module.load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome

    def load(path: Path) -> object:
        value = original_loader(path)
        loaded.append(value)
        return value

    monkeypatch.setattr(
        outcome_module,
        "load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome",
        load,
    )
    phase306 = _Recorder(fault=AssertionError("Phase306 must be zero-call"))
    observer = _Recorder(
        delegate=outcome_module.reconcile_external_publication_execution
    )
    second = run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        start_authorization_path=authorization_path,
        request=request,
        phase306_function=phase306,
        phase283_function=observer,
    )
    assert phase306.call_count == 0
    assert observer.call_count == 1
    assert loaded and second is loaded[0]
    assert second is not first


def test_existing_none_outcome_plus_later_evidence_is_explicit_conflict(
    tmp_path: Path,
) -> None:
    root = tmp_path / "none-conflict"
    authorization_path, base_request, _ = (
        phase306_tests._persist_real_provider_free_resume_lineage(root)
    )
    request = _canonical_request(authorization_path, base_request)
    run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
        start_authorization_path=authorization_path
    )
    first = run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        start_authorization_path=authorization_path, request=request
    )
    assert first.result_kind == "none"
    outcome_path = _paths(authorization_path)[2]
    before = outcome_path.read_bytes()
    reconciliation = outcome_module.reconcile_external_publication_execution(
        claim_path=external_publication_attempt_claim_path(
            request.ledger_directory,
            external_publication_consumption_key(request.approval),
        ),
        execution_evidence_path=request.execution_evidence_path,
    )
    persist_external_publication_execution_reconciliation(
        request.execution_reconciliation_evidence_path,
        reconciliation,
    )
    phase306 = _Recorder(fault=AssertionError("Phase306 must be zero-call"))
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeConflictError
    ) as caught:
        run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            start_authorization_path=authorization_path,
            request=request,
            phase306_function=phase306,
        )
    assert caught.value.detail.classification == "conflict"
    assert phase306.call_count == 0
    assert outcome_path.read_bytes() == before


def test_known_phase306_error_identity_is_preserved_without_outcome(
    tmp_path: Path,
) -> None:
    root = tmp_path / "known-error"
    approval = phase306_tests._approval()
    authorization_path = phase306_tests._persist_lineage(root, approval)
    request = _canonical_request(
        authorization_path, phase306_tests._request(root, approval)
    )
    known = outcome_module.ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffError(
        "dependency_error"
    )
    phase306 = _Recorder(fault=known)
    with pytest.raises(type(known)) as caught:
        run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            start_authorization_path=authorization_path,
            request=request,
            phase306_function=phase306,
        )
    assert caught.value is known
    assert not _paths(authorization_path)[2].exists()


def test_ambiguous_outcome_persistence_does_not_rerun_phase306(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ambiguous-run"
    authorization_path, base_request, _ = (
        phase306_tests._persist_real_provider_free_resume_lineage(root)
    )
    request = _canonical_request(authorization_path, base_request)
    original_fsync = outcome_module._fsync_outcome_directory
    calls = 0

    def fail_once(directory: Path) -> None:
        nonlocal calls
        calls += 1
        original_fsync(directory)
        raise OSError("ambiguous after fsync")

    monkeypatch.setattr(outcome_module, "_fsync_outcome_directory", fail_once)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomePersistenceError
    ) as caught:
        run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            start_authorization_path=authorization_path, request=request
        )
    _assert_persistence_error(caught.value, "ambiguous")
    assert calls == 1
    outcome_path = _paths(authorization_path)[2]
    retained = outcome_path.read_bytes()
    monkeypatch.undo()
    phase306 = _Recorder(fault=AssertionError("Phase306 must not retry"))
    result = run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
        start_authorization_path=authorization_path,
        request=request,
        phase306_function=phase306,
    )
    assert phase306.call_count == 0
    assert result.result_kind == "reconciliation"
    assert outcome_path.read_bytes() == retained


def test_preflight_exact_path_request_and_canonical_evidence_before_phase306(
    tmp_path: Path,
) -> None:
    root = tmp_path / "preflight"
    approval = phase306_tests._approval()
    authorization_path = phase306_tests._persist_lineage(root, approval)
    request = _canonical_request(
        authorization_path, phase306_tests._request(root, approval)
    )
    phase306 = _Recorder()

    child_path = _PathChild(str(authorization_path))
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ) as caught:
        run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            start_authorization_path=child_path,
            request=request,
            phase306_function=phase306,
        )
    _assert_error(caught.value, "path_type")
    assert phase306.call_count == 0

    malformed_evidence = replace(
        request,
        execution_reconciliation_evidence_path=root / "not-canonical.json",
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ) as caught:
        run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            start_authorization_path=authorization_path,
            request=malformed_evidence,
            phase306_function=phase306,
        )
    _assert_error(caught.value, "reconciliation_path")
    assert phase306.call_count == 0

    mismatched_approval = replace(approval, approval_id="different-approval")
    bad_request = replace(request, approval=mismatched_approval)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ) as caught:
        run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            start_authorization_path=authorization_path,
            request=bad_request,
            phase306_function=phase306,
        )
    _assert_error(caught.value, "request_lineage")
    assert phase306.call_count == 0


def test_wrong_authorization_filename_is_zero_call_to_phase306(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wrong-name"
    approval = phase306_tests._approval()
    good = phase306_tests._persist_lineage(root, approval)
    wrong = root / f"{_AUTHORIZATION_PREFIX}{'f' * 64}{_SUFFIX}"
    wrong.write_bytes(good.read_bytes())
    request = _canonical_request(wrong, phase306_tests._request(root, approval))
    phase306 = _Recorder()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ) as caught:
        run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            start_authorization_path=wrong,
            request=request,
            phase306_function=phase306,
        )
    _assert_error(caught.value, "authorization_path")
    assert phase306.call_count == 0


def test_source_audit_excludes_forbidden_direct_boundaries_and_ambient_state() -> None:
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
    forbidden = {
        "run_external_publication_operation",
        "resume_external_publication_reconciliation_closure",
        "reconcile_and_persist_external_publication_execution",
        "execute_and_persist_approved_external_publication",
        "acquire_external_publication_operation_start",
        "run_external_publication_operation_start_handoff",
        "run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff",
        "route_external_publication_recovery_resume_decision_preparation_start_acquisition",
        "persist_external_publication_execution_reconciliation",
        "time",
        "random",
        "uuid",
        "socket",
        "subprocess",
    }
    assert not forbidden.intersection(imported_names | imported_from)
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not forbidden.intersection(called_names)
    for token in (
        "os.environ",
        "os.getenv",
        "uuid4",
        "token_hex",
        "Path.resolve",
        ".resolve(",
        "realpath",
        "normpath",
        "abspath",
        "samefile",
        "readlink",
        "phase285_function",
        "phase286_function",
        "phase287_function",
        "phase288_function",
        "phase290_function",
        "phase304_function",
        "phase305_function",
    ):
        assert token not in _SOURCE


def test_engine_exports_exact_phase307_public_symbols() -> None:
    expected = set(outcome_module.__all__)
    from ai_office import engine

    assert expected == {
        "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome",
        "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeCompatibilityError",
        "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeConflictError",
        "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError",
        "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeFailureDetail",
        "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeLoadError",
        "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomePersistenceError",
        "external_publication_recovery_resume_decision_preparation_reconciliation_outcome_canonical_bytes",
        "external_publication_recovery_resume_decision_preparation_reconciliation_outcome_digest",
        "load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome",
        "persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome",
        "run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome",
        "serialize_external_publication_recovery_resume_decision_preparation_reconciliation_outcome_canonical",
    }
    for name in expected:
        assert getattr(engine, name) is getattr(outcome_module, name)


@pytest.mark.parametrize(
    ("execution_provider", "expected_status"),
    [("future-provider", "matched"), ("other-provider", "lineage_mismatch")],
)
def test_read_only_observer_classifies_real_provider_free_lineage(
    tmp_path: Path, execution_provider: str, expected_status: str
) -> None:
    root = tmp_path / f"observer-{execution_provider}"
    authorization_path, base_request, _ = (
        phase306_tests._persist_real_provider_free_resume_lineage(
            root, execution_provider=execution_provider
        )
    )
    request = _canonical_request(authorization_path, base_request)
    run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
        start_authorization_path=authorization_path, request=request
    )
    reconciliation = outcome_module.load_external_publication_execution_reconciliation(
        request.execution_reconciliation_evidence_path
    )
    assert reconciliation.status == expected_status
    assert external_publication_execution_reconciliation_digest(reconciliation)
