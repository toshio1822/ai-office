"""Focused provider-free tests for the Phase 287 recovery handoff."""

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
import ai_office.engine.external_publication_execution_orchestration as phase285_module
import ai_office.engine.external_publication_execution_reconciliation as phase283_module
import ai_office.engine.external_publication_execution_reconciliation_evidence as phase284_module  # noqa: E501
import ai_office.engine.external_publication_execution_reconciliation_orchestration as phase286_module  # noqa: E501
from ai_office.engine import (
    ExternalPublicationApproval,
    ExternalPublicationAttemptClaimError,
    ExternalPublicationAttemptClaimPersistenceError,
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationError,
    ExternalPublicationExecutionReconciliationEvidenceError,
    ExternalPublicationExecutionReconciliationOrchestrationError,
    ExternalPublicationExecutionResult,
    ExternalPublicationPlan,
    approve_external_publication,
    claim_external_publication_attempt,
    external_publication_attempt_claim_path,
    external_publication_consumption_key,
    load_external_publication_execution_reconciliation,
    persist_external_publication_execution_result,
    resume_external_publication_reconciliation_closure,
)

resume_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_reconciliation_resume"
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
_OUTPUT = b"phase-287-output"
_UNSET = object()


def _approval() -> ExternalPublicationApproval:
    return ExternalPublicationApproval(
        approved=True,
        publication_plan_sha256="c" * 64,
        approved_by="human-reviewer-287",
        approval_id="approval-287",
    )


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


def _assert_resume_error(error: ValueError, classification: str) -> None:
    assert (
        type(error)
        is resume_module.ExternalPublicationExecutionReconciliationResumeCompatibilityError  # noqa: E501
    )
    assert isinstance(
        error, resume_module.ExternalPublicationExecutionReconciliationResumeError
    )
    assert isinstance(error, ValueError)
    assert str(error) == "external publication reconciliation resume is blocked"
    assert error.detail.classification == classification


def _invoke(
    *,
    ledger_directory: object,
    approval: object,
    execution_evidence_path: object,
    reconciliation_evidence_path: object,
    key_function: object = _UNSET,
    claim_path_function: object = _UNSET,
    phase286_function: object = _UNSET,
) -> ExternalPublicationExecutionReconciliation:
    kwargs: dict[str, object] = {
        "ledger_directory": ledger_directory,
        "approval": approval,
        "execution_evidence_path": execution_evidence_path,
        "reconciliation_evidence_path": reconciliation_evidence_path,
    }
    if key_function is not _UNSET:
        kwargs["phase280_consumption_key_function"] = key_function
    if claim_path_function is not _UNSET:
        kwargs["phase280_claim_path_function"] = claim_path_function
    if phase286_function is not _UNSET:
        kwargs["phase286_function"] = phase286_function
    return resume_external_publication_reconciliation_closure(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Public surface and zero-side-effect contract
# ---------------------------------------------------------------------------


def test_public_signature_default_dependencies_and_exports_are_exact() -> None:
    function = resume_external_publication_reconciliation_closure
    parameters = list(inspect.signature(function).parameters.values())
    assert [parameter.name for parameter in parameters[:4]] == [
        "ledger_directory",
        "approval",
        "execution_evidence_path",
        "reconciliation_evidence_path",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in parameters
    )
    assert [parameter.name for parameter in parameters[4:]] == [
        "phase280_consumption_key_function",
        "phase280_claim_path_function",
        "phase286_function",
    ]
    assert parameters[4].default is claim_module.external_publication_consumption_key
    assert parameters[5].default is claim_module.external_publication_attempt_claim_path
    assert (
        parameters[6].default
        is phase286_module.reconcile_and_persist_external_publication_execution
    )
    assert resume_module.resume_external_publication_reconciliation_closure is function
    assert "resume_external_publication_reconciliation_closure" in resume_module.__all__


def test_resume_source_has_no_retry_or_forbidden_boundary_access() -> None:
    source = inspect.getsource(resume_module)
    tree = ast.parse(source)
    assert not any(
        isinstance(node, (ast.For, ast.While, ast.AsyncFor)) for node in ast.walk(tree)
    )
    forbidden = (
        "execute_and_persist_approved_external_publication",
        "execute_approved_external_publication",
        "claim_external_publication_attempt",
        "build_external_publication_attempt_claim",
        "load_external_publication_attempt_claim",
        "persist_external_publication_execution_result",
        "load_external_publication_execution_result",
        "external_publication_execution_result_digest",
        "reconcile_external_publication_execution",
        "persist_external_publication_execution_reconciliation",
        "load_external_publication_execution_reconciliation",
        "external_publication_execution_reconciliation_digest",
        "serialize_external_publication_execution_reconciliation_canonical",
        "external_publication_execution_reconciliation_canonical_bytes",
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
        "time",
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
        "re",
        "ai_office.engine.external_publication",
        "ai_office.engine.external_publication_execution_reconciliation",
        "ai_office.engine.external_publication_execution_reconciliation_evidence",
        "ai_office.engine.external_publication_execution_reconciliation_orchestration",
    }


def test_route_has_no_direct_lower_boundary_or_filesystem_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        (phase283_module, "reconcile_external_publication_execution"),
        (phase284_module, "persist_external_publication_execution_reconciliation"),
        (phase284_module, "load_external_publication_execution_reconciliation"),
        (phase284_module, "external_publication_execution_reconciliation_digest"),
    ):
        monkeypatch.setattr(owner, name, forbidden(name), raising=False)

    for name in ("open", "read_bytes", "write_bytes", "mkdir", "unlink"):
        monkeypatch.setattr(Path, name, forbidden(f"Path.{name}"))

    ledger = tmp_path / "ledger"
    execution_path = tmp_path / "execution.json"
    reconciliation_path = tmp_path / "reconciliation.json"
    claim_path = tmp_path / "derived-claim.json"
    result = _matched()
    key = "d" * 64

    def key_function(value: object) -> str:
        assert value is _approval_value
        return key

    def claim_path_function(directory: object, returned_key: object) -> Path:
        assert directory is ledger
        assert returned_key is key
        return claim_path

    def phase286(**kwargs: object) -> ExternalPublicationExecutionReconciliation:
        assert kwargs == {
            "claim_path": claim_path,
            "execution_evidence_path": execution_path,
            "reconciliation_evidence_path": reconciliation_path,
        }
        return result

    _approval_value = _approval()
    returned = _invoke(
        ledger_directory=ledger,
        approval=_approval_value,
        execution_evidence_path=execution_path,
        reconciliation_evidence_path=reconciliation_path,
        key_function=key_function,
        claim_path_function=claim_path_function,
        phase286_function=phase286,
    )
    assert returned is result
    assert calls == []


# ---------------------------------------------------------------------------
# Exact order, identities, status routes, and contract validation
# ---------------------------------------------------------------------------


def test_success_is_exact_linear_order_and_preserves_all_object_identities() -> None:
    ledger = Path("ledger")
    approval = _approval()
    execution_path = Path("execution-evidence.json")
    reconciliation_path = Path("reconciliation-evidence.json")
    key = "e" * 64
    claim_path = Path("canonical-claim.json")
    result = _matched()
    order: list[str] = []
    calls: list[tuple[object, ...]] = []

    def key_function(value: object) -> str:
        order.append("consumption_key")
        calls.append((value,))
        assert value is approval
        return key

    def claim_path_function(directory: object, returned_key: object) -> Path:
        order.append("claim_path")
        calls.append((directory, returned_key))
        assert directory is ledger
        assert returned_key is key
        return claim_path

    def phase286(**kwargs: object) -> ExternalPublicationExecutionReconciliation:
        order.append("phase286")
        calls.append(
            (
                kwargs["claim_path"],
                kwargs["execution_evidence_path"],
                kwargs["reconciliation_evidence_path"],
            )
        )
        assert kwargs["claim_path"] is claim_path
        assert kwargs["execution_evidence_path"] is execution_path
        assert kwargs["reconciliation_evidence_path"] is reconciliation_path
        return result

    returned = _invoke(
        ledger_directory=ledger,
        approval=approval,
        execution_evidence_path=execution_path,
        reconciliation_evidence_path=reconciliation_path,
        key_function=key_function,
        claim_path_function=claim_path_function,
        phase286_function=phase286,
    )
    assert order == ["consumption_key", "claim_path", "phase286"]
    assert len(calls) == 3
    assert returned is result


@pytest.mark.parametrize("result_factory", [_matched, _lineage_mismatch])
def test_matched_and_lineage_mismatch_are_successful_non_retry_routes(
    result_factory,
) -> None:
    calls = {"key": 0, "path": 0, "phase286": 0}
    approval = _approval()
    ledger = Path("ledger")
    key = "f" * 64
    claim_path = Path("claim.json")
    execution_path = Path("execution.json")
    reconciliation_path = Path("reconciliation.json")
    result = result_factory()

    def key_function(_approval: object) -> str:
        calls["key"] += 1
        return key

    def claim_path_function(_ledger: object, _key: object) -> Path:
        calls["path"] += 1
        return claim_path

    def phase286(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        calls["phase286"] += 1
        return result

    returned = _invoke(
        ledger_directory=ledger,
        approval=approval,
        execution_evidence_path=execution_path,
        reconciliation_evidence_path=reconciliation_path,
        key_function=key_function,
        claim_path_function=claim_path_function,
        phase286_function=phase286,
    )
    assert returned is result
    assert calls == {"key": 1, "path": 1, "phase286": 1}
    assert result.status in {"matched", "lineage_mismatch"}


@pytest.mark.parametrize("bad_approval", [SimpleNamespace(), {}, {"approved": True}])
def test_exact_approval_type_is_required_before_any_lower_dependency(
    bad_approval: object,
) -> None:
    calls = {"key": 0, "path": 0, "phase286": 0}

    def key_function(_approval: object) -> str:
        calls["key"] += 1
        return "a" * 64

    def claim_path_function(_ledger: object, _key: object) -> Path:
        calls["path"] += 1
        return Path("claim.json")

    def phase286(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        calls["phase286"] += 1
        return _matched()

    with pytest.raises(ValueError) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=bad_approval,
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=key_function,
            claim_path_function=claim_path_function,
            phase286_function=phase286,
        )
    _assert_resume_error(error.value, "approval_contract")
    assert calls == {"key": 0, "path": 0, "phase286": 0}


def test_approval_subclass_is_rejected_before_any_lower_dependency() -> None:
    class ApprovalSubclass(ExternalPublicationApproval):
        pass

    source = _approval()
    subclass = _forged_instance(ApprovalSubclass, source)
    with pytest.raises(ValueError) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=subclass,
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=lambda _value: "a" * 64,
            claim_path_function=lambda _directory, _key: Path("claim.json"),
            phase286_function=lambda **_kwargs: _matched(),
        )
    _assert_resume_error(error.value, "approval_contract")


@pytest.mark.parametrize(
    "bad_key",
    [
        1,
        "A" * 64,
        "a" * 63,
        "a" * 65,
        "g" * 64,
        "a" * 63 + "G",
    ],
)
def test_malformed_consumption_key_is_rejected_before_claim_path_and_phase286(
    bad_key: object,
) -> None:
    calls = {"path": 0, "phase286": 0}

    def claim_path_function(_directory: object, _key: object) -> Path:
        calls["path"] += 1
        return Path("claim.json")

    def phase286(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        calls["phase286"] += 1
        return _matched()

    with pytest.raises(ValueError) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=_approval(),
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=lambda _approval: bad_key,
            claim_path_function=claim_path_function,
            phase286_function=phase286,
        )
    _assert_resume_error(error.value, "consumption_key_contract")
    assert calls == {"path": 0, "phase286": 0}


def test_consumption_key_subclass_is_rejected_without_coercion() -> None:
    class KeySubclass(str):
        pass

    bad_key = KeySubclass("a" * 64)
    with pytest.raises(ValueError) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=_approval(),
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=lambda _approval: bad_key,
            claim_path_function=lambda _directory, _key: Path("claim.json"),
            phase286_function=lambda **_kwargs: _matched(),
        )
    _assert_resume_error(error.value, "consumption_key_contract")


@pytest.mark.parametrize("bad_path", ["claim.json", SimpleNamespace(), {}, 7])
def test_malformed_claim_path_is_rejected_before_phase286(bad_path: object) -> None:
    calls = 0

    def phase286(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        nonlocal calls
        calls += 1
        return _matched()

    with pytest.raises(ValueError) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=_approval(),
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=lambda _approval: "b" * 64,
            claim_path_function=lambda _directory, _key: bad_path,
            phase286_function=phase286,
        )
    _assert_resume_error(error.value, "claim_path_contract")
    assert calls == 0


def test_claim_path_subclass_is_rejected_without_coercion() -> None:
    concrete_path_type = type(Path())

    class PathSubclass(concrete_path_type):
        pass

    bad_path = PathSubclass("claim.json")
    with pytest.raises(ValueError) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=_approval(),
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=lambda _approval: "b" * 64,
            claim_path_function=lambda _directory, _key: bad_path,
            phase286_function=lambda **_kwargs: _matched(),
        )
    _assert_resume_error(error.value, "claim_path_contract")


@pytest.mark.parametrize(
    "field_name",
    [
        "schema_version",
        "claim_sha256",
        "execution_evidence_sha256",
        "status",
        "mismatched_fields",
    ],
)
def test_malformed_exact_phase286_result_is_rejected_after_one_call(
    field_name: str,
) -> None:
    result = _matched()
    object.__setattr__(
        result,
        field_name,
        {
            "schema_version": "wrong-schema",
            "claim_sha256": "C" * 64,
            "execution_evidence_sha256": "D" * 64,
            "status": "unknown",
            "mismatched_fields": ("unknown",),
        }[field_name],
    )
    calls = 0

    def phase286(**_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return result

    with pytest.raises(ValueError) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=_approval(),
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=lambda _approval: "c" * 64,
            claim_path_function=lambda _directory, _key: Path("claim.json"),
            phase286_function=phase286,
        )
    _assert_resume_error(error.value, "closure_contract")
    assert calls == 1


@pytest.mark.parametrize(
    "bad_result",
    [SimpleNamespace(), {}, {"status": "matched"}],
)
def test_wrong_lookalike_mapping_phase286_results_are_rejected_without_retry(
    bad_result: object,
) -> None:
    calls = 0

    def phase286(**_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return bad_result

    with pytest.raises(ValueError) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=_approval(),
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=lambda _approval: "d" * 64,
            claim_path_function=lambda _directory, _key: Path("claim.json"),
            phase286_function=phase286,
        )
    _assert_resume_error(error.value, "closure_contract")
    assert calls == 1


def test_phase286_subclass_result_is_rejected_without_retry() -> None:
    class ReconciliationSubclass(ExternalPublicationExecutionReconciliation):
        pass

    result = _forged_instance(ReconciliationSubclass, _matched())
    with pytest.raises(ValueError) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=_approval(),
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=lambda _approval: "e" * 64,
            claim_path_function=lambda _directory, _key: Path("claim.json"),
            phase286_function=lambda **_kwargs: result,
        )
    _assert_resume_error(error.value, "closure_contract")


# ---------------------------------------------------------------------------
# Error identity, downstream zero-calls, and no-retry behavior
# ---------------------------------------------------------------------------


def test_known_consumption_key_error_is_propagated_by_identity() -> None:
    sentinel = ExternalPublicationAttemptClaimError("consumption_key")
    calls = {"path": 0, "phase286": 0}

    def claim_path_function(_directory: object, _key: object) -> Path:
        calls["path"] += 1
        return Path("claim.json")

    def phase286(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        calls["phase286"] += 1
        return _matched()

    with pytest.raises(type(sentinel)) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=_approval(),
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=lambda _approval: (_ for _ in ()).throw(sentinel),
            claim_path_function=claim_path_function,
            phase286_function=phase286,
        )
    assert error.value is sentinel
    assert calls == {"path": 0, "phase286": 0}


def test_known_claim_path_error_is_propagated_by_identity() -> None:
    sentinel = ExternalPublicationAttemptClaimPersistenceError("target")
    calls = 0

    def phase286(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        nonlocal calls
        calls += 1
        return _matched()

    def claim_path_function(_directory: object, _key: object) -> Path:
        raise sentinel

    with pytest.raises(type(sentinel)) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=_approval(),
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=lambda _approval: "f" * 64,
            claim_path_function=claim_path_function,
            phase286_function=phase286,
        )
    assert error.value is sentinel
    assert calls == 0


def test_unexpected_phase280_dependency_exception_is_fixed_and_not_retried() -> None:
    calls = {"key": 0, "path": 0, "phase286": 0}

    def key_function(_approval: object) -> str:
        calls["key"] += 1
        raise RuntimeError("private approval and ledger detail")

    def claim_path_function(_directory: object, _key: object) -> Path:
        calls["path"] += 1
        return Path("claim.json")

    def phase286(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        calls["phase286"] += 1
        return _matched()

    with pytest.raises(ValueError) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=_approval(),
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=key_function,
            claim_path_function=claim_path_function,
            phase286_function=phase286,
        )
    _assert_resume_error(error.value, "dependency_error")
    assert "private" not in str(error.value)
    assert "approval" not in str(error.value)
    assert calls == {"key": 1, "path": 0, "phase286": 0}


def test_unexpected_claim_path_dependency_exception_is_fixed_and_not_retried() -> None:
    calls = {"path": 0, "phase286": 0}

    def claim_path_function(_directory: object, _key: object) -> Path:
        calls["path"] += 1
        raise RuntimeError("private path and ledger detail")

    def phase286(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        calls["phase286"] += 1
        return _matched()

    with pytest.raises(ValueError) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=_approval(),
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=lambda _approval: "a" * 64,
            claim_path_function=claim_path_function,
            phase286_function=phase286,
        )
    _assert_resume_error(error.value, "dependency_error")
    assert "private" not in str(error.value)
    assert "path" not in str(error.value)
    assert calls == {"path": 1, "phase286": 0}


@pytest.mark.parametrize(
    "sentinel",
    [
        ExternalPublicationExecutionReconciliationOrchestrationError(
            "dependency_error"
        ),
        ExternalPublicationExecutionReconciliationError("claim"),
        ExternalPublicationExecutionReconciliationEvidenceError("persistence"),
    ],
)
def test_known_phase286_errors_are_propagated_by_identity_without_retry(
    sentinel: ValueError,
) -> None:
    calls = 0

    def phase286(**_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise sentinel

    with pytest.raises(type(sentinel)) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=_approval(),
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=lambda _approval: "b" * 64,
            claim_path_function=lambda _directory, _key: Path("claim.json"),
            phase286_function=phase286,
        )
    assert error.value is sentinel
    assert calls == 1


def test_unexpected_phase286_exception_is_fixed_and_not_retried() -> None:
    calls = 0

    def phase286(**_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("private reconciliation evidence detail")

    with pytest.raises(ValueError) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=_approval(),
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=lambda _approval: "c" * 64,
            claim_path_function=lambda _directory, _key: Path("claim.json"),
            phase286_function=phase286,
        )
    _assert_resume_error(error.value, "dependency_error")
    assert "private" not in str(error.value)
    assert "evidence" not in str(error.value)
    assert calls == 1


def test_noncallable_dependencies_fail_before_any_lower_boundary() -> None:
    with pytest.raises(ValueError) as error:
        _invoke(
            ledger_directory=Path("ledger"),
            approval=_approval(),
            execution_evidence_path=Path("execution.json"),
            reconciliation_evidence_path=Path("reconciliation.json"),
            key_function=None,
            claim_path_function=lambda _directory, _key: Path("claim.json"),
            phase286_function=lambda **_kwargs: _matched(),
        )
    _assert_resume_error(error.value, "configuration")


# ---------------------------------------------------------------------------
# Real Phase 280 helper + Phase 286 boundary integration
# ---------------------------------------------------------------------------


def _real_sidecars(
    tmp_path: Path,
    *,
    execution_provider: str = "future-provider",
) -> tuple[Path, ExternalPublicationApproval, Path, Path, Path, bytes, bytes]:
    plan = ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id="regen-287-real-boundary",
        reconciliation_evidence_sha256="f" * 64,
        receipt_sha256="1" * 64,
        business_output_sha256=hashlib.sha256(_OUTPUT).hexdigest(),
        output_byte_length=len(_OUTPUT),
        provider="future-provider",
        publication_target_sha256="e" * 64,
    )
    approval = approve_external_publication(
        plan,
        approved_by="human-reviewer-287",
        approval_id="approval-287-real-boundary",
    )
    ledger_directory = tmp_path / "ledger"
    ledger_directory.mkdir()
    claim = claim_external_publication_attempt(ledger_directory, plan, approval)
    expected_claim_path = external_publication_attempt_claim_path(
        ledger_directory, external_publication_consumption_key(approval)
    )
    assert expected_claim_path.name == f"{claim.consumption_key}.json"

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
        publication_id="publication-287-local",
        status="published",
    )
    execution_evidence_path = tmp_path / "execution-evidence.json"
    persist_external_publication_execution_result(
        execution_evidence_path, execution_result
    )
    reconciliation_evidence_path = tmp_path / "reconciliation-evidence.json"
    return (
        ledger_directory,
        approval,
        expected_claim_path,
        execution_evidence_path,
        reconciliation_evidence_path,
        expected_claim_path.read_bytes(),
        execution_evidence_path.read_bytes(),
    )


def test_real_default_phase280_and_phase286_boundaries_match_and_recover_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ledger_directory,
        approval,
        claim_path,
        execution_path,
        reconciliation_path,
        claim_before,
        execution_before,
    ) = _real_sidecars(tmp_path)
    provider_calls = 0

    def forbidden_provider(*_args: object, **_kwargs: object) -> object:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("Phase 281/provider execution must remain unused")

    monkeypatch.setattr(
        execution_module,
        "execute_approved_external_publication",
        forbidden_provider,
    )

    first = resume_external_publication_reconciliation_closure(
        ledger_directory=ledger_directory,
        approval=approval,
        execution_evidence_path=execution_path,
        reconciliation_evidence_path=reconciliation_path,
    )
    reconciliation_after_first = reconciliation_path.read_bytes()
    second = resume_external_publication_reconciliation_closure(
        ledger_directory=ledger_directory,
        approval=approval,
        execution_evidence_path=execution_path,
        reconciliation_evidence_path=reconciliation_path,
    )

    assert type(first) is ExternalPublicationExecutionReconciliation
    assert first.status == "matched"
    assert first.mismatched_fields == ()
    assert type(second) is ExternalPublicationExecutionReconciliation
    assert second.status == "matched"
    assert (
        load_external_publication_execution_reconciliation(reconciliation_path) == first
    )
    assert reconciliation_after_first == reconciliation_path.read_bytes()
    assert claim_before == claim_path.read_bytes()
    assert execution_before == execution_path.read_bytes()
    assert provider_calls == 0


def test_real_default_lineage_mismatch_is_persisted_without_retry(
    tmp_path: Path,
) -> None:
    (
        ledger_directory,
        approval,
        _claim_path,
        execution_path,
        reconciliation_path,
        _claim_before,
        _execution_before,
    ) = _real_sidecars(tmp_path, execution_provider="different-provider")

    result = resume_external_publication_reconciliation_closure(
        ledger_directory=ledger_directory,
        approval=approval,
        execution_evidence_path=execution_path,
        reconciliation_evidence_path=reconciliation_path,
    )

    assert result.status == "lineage_mismatch"
    assert result.mismatched_fields == ("provider",)
    persisted = load_external_publication_execution_reconciliation(reconciliation_path)
    assert persisted == result
    assert persisted.status == "lineage_mismatch"
