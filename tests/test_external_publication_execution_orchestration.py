"""Focused provider-free tests for the Phase 285 publication orchestration."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib
import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.engine import (
    ExternalPublicationApproval,
    ExternalPublicationAttemptAlreadyConsumedError,
    ExternalPublicationAttemptClaimPersistenceError,
    ExternalPublicationError,
    ExternalPublicationExecutionAmbiguousError,
    ExternalPublicationExecutionError,
    ExternalPublicationExecutionEvidenceConflictError,
    ExternalPublicationExecutionEvidencePersistenceError,
    ExternalPublicationExecutionOrchestrationCompatibilityError,
    ExternalPublicationExecutionOrchestrationError,
    ExternalPublicationExecutionResult,
    ExternalPublicationPlan,
    ExternalPublicationTarget,
    ExternalPublicationTransportReceipt,
    PublicationRegenerationExportReconciliation,
    approve_external_publication,
    build_external_publication_plan,
    execute_and_persist_approved_external_publication,
    persist_publication_regeneration_export_reconciliation,
    preflight_external_publication_execution_result_path,
)

execution_module = importlib.import_module(
    "ai_office.engine.external_publication_execution"
)
evidence_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_evidence"
)
orchestration_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_orchestration"
)
reconciliation_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_reconciliation"
)
reconciliation_evidence_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_reconciliation_evidence"
)

_RESULT_SCHEMA = "external-publication-execution-result.v1"
_PLAN_SCHEMA = "external-publication-plan.v1"
_TARGET_SCHEMA = "external-publication-target.v1"
_OUTPUT = b"phase-285-output"


def _result() -> ExternalPublicationExecutionResult:
    return ExternalPublicationExecutionResult(
        schema_version=_RESULT_SCHEMA,
        regeneration_id="regen-285-orchestration",
        publication_attempt_claim_sha256="a" * 64,
        publication_plan_sha256="b" * 64,
        publication_approval_sha256="c" * 64,
        business_output_sha256="d" * 64,
        output_byte_length=len(_OUTPUT),
        provider="future-provider",
        publication_target_sha256="e" * 64,
        publication_id="publication-285-1",
        status="published",
    )


def _plan() -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version=_PLAN_SCHEMA,
        regeneration_id="regen-285-orchestration",
        reconciliation_evidence_sha256="f" * 64,
        receipt_sha256="1" * 64,
        business_output_sha256="d" * 64,
        output_byte_length=len(_OUTPUT),
        provider="future-provider",
        publication_target_sha256="e" * 64,
    )


def _target() -> ExternalPublicationTarget:
    return ExternalPublicationTarget(
        schema_version=_TARGET_SCHEMA,
        provider="future-provider",
        destination_id="destination-285",
    )


def _approval(plan: ExternalPublicationPlan) -> ExternalPublicationApproval:
    return approve_external_publication(
        plan,
        approved_by="human-reviewer-285",
        approval_id="approval-285-1",
    )


def _args(tmp_path: Path) -> dict[str, object]:
    plan = _plan()
    return {
        "execution_evidence_path": tmp_path / "execution-evidence.json",
        "reconciliation_evidence_path": tmp_path / "reconciliation-evidence.json",
        "output_path": tmp_path / "business-output.bin",
        "ledger_directory": tmp_path / "ledger",
        "plan": plan,
        "approval": _approval(plan),
        "target": _target(),
        "transport": lambda *_args: ExternalPublicationTransportReceipt(
            "unused-by-fake-phase281"
        ),
    }


def _invoke(
    args: dict[str, object],
    *,
    preflight,
    phase281,
    persistence,
) -> ExternalPublicationExecutionResult:
    return execute_and_persist_approved_external_publication(
        **args,
        phase282_preflight_function=preflight,
        phase281_function=phase281,
        phase282_persistence_function=persistence,
    )  # type: ignore[arg-type]


def _assert_orchestration_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationExecutionOrchestrationCompatibilityError
    assert isinstance(error, ExternalPublicationExecutionOrchestrationError)
    assert isinstance(error, ValueError)
    assert str(error) == "external publication execution orchestration is blocked"
    assert error.detail.classification == classification


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationExecutionEvidencePersistenceError
    assert str(error) == "external publication execution evidence persistence failed"
    assert error.detail.classification == classification


# ---------------------------------------------------------------------------
# Phase 282 fresh-target preflight
# ---------------------------------------------------------------------------


def test_preflight_is_exported_and_requires_exact_concrete_path(tmp_path: Path) -> None:
    assert (
        evidence_module.preflight_external_publication_execution_result_path
        is preflight_external_publication_execution_result_path
    )
    assert (
        "preflight_external_publication_execution_result_path"
        in evidence_module.__all__
    )

    with pytest.raises(ExternalPublicationExecutionEvidencePersistenceError) as error:
        preflight_external_publication_execution_result_path(
            str(tmp_path / "result.json")  # type: ignore[arg-type]
        )
    _assert_persistence_error(error.value, "path_type")


@pytest.mark.parametrize(
    "bad_kind", ["missing_parent", "existing", "directory", "symlink", "fifo"]
)
def test_preflight_rejects_unusable_targets_without_creating_or_reading(
    tmp_path: Path,
    bad_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "evidence"
    parent.mkdir()
    target = parent / "result.json"
    before: bytes | None = None

    if bad_kind == "missing_parent":
        target = tmp_path / "does-not-exist" / "result.json"
    elif bad_kind == "existing":
        target.write_bytes(b"existing evidence")
        before = target.read_bytes()
    elif bad_kind == "directory":
        target.mkdir()
    elif bad_kind == "symlink":
        target.symlink_to(parent / "missing-target")
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO creation is unavailable")
        os.mkfifo(target)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("preflight performed filesystem mutation or content read")

    monkeypatch.setattr(evidence_module.Path, "open", forbidden)
    monkeypatch.setattr(evidence_module.Path, "read_bytes", forbidden)
    monkeypatch.setattr(evidence_module.Path, "write_bytes", forbidden)
    monkeypatch.setattr(evidence_module.Path, "mkdir", forbidden)
    monkeypatch.setattr(evidence_module.Path, "unlink", forbidden)

    with pytest.raises(ExternalPublicationExecutionEvidencePersistenceError) as error:
        preflight_external_publication_execution_result_path(target)
    expected = {
        "missing_parent": "parent",
        "existing": "target_exists",
        "directory": "target",
        "symlink": "target",
        "fifo": "target",
    }[bad_kind]
    _assert_persistence_error(error.value, expected)
    if bad_kind == "missing_parent":
        assert not target.parent.exists()
        assert not target.exists()
    elif bad_kind == "existing":
        assert target.is_file()
        assert target.stat().st_size == len(before or b"")
    elif bad_kind == "directory":
        assert target.is_dir()
    elif bad_kind == "symlink":
        assert target.is_symlink()
    else:
        assert target.exists()


def test_preflight_missing_target_is_read_only_and_does_not_reserve_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "new-result.json"
    calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> object:
        calls.append("forbidden")
        raise AssertionError("preflight attempted a mutation or content read")

    monkeypatch.setattr(evidence_module.Path, "open", forbidden)
    monkeypatch.setattr(evidence_module.Path, "read_bytes", forbidden)
    monkeypatch.setattr(evidence_module.Path, "write_bytes", forbidden)
    monkeypatch.setattr(evidence_module.Path, "mkdir", forbidden)
    monkeypatch.setattr(evidence_module.Path, "unlink", forbidden)

    assert preflight_external_publication_execution_result_path(target) is None
    assert calls == []
    assert not target.exists()


def test_preflight_existing_regular_target_does_not_read_contents(
    tmp_path: Path,
) -> None:
    target = tmp_path / "result.json"
    target.write_bytes(b"must remain untouched")
    before = target.read_bytes()

    with pytest.raises(ExternalPublicationExecutionEvidencePersistenceError) as error:
        preflight_external_publication_execution_result_path(target)
    _assert_persistence_error(error.value, "target_exists")
    assert target.read_bytes() == before


# ---------------------------------------------------------------------------
# Public surface, order, identities, and source audit
# ---------------------------------------------------------------------------


def test_public_signature_defaults_and_exports_are_exact() -> None:
    function = execute_and_persist_approved_external_publication
    parameters = list(inspect.signature(function).parameters.values())
    assert [parameter.name for parameter in parameters[:8]] == [
        "execution_evidence_path",
        "reconciliation_evidence_path",
        "output_path",
        "ledger_directory",
        "plan",
        "approval",
        "target",
        "transport",
    ]
    assert [parameter.name for parameter in parameters[8:]] == [
        "phase282_preflight_function",
        "phase281_function",
        "phase282_persistence_function",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in parameters
    )
    assert parameters[8].default is preflight_external_publication_execution_result_path
    assert (
        parameters[9].default is execution_module.execute_approved_external_publication
    )
    assert (
        parameters[10].default
        is evidence_module.persist_external_publication_execution_result
    )
    assert issubclass(
        ExternalPublicationExecutionOrchestrationCompatibilityError,
        ExternalPublicationExecutionOrchestrationError,
    )
    assert issubclass(ExternalPublicationExecutionOrchestrationError, ValueError)


def test_orchestration_source_has_no_retry_or_forbidden_lower_boundary_access() -> None:
    source = inspect.getsource(orchestration_module)
    tree = ast.parse(source)
    assert not any(
        isinstance(node, (ast.For, ast.While, ast.AsyncFor)) for node in ast.walk(tree)
    )
    forbidden = (
        "load_external_publication_execution_result",
        "external_publication_execution_result_digest",
        "external_publication_execution_result_canonical_bytes",
        "serialize_external_publication_execution_result_canonical",
        "reconcile_external_publication_execution",
        "persist_external_publication_execution_reconciliation",
        "load_external_publication_execution_reconciliation",
        "external_publication_execution_reconciliation_digest",
        "claim_external_publication_attempt",
        "load_external_publication_attempt_claim",
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
        "ai_office.engine.external_publication",
        "ai_office.engine.external_publication_execution",
        "ai_office.engine.external_publication_execution_evidence",
    }


def test_success_is_exact_preflight_phase281_phase282_order_and_identity(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    result = _result()
    order: list[str] = []
    preflight_paths: list[Path] = []
    phase281_calls: list[dict[str, object]] = []
    persistence_calls: list[tuple[Path, object]] = []

    def preflight(path: Path) -> None:
        order.append("preflight")
        preflight_paths.append(path)

    def phase281(**kwargs: object) -> ExternalPublicationExecutionResult:
        order.append("phase281")
        phase281_calls.append(kwargs)
        return result

    def persistence(path: Path, value: object) -> None:
        order.append("phase282")
        persistence_calls.append((path, value))

    returned = _invoke(
        args,
        preflight=preflight,
        phase281=phase281,
        persistence=persistence,
    )
    assert order == ["preflight", "phase281", "phase282"]
    assert preflight_paths == [args["execution_evidence_path"]]
    assert preflight_paths[0] is args["execution_evidence_path"]
    assert len(phase281_calls) == 1
    assert set(phase281_calls[0]) == {
        "reconciliation_evidence_path",
        "output_path",
        "ledger_directory",
        "plan",
        "approval",
        "target",
        "transport",
    }
    for name in phase281_calls[0]:
        assert phase281_calls[0][name] is args[name]
    assert len(persistence_calls) == 1
    assert persistence_calls[0][0] is args["execution_evidence_path"]
    assert persistence_calls[0][1] is result
    assert returned is result


def test_phase281_and_phase282_defaults_are_public_boundaries() -> None:
    signature = inspect.signature(execute_and_persist_approved_external_publication)
    assert signature.parameters["phase281_function"].default is (
        execution_module.execute_approved_external_publication
    )
    assert signature.parameters["phase282_persistence_function"].default is (
        evidence_module.persist_external_publication_execution_result
    )


# ---------------------------------------------------------------------------
# Same-object lower-boundary errors and zero-retry behavior
# ---------------------------------------------------------------------------


def test_known_preflight_error_propagates_by_identity_and_blocks_all_later_calls(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    sentinel = ExternalPublicationExecutionEvidencePersistenceError("parent")
    calls = {"phase281": 0, "phase282": 0}

    def preflight(_path: Path) -> None:
        raise sentinel

    def phase281(**_kwargs: object) -> ExternalPublicationExecutionResult:
        calls["phase281"] += 1
        return _result()

    def persistence(_path: Path, _result: object) -> None:
        calls["phase282"] += 1

    with pytest.raises(type(sentinel)) as error:
        _invoke(args, preflight=preflight, phase281=phase281, persistence=persistence)
    assert error.value is sentinel
    assert calls == {"phase281": 0, "phase282": 0}


def test_unexpected_preflight_exception_is_fixed_dependency_error(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    calls = {"phase281": 0, "phase282": 0}

    def preflight(_path: Path) -> None:
        raise RuntimeError("path secret and underlying detail")

    def phase281(**_kwargs: object) -> ExternalPublicationExecutionResult:
        calls["phase281"] += 1
        return _result()

    def persistence(_path: Path, _result: object) -> None:
        calls["phase282"] += 1

    with pytest.raises(
        ExternalPublicationExecutionOrchestrationCompatibilityError
    ) as error:
        _invoke(args, preflight=preflight, phase281=phase281, persistence=persistence)
    _assert_orchestration_error(error.value, "dependency_error")
    assert "underlying" not in str(error.value)
    assert calls == {"phase281": 0, "phase282": 0}


@pytest.mark.parametrize(
    "sentinel",
    [
        ExternalPublicationExecutionError("plan"),
        ExternalPublicationExecutionAmbiguousError("transport_execution"),
        ExternalPublicationAttemptAlreadyConsumedError(),
        ExternalPublicationAttemptClaimPersistenceError("ambiguous"),
    ],
)
def test_known_phase281_blocked_ambiguous_and_claim_errors_propagate_by_identity(
    tmp_path: Path,
    sentinel: ExternalPublicationError,
) -> None:
    args = _args(tmp_path)
    calls = 0

    def preflight(_path: Path) -> None:
        pass

    def phase281(**_kwargs: object) -> ExternalPublicationExecutionResult:
        nonlocal calls
        calls += 1
        raise sentinel

    def persistence(_path: Path, _result: object) -> None:
        raise AssertionError("Phase 282 must not run after Phase 281 failure")

    with pytest.raises(type(sentinel)) as error:
        _invoke(args, preflight=preflight, phase281=phase281, persistence=persistence)
    assert error.value is sentinel
    assert calls == 1


def test_unexpected_phase281_exception_is_fixed_and_persistence_is_zero(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    persist_calls = 0

    def phase281(**_kwargs: object) -> ExternalPublicationExecutionResult:
        raise RuntimeError("provider detail must not leak")

    def persistence(_path: Path, _result: object) -> None:
        nonlocal persist_calls
        persist_calls += 1

    with pytest.raises(
        ExternalPublicationExecutionOrchestrationCompatibilityError
    ) as error:
        _invoke(
            args,
            preflight=lambda _path: None,
            phase281=phase281,
            persistence=persistence,
        )
    _assert_orchestration_error(error.value, "dependency_error")
    assert "provider detail" not in str(error.value)
    assert persist_calls == 0


@pytest.mark.parametrize("bad_result", [SimpleNamespace(), {}, {"status": "published"}])
def test_wrong_lookalike_or_mapping_phase281_result_is_rejected_before_persistence(
    tmp_path: Path, bad_result: object
) -> None:
    args = _args(tmp_path)
    calls = 0

    def persistence(_path: Path, _result: object) -> None:
        nonlocal calls
        calls += 1

    def phase281(**_kwargs: object) -> object:
        return bad_result

    with pytest.raises(
        ExternalPublicationExecutionOrchestrationCompatibilityError
    ) as error:
        _invoke(
            args,
            preflight=lambda _path: None,
            phase281=phase281,  # type: ignore[arg-type]
            persistence=persistence,
        )
    _assert_orchestration_error(error.value, "execution_contract")
    assert calls == 0


def test_phase281_result_subclass_is_rejected_before_persistence(
    tmp_path: Path,
) -> None:
    class ResultSubclass(ExternalPublicationExecutionResult):
        pass

    source = _result()
    value = object.__new__(ResultSubclass)
    for field in dataclasses.fields(source):
        object.__setattr__(value, field.name, getattr(source, field.name))
    args = _args(tmp_path)
    calls = 0

    def persistence(_path: Path, _result: object) -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(
        ExternalPublicationExecutionOrchestrationCompatibilityError
    ) as error:
        _invoke(
            args,
            preflight=lambda _path: None,
            phase281=lambda **_kwargs: value,  # type: ignore[arg-type]
            persistence=persistence,
        )
    _assert_orchestration_error(error.value, "execution_contract")
    assert calls == 0


def test_phase282_conflict_is_same_object_and_phase281_is_not_retried(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    result = _result()
    sentinel = ExternalPublicationExecutionEvidenceConflictError()
    counts = {"phase281": 0, "phase282": 0}

    def phase281(**_kwargs: object) -> ExternalPublicationExecutionResult:
        counts["phase281"] += 1
        return result

    def persistence(_path: Path, value: object) -> None:
        counts["phase282"] += 1
        assert value is result
        raise sentinel

    with pytest.raises(type(sentinel)) as error:
        _invoke(
            args,
            preflight=lambda _path: None,
            phase281=phase281,
            persistence=persistence,
        )
    assert error.value is sentinel
    assert counts == {"phase281": 1, "phase282": 1}


def test_phase282_ambiguous_persistence_is_same_object_and_never_retried(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    result = _result()
    sentinel = ExternalPublicationExecutionEvidencePersistenceError("ambiguous")
    counts = {"phase281": 0, "phase282": 0}

    def phase281(**_kwargs: object) -> ExternalPublicationExecutionResult:
        counts["phase281"] += 1
        return result

    def persistence(_path: Path, _value: object) -> None:
        counts["phase282"] += 1
        raise sentinel

    with pytest.raises(type(sentinel)) as error:
        _invoke(
            args,
            preflight=lambda _path: None,
            phase281=phase281,
            persistence=persistence,
        )
    assert error.value is sentinel
    assert counts == {"phase281": 1, "phase282": 1}


def test_unexpected_phase282_exception_is_fixed_and_never_retried(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    result = _result()
    counts = {"phase281": 0, "phase282": 0}

    def phase281(**_kwargs: object) -> ExternalPublicationExecutionResult:
        counts["phase281"] += 1
        return result

    def persistence(_path: Path, _value: object) -> None:
        counts["phase282"] += 1
        raise RuntimeError("durability implementation detail")

    with pytest.raises(
        ExternalPublicationExecutionOrchestrationCompatibilityError
    ) as error:
        _invoke(
            args,
            preflight=lambda _path: None,
            phase281=phase281,
            persistence=persistence,
        )
    _assert_orchestration_error(error.value, "dependency_error")
    assert "durability implementation detail" not in str(error.value)
    assert counts == {"phase281": 1, "phase282": 1}


def test_non_none_phase282_return_is_persistence_contract_and_not_retried(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    result = _result()
    counts = {"phase281": 0, "phase282": 0}

    def phase281(**_kwargs: object) -> ExternalPublicationExecutionResult:
        counts["phase281"] += 1
        return result

    def persistence(_path: Path, _value: object) -> object:
        counts["phase282"] += 1
        return False

    with pytest.raises(
        ExternalPublicationExecutionOrchestrationCompatibilityError
    ) as error:
        _invoke(
            args,
            preflight=lambda _path: None,
            phase281=phase281,
            persistence=persistence,
        )
    _assert_orchestration_error(error.value, "persistence_contract")
    assert counts == {"phase281": 1, "phase282": 1}


def test_result_mutation_during_phase282_persistence_fails_closed_without_compensation(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    result = _result()
    counts = {"phase281": 0, "phase282": 0}

    def phase281(**_kwargs: object) -> ExternalPublicationExecutionResult:
        counts["phase281"] += 1
        return result

    def persistence(_path: Path, value: object) -> None:
        counts["phase282"] += 1
        assert value is result
        object.__setattr__(value, "publication_id", "mutated-after-publication")

    with pytest.raises(
        ExternalPublicationExecutionOrchestrationCompatibilityError
    ) as error:
        _invoke(
            args,
            preflight=lambda _path: None,
            phase281=phase281,
            persistence=persistence,
        )
    _assert_orchestration_error(error.value, "result_mutation")
    assert counts == {"phase281": 1, "phase282": 1}
    assert result.publication_id == "mutated-after-publication"


def test_phase282_persistence_cannot_mutate_result_type_before_return(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    result = _result()

    def persistence(_path: Path, _value: object) -> None:
        # A frozen result must remain the same exact object; value mutation is
        # checked after this single persistence call.
        object.__setattr__(result, "status", "not-published")

    with pytest.raises(
        ExternalPublicationExecutionOrchestrationCompatibilityError
    ) as error:
        _invoke(
            args,
            preflight=lambda _path: None,
            phase281=lambda **_kwargs: result,
            persistence=persistence,
        )
    _assert_orchestration_error(error.value, "result_mutation")


def test_no_phase283_phase284_loader_digest_claim_or_provider_access_from_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _args(tmp_path)
    result = _result()
    calls: list[str] = []

    def forbidden(name: str):
        def fail(*_args: object, **_kwargs: object) -> object:
            calls.append(name)
            raise AssertionError(f"forbidden dependency called: {name}")

        return fail

    for owner, name in (
        (reconciliation_module, "reconcile_external_publication_execution"),
        (
            reconciliation_evidence_module,
            "persist_external_publication_execution_reconciliation",
        ),
        (
            reconciliation_evidence_module,
            "load_external_publication_execution_reconciliation",
        ),
        (
            reconciliation_evidence_module,
            "external_publication_execution_reconciliation_digest",
        ),
        (execution_module, "claim_external_publication_attempt"),
        (execution_module, "load_external_publication_attempt_claim"),
        (evidence_module, "load_external_publication_execution_result"),
        (evidence_module, "external_publication_execution_result_digest"),
    ):
        monkeypatch.setattr(owner, name, forbidden(name), raising=False)

    returned = _invoke(
        args,
        preflight=lambda _path: None,
        phase281=lambda **_kwargs: result,
        persistence=lambda _path, _value: None,
    )
    assert returned is result
    assert calls == []


def test_unknown_noncallable_dependency_is_configuration_and_does_not_start_route(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    with pytest.raises(
        ExternalPublicationExecutionOrchestrationCompatibilityError
    ) as error:
        execute_and_persist_approved_external_publication(
            **args,  # type: ignore[arg-type]
            phase282_preflight_function=None,  # type: ignore[arg-type]
        )
    _assert_orchestration_error(error.value, "configuration")


# Keep this explicit regression close to the orchestration tests: the default
# route uses only the caller's fake transport and never a real provider.
def test_real_default_phase281_and_phase282_boundaries_use_fake_transport_only(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "business-output.bin"
    output_path.write_bytes(_OUTPUT)
    output_digest = hashlib.sha256(_OUTPUT).hexdigest()
    target = _target()
    reconciliation = PublicationRegenerationExportReconciliation(
        schema_version="publication-regeneration-export-reconciliation.v1",
        regeneration_id="regen-285-orchestration",
        receipt_sha256="f" * 64,
        status="matched",
        expected_business_output_sha256=output_digest,
        expected_output_byte_length=len(_OUTPUT),
        observed_business_output_sha256=output_digest,
        observed_output_byte_length=len(_OUTPUT),
    )
    reconciliation_path = tmp_path / "reconciliation-evidence.json"
    persist_publication_regeneration_export_reconciliation(
        reconciliation_path, reconciliation
    )
    plan = build_external_publication_plan(
        reconciliation_evidence_path=reconciliation_path,
        target=target,
    )
    approval = approve_external_publication(
        plan,
        approved_by="human-reviewer-285-default",
        approval_id="approval-285-default",
    )
    ledger_directory = tmp_path / "ledger"
    ledger_directory.mkdir()
    transport_calls: list[tuple[object, bytes]] = []

    def fake_transport(
        target_value: object, payload: bytes
    ) -> ExternalPublicationTransportReceipt:
        transport_calls.append((target_value, payload))
        return ExternalPublicationTransportReceipt("publication-285-default")

    execution_evidence_path = tmp_path / "execution-evidence.json"
    result = execute_and_persist_approved_external_publication(
        execution_evidence_path=execution_evidence_path,
        reconciliation_evidence_path=reconciliation_path,
        output_path=output_path,
        ledger_directory=ledger_directory,
        plan=plan,
        approval=approval,
        target=target,
        transport=fake_transport,
    )
    assert type(result) is ExternalPublicationExecutionResult
    assert execution_evidence_path.is_file()
    assert len(transport_calls) == 1
    assert transport_calls[0][0] is target
    assert transport_calls[0][1] == _OUTPUT
    assert tuple(ledger_directory.iterdir())
