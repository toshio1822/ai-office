"""Focused provider-free tests for the Phase 286 reconciliation closure."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_office.engine.external_publication as claim_module
import ai_office.engine.external_publication_execution as execution_module
from ai_office.engine import (
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationError,
    ExternalPublicationExecutionReconciliationEvidenceConflictError,
    ExternalPublicationExecutionReconciliationEvidenceError,
    ExternalPublicationExecutionReconciliationEvidencePersistenceError,
    ExternalPublicationExecutionReconciliationOrchestrationCompatibilityError,
    ExternalPublicationExecutionReconciliationOrchestrationError,
    ExternalPublicationExecutionResult,
    ExternalPublicationPlan,
    approve_external_publication,
    claim_external_publication_attempt,
    external_publication_attempt_claim_path,
    load_external_publication_execution_reconciliation,
    persist_external_publication_execution_result,
    reconcile_and_persist_external_publication_execution,
)

execution_evidence_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_evidence"
)
phase285_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_orchestration"
)
reconciliation_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_reconciliation"
)
reconciliation_evidence_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_reconciliation_evidence"
)
orchestration_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_reconciliation_orchestration"
)

_SCHEMA = "external-publication-execution-reconciliation.v1"
_CLAIM_DIGEST = "a" * 64
_EXECUTION_DIGEST = "b" * 64
_FIELDS = (
    "publication_attempt_claim_sha256",
    "regeneration_id",
    "publication_plan_sha256",
    "publication_approval_sha256",
    "business_output_sha256",
    "output_byte_length",
    "provider",
    "publication_target_sha256",
)
_OUTPUT = b"phase-286-output"


def _matched() -> ExternalPublicationExecutionReconciliation:
    return ExternalPublicationExecutionReconciliation(
        schema_version=_SCHEMA,
        claim_sha256=_CLAIM_DIGEST,
        execution_evidence_sha256=_EXECUTION_DIGEST,
        status="matched",
        mismatched_fields=(),
    )


def _lineage_mismatch() -> ExternalPublicationExecutionReconciliation:
    return ExternalPublicationExecutionReconciliation(
        schema_version=_SCHEMA,
        claim_sha256=_CLAIM_DIGEST,
        execution_evidence_sha256=_EXECUTION_DIGEST,
        status="lineage_mismatch",
        mismatched_fields=_FIELDS,
    )


def _forged_instance(cls: type[object], source: object) -> object:
    value = object.__new__(cls)
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        object.__setattr__(value, field.name, getattr(source, field.name))
    return value


def _assert_orchestration_error(error: ValueError, classification: str) -> None:
    assert (
        type(error)
        is ExternalPublicationExecutionReconciliationOrchestrationCompatibilityError
    )
    assert isinstance(
        error, ExternalPublicationExecutionReconciliationOrchestrationError
    )
    assert isinstance(error, ValueError)
    assert (
        str(error)
        == "external publication execution reconciliation orchestration is blocked"
    )
    assert error.detail.classification == classification


def _assert_evidence_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationExecutionReconciliationEvidenceError
    assert (
        str(error)
        == "external publication execution reconciliation evidence is invalid"
    )
    assert error.detail.classification == classification


def _args(tmp_path: Path) -> dict[str, object]:
    return {
        "claim_path": tmp_path / "claim.json",
        "execution_evidence_path": tmp_path / "execution-evidence.json",
        "reconciliation_evidence_path": tmp_path / "reconciliation-evidence.json",
    }


def _invoke(
    args: dict[str, object],
    *,
    phase283,
    phase284,
) -> ExternalPublicationExecutionReconciliation:
    return reconcile_and_persist_external_publication_execution(
        **args,
        phase283_function=phase283,
        phase284_persistence_function=phase284,
    )  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Public surface and zero-side-effect contract
# ---------------------------------------------------------------------------


def test_public_signature_and_default_dependencies_are_exact() -> None:
    function = reconcile_and_persist_external_publication_execution
    parameters = list(inspect.signature(function).parameters.values())

    assert [parameter.name for parameter in parameters[:3]] == [
        "claim_path",
        "execution_evidence_path",
        "reconciliation_evidence_path",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in parameters
    )
    assert parameters[3].name == "phase283_function"
    assert (
        parameters[3].default
        is reconciliation_module.reconcile_external_publication_execution
    )
    assert parameters[4].name == "phase284_persistence_function"
    default_persistence = getattr(
        reconciliation_evidence_module,
        "persist_external_publication_execution_reconciliation",
    )
    assert parameters[4].default is default_persistence
    assert (
        orchestration_module.reconcile_and_persist_external_publication_execution
        is function
    )
    assert (
        "reconcile_and_persist_external_publication_execution"
        in orchestration_module.__all__
    )


def test_orchestration_source_has_no_retry_or_forbidden_dependency_access() -> None:
    source = inspect.getsource(orchestration_module)
    tree = ast.parse(source)

    assert not any(
        isinstance(node, (ast.For, ast.While, ast.AsyncFor)) for node in ast.walk(tree)
    )
    forbidden = (
        "execute_and_persist_approved_external_publication",
        "execute_approved_external_publication",
        "claim_external_publication_attempt",
        "load_external_publication_attempt_claim",
        "persist_external_publication_execution_result",
        "load_external_publication_execution_result",
        "external_publication_execution_result_digest",
        "serialize_external_publication_execution_result_canonical",
        "external_publication_execution_reconciliation_canonical_bytes",
        "external_publication_execution_reconciliation_digest",
        "load_external_publication_execution_reconciliation",
        "serialize_external_publication_execution_reconciliation_canonical",
        "preflight",
        "read_bytes",
        "write_bytes",
        "mkdir",
        "unlink",
        "socket",
        "random",
        "uuid",
        "environ",
        "subprocess",
        "requests",
    )
    assert all(name not in source for name in forbidden)

    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported_modules <= {
        "__future__",
        "collections.abc",
        "dataclasses",
        "pathlib",
        "typing",
        "ai_office.engine.external_publication_execution_reconciliation",
        "ai_office.engine.external_publication_execution_reconciliation_evidence",
    }


def test_route_has_no_direct_lower_boundary_or_filesystem_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _args(tmp_path)
    result = _matched()
    calls: list[str] = []

    def forbidden(name: str):
        def fail(*_args: object, **_kwargs: object) -> object:
            calls.append(name)
            raise AssertionError(f"forbidden dependency called: {name}")

        return fail

    for owner, name in (
        (phase285_module, "execute_and_persist_approved_external_publication"),
        (execution_module, "execute_approved_external_publication"),
        (claim_module, "claim_external_publication_attempt"),
        (claim_module, "load_external_publication_attempt_claim"),
        (execution_evidence_module, "persist_external_publication_execution_result"),
        (execution_evidence_module, "load_external_publication_execution_result"),
        (execution_evidence_module, "external_publication_execution_result_digest"),
        (
            execution_evidence_module,
            "serialize_external_publication_execution_result_canonical",
        ),
        (
            reconciliation_evidence_module,
            "load_external_publication_execution_reconciliation",
        ),
        (
            reconciliation_evidence_module,
            "external_publication_execution_reconciliation_digest",
        ),
        (
            reconciliation_evidence_module,
            "serialize_external_publication_execution_reconciliation_canonical",
        ),
    ):
        monkeypatch.setattr(owner, name, forbidden(name), raising=False)

    for name in ("open", "read_bytes", "write_bytes", "mkdir", "unlink"):
        monkeypatch.setattr(Path, name, forbidden(f"Path.{name}"))

    returned = _invoke(
        args,
        phase283=lambda **_kwargs: result,
        phase284=lambda _path, _value: None,
    )
    assert returned is result
    assert calls == []


# ---------------------------------------------------------------------------
# Exact linear order, identities, valid statuses, and integrity
# ---------------------------------------------------------------------------


def test_success_is_exact_phase283_phase284_order_and_all_path_identities(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    result = _matched()
    order: list[str] = []
    phase283_calls: list[dict[str, object]] = []
    phase284_calls: list[tuple[Path, object]] = []

    def phase283(**kwargs: object) -> ExternalPublicationExecutionReconciliation:
        order.append("phase283")
        phase283_calls.append(kwargs)
        return result

    def phase284(path: Path, value: object) -> None:
        order.append("phase284")
        phase284_calls.append((path, value))

    returned = _invoke(args, phase283=phase283, phase284=phase284)

    assert order == ["phase283", "phase284"]
    assert len(phase283_calls) == 1
    assert set(phase283_calls[0]) == {"claim_path", "execution_evidence_path"}
    assert phase283_calls[0]["claim_path"] is args["claim_path"]
    assert (
        phase283_calls[0]["execution_evidence_path"] is args["execution_evidence_path"]
    )
    assert len(phase284_calls) == 1
    assert phase284_calls[0][0] is args["reconciliation_evidence_path"]
    assert phase284_calls[0][1] is result
    assert returned is result


def test_lineage_mismatch_is_a_valid_persisted_observation_without_retry(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    result = _lineage_mismatch()
    calls = {"phase283": 0, "phase284": 0}

    def phase283(**kwargs: object) -> ExternalPublicationExecutionReconciliation:
        del kwargs
        calls["phase283"] += 1
        return result

    def phase284(path: Path, value: object) -> None:
        calls["phase284"] += 1
        assert path is args["reconciliation_evidence_path"]
        assert value is result
        assert value.status == "lineage_mismatch"  # type: ignore[union-attr]

    returned = _invoke(args, phase283=phase283, phase284=phase284)
    assert returned is result
    assert calls == {"phase283": 1, "phase284": 1}


def test_wrong_subclass_lookalike_mapping_and_malformed_results_are_rejected(
    tmp_path: Path,
) -> None:
    class ReconciliationSubclass(ExternalPublicationExecutionReconciliation):
        pass

    source = _matched()
    subclass = _forged_instance(ReconciliationSubclass, source)
    # Forge a concrete exact instance whose status violates the model
    # invariant; construction is intentionally bypassed so Phase 286 owns the
    # output contract check.
    malformed = object.__new__(ExternalPublicationExecutionReconciliation)
    object.__setattr__(malformed, "schema_version", _SCHEMA)
    object.__setattr__(malformed, "claim_sha256", _CLAIM_DIGEST)
    object.__setattr__(malformed, "execution_evidence_sha256", _EXECUTION_DIGEST)
    object.__setattr__(malformed, "status", "matched")
    object.__setattr__(malformed, "mismatched_fields", _FIELDS)

    bad_values = [SimpleNamespace(), {}, {"status": "matched"}, subclass, malformed]
    for bad_value in bad_values:
        args = _args(tmp_path / str(len(bad_values)))
        calls = 0

        def phase284(_path: Path, _value: object) -> None:
            nonlocal calls
            calls += 1

        with pytest.raises(
            ExternalPublicationExecutionReconciliationOrchestrationCompatibilityError
        ) as error:
            _invoke(
                args,
                phase283=lambda **_kwargs: bad_value,
                phase284=phase284,
            )
        _assert_orchestration_error(error.value, "reconciliation_contract")
        assert calls == 0


@pytest.mark.parametrize(
    ("field_name", "new_value"),
    [
        ("schema_version", "not-the-schema"),
        ("claim_sha256", "c" * 64),
        ("execution_evidence_sha256", "d" * 64),
        ("status", "not-a-status"),
        ("mismatched_fields", _FIELDS),
    ],
)
def test_snapshot_detects_mutation_of_each_reconciliation_field(
    tmp_path: Path,
    field_name: str,
    new_value: object,
) -> None:
    args = _args(tmp_path)
    result = _matched()
    calls = {"phase283": 0, "phase284": 0}

    def phase283(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        calls["phase283"] += 1
        return result

    def phase284(_path: Path, value: object) -> None:
        calls["phase284"] += 1
        object.__setattr__(value, field_name, new_value)

    with pytest.raises(
        ExternalPublicationExecutionReconciliationOrchestrationCompatibilityError
    ) as error:
        _invoke(args, phase283=phase283, phase284=phase284)
    _assert_orchestration_error(error.value, "result_mutation")
    assert calls == {"phase283": 1, "phase284": 1}


def test_non_none_persistence_return_is_contract_failure_without_retry(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    result = _matched()
    calls = {"phase283": 0, "phase284": 0}

    def phase283(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        calls["phase283"] += 1
        return result

    def phase284(_path: Path, _value: object) -> object:
        calls["phase284"] += 1
        return False

    with pytest.raises(
        ExternalPublicationExecutionReconciliationOrchestrationCompatibilityError
    ) as error:
        _invoke(args, phase283=phase283, phase284=phase284)
    _assert_orchestration_error(error.value, "persistence_contract")
    assert calls == {"phase283": 1, "phase284": 1}


# ---------------------------------------------------------------------------
# Known-error identity and zero-retry behavior
# ---------------------------------------------------------------------------


def test_known_phase283_error_is_same_object_and_blocks_phase284(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    sentinel = ExternalPublicationExecutionReconciliationError("claim")
    calls = 0

    def phase284(_path: Path, _value: object) -> None:
        nonlocal calls
        calls += 1

    def phase283(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        raise sentinel

    with pytest.raises(type(sentinel)) as error:
        _invoke(args, phase283=phase283, phase284=phase284)
    assert error.value is sentinel
    assert calls == 0


def test_unexpected_phase283_exception_is_fixed_detail_safe_and_blocks_phase284(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    calls = 0

    def phase284(_path: Path, _value: object) -> None:
        nonlocal calls
        calls += 1

    def phase283(**_kwargs: object) -> object:
        raise RuntimeError("claim path and private dependency detail")

    with pytest.raises(
        ExternalPublicationExecutionReconciliationOrchestrationCompatibilityError
    ) as error:
        _invoke(args, phase283=phase283, phase284=phase284)
    _assert_orchestration_error(error.value, "dependency_error")
    assert "private" not in str(error.value)
    assert "claim path" not in str(error.value)
    assert calls == 0


@pytest.mark.parametrize(
    "sentinel",
    [
        ExternalPublicationExecutionReconciliationEvidenceConflictError(),
        ExternalPublicationExecutionReconciliationEvidencePersistenceError("ambiguous"),
    ],
)
def test_known_phase284_conflict_and_ambiguous_errors_are_same_object_and_not_retried(
    tmp_path: Path,
    sentinel: ExternalPublicationExecutionReconciliationEvidenceError,
) -> None:
    args = _args(tmp_path)
    result = _matched()
    calls = {"phase283": 0, "phase284": 0}

    def phase283(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        calls["phase283"] += 1
        return result

    def phase284(_path: Path, value: object) -> None:
        calls["phase284"] += 1
        assert value is result
        raise sentinel

    with pytest.raises(type(sentinel)) as error:
        _invoke(args, phase283=phase283, phase284=phase284)
    assert error.value is sentinel
    assert calls == {"phase283": 1, "phase284": 1}


def test_unexpected_phase284_exception_is_fixed_and_never_retried(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    result = _matched()
    calls = {"phase283": 0, "phase284": 0}

    def phase283(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        calls["phase283"] += 1
        return result

    def phase284(_path: Path, _value: object) -> None:
        calls["phase284"] += 1
        raise RuntimeError("ambiguous persistence implementation detail")

    with pytest.raises(
        ExternalPublicationExecutionReconciliationOrchestrationCompatibilityError
    ) as error:
        _invoke(args, phase283=phase283, phase284=phase284)
    _assert_orchestration_error(error.value, "dependency_error")
    assert "implementation detail" not in str(error.value)
    assert calls == {"phase283": 1, "phase284": 1}


def test_noncallable_dependencies_fail_before_either_boundary(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    with pytest.raises(
        ExternalPublicationExecutionReconciliationOrchestrationCompatibilityError
    ) as error:
        reconcile_and_persist_external_publication_execution(
            **args,  # type: ignore[arg-type]
            phase283_function=None,  # type: ignore[arg-type]
        )
    _assert_orchestration_error(error.value, "configuration")


# ---------------------------------------------------------------------------
# Real Phase 283 + Phase 284 boundary integration
# ---------------------------------------------------------------------------


def _real_sidecars(
    tmp_path: Path,
    *,
    execution_provider: str = "future-provider",
) -> tuple[Path, Path, Path, bytes, bytes]:
    plan = ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id="regen-286-real-boundary",
        reconciliation_evidence_sha256="f" * 64,
        receipt_sha256="1" * 64,
        business_output_sha256=hashlib.sha256(_OUTPUT).hexdigest(),
        output_byte_length=len(_OUTPUT),
        provider="future-provider",
        publication_target_sha256="e" * 64,
    )
    approval = approve_external_publication(
        plan,
        approved_by="human-reviewer-286",
        approval_id="approval-286-real-boundary",
    )
    ledger_directory = tmp_path / "ledger"
    ledger_directory.mkdir()
    claim = claim_external_publication_attempt(ledger_directory, plan, approval)
    claim_path = external_publication_attempt_claim_path(
        ledger_directory, claim.consumption_key
    )

    execution_result = ExternalPublicationExecutionResult(
        schema_version="external-publication-execution-result.v1",
        regeneration_id=claim.regeneration_id,
        publication_attempt_claim_sha256=claim.digest,
        publication_plan_sha256=claim.publication_plan_sha256,
        publication_approval_sha256=claim.publication_approval_sha256,
        business_output_sha256=claim.business_output_sha256,
        output_byte_length=claim.output_byte_length,
        provider=execution_provider,
        publication_target_sha256=claim.publication_target_sha256,
        publication_id="publication-286-local",
        status="published",
    )
    execution_evidence_path = tmp_path / "execution-evidence.json"
    persist_external_publication_execution_result(
        execution_evidence_path, execution_result
    )
    return (
        claim_path,
        execution_evidence_path,
        tmp_path / "reconciliation-evidence.json",
        claim_path.read_bytes(),
        execution_evidence_path.read_bytes(),
    )


def test_real_default_boundaries_match_then_idempotently_recover_without_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim_path, execution_path, reconciliation_path, claim_before, execution_before = (
        _real_sidecars(tmp_path)
    )

    def forbidden_provider(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Phase 281/provider execution must remain unused")

    monkeypatch.setattr(
        execution_module,
        "execute_approved_external_publication",
        forbidden_provider,
    )

    first = reconcile_and_persist_external_publication_execution(
        claim_path=claim_path,
        execution_evidence_path=execution_path,
        reconciliation_evidence_path=reconciliation_path,
    )
    evidence_after_first = reconciliation_path.read_bytes()
    second = reconcile_and_persist_external_publication_execution(
        claim_path=claim_path,
        execution_evidence_path=execution_path,
        reconciliation_evidence_path=reconciliation_path,
    )

    assert type(first) is ExternalPublicationExecutionReconciliation
    assert first.status == "matched"
    assert first.mismatched_fields == ()
    assert type(second) is ExternalPublicationExecutionReconciliation
    assert second.status == "matched"
    assert reconciliation_path.is_file()
    assert (
        load_external_publication_execution_reconciliation(reconciliation_path) == first
    )
    assert evidence_after_first == reconciliation_path.read_bytes()
    assert claim_before == claim_path.read_bytes()
    assert execution_before == execution_path.read_bytes()
    assert reconciliation_path.read_bytes() == evidence_after_first


def test_real_default_lineage_mismatch_is_persisted_as_valid_evidence(
    tmp_path: Path,
) -> None:
    claim_path, execution_path, reconciliation_path, _, _ = _real_sidecars(
        tmp_path,
        execution_provider="different-provider",
    )

    result = reconcile_and_persist_external_publication_execution(
        claim_path=claim_path,
        execution_evidence_path=execution_path,
        reconciliation_evidence_path=reconciliation_path,
    )

    assert result.status == "lineage_mismatch"
    assert result.mismatched_fields == ("provider",)
    persisted = load_external_publication_execution_reconciliation(reconciliation_path)
    assert persisted == result
    assert persisted.status == "lineage_mismatch"
    assert persisted.mismatched_fields == ("provider",)
