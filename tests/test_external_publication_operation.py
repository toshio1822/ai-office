"""Focused provider-free tests for the Phase 288 operation dispatcher."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_office.engine.external_publication as publication_module
import ai_office.engine.external_publication_execution as execution_module
import ai_office.engine.external_publication_execution_orchestration as phase285_module
from ai_office.engine import (
    ExternalPublicationApproval,
    ExternalPublicationAttemptAlreadyConsumedError,
    ExternalPublicationError,
    ExternalPublicationExecutionAmbiguousError,
    ExternalPublicationExecutionError,
    ExternalPublicationExecutionEvidenceError,
    ExternalPublicationExecutionOrchestrationError,
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationError,
    ExternalPublicationExecutionReconciliationEvidenceError,
    ExternalPublicationExecutionReconciliationOrchestrationError,
    ExternalPublicationExecutionResult,
    ExternalPublicationFreshOperationRequest,
    ExternalPublicationOperationCompatibilityError,
    ExternalPublicationOperationError,
    ExternalPublicationOperationFailureDetail,
    ExternalPublicationPlan,
    ExternalPublicationResumeOperationRequest,
    ExternalPublicationTarget,
    ExternalPublicationTransportReceipt,
    PublicationRegenerationExportReconciliation,
    approve_external_publication,
    build_external_publication_plan,
    claim_external_publication_attempt,
    external_publication_attempt_claim_path,
    external_publication_consumption_key,
    load_external_publication_execution_reconciliation,
    persist_external_publication_execution_result,
    persist_publication_regeneration_export_reconciliation,
    run_external_publication_operation,
)

execution_evidence_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_evidence"
)
reconciliation_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_reconciliation"
)
reconciliation_evidence_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_reconciliation_evidence"
)
phase286_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_reconciliation_orchestration"
)
phase287_module = importlib.import_module(
    "ai_office.engine.external_publication_execution_reconciliation_resume"
)
operation_module = importlib.import_module(
    "ai_office.engine.external_publication_operation"
)

_FRESH_SCHEMA = "external-publication-execution-result.v1"
_RECONCILIATION_SCHEMA = "external-publication-execution-reconciliation.v1"
_PLAN_SCHEMA = "external-publication-plan.v1"
_TARGET_SCHEMA = "external-publication-target.v1"
_EXPORT_RECONCILIATION_SCHEMA = "publication-regeneration-export-reconciliation.v1"
_OUTPUT = b"phase-288-output"
_UNSET = object()


def _fresh_result() -> ExternalPublicationExecutionResult:
    return ExternalPublicationExecutionResult(
        schema_version=_FRESH_SCHEMA,
        regeneration_id="regen-288-dispatch",
        publication_attempt_claim_sha256="a" * 64,
        publication_plan_sha256="b" * 64,
        publication_approval_sha256="c" * 64,
        business_output_sha256="d" * 64,
        output_byte_length=len(_OUTPUT),
        provider="future-provider",
        publication_target_sha256="e" * 64,
        publication_id="publication-288-dispatch",
        status="published",
    )


def _resume_result(
    status: str = "matched",
) -> ExternalPublicationExecutionReconciliation:
    return ExternalPublicationExecutionReconciliation(
        schema_version=_RECONCILIATION_SCHEMA,
        claim_sha256="a" * 64,
        execution_evidence_sha256="b" * 64,
        status=status,  # type: ignore[arg-type]
        mismatched_fields=() if status == "matched" else ("provider",),
    )


def _approval() -> ExternalPublicationApproval:
    return ExternalPublicationApproval(
        approved=True,
        publication_plan_sha256="a" * 64,
        approved_by="reviewer-288",
        approval_id="approval-288",
    )


def _plan() -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version=_PLAN_SCHEMA,
        regeneration_id="regen-288-dispatch",
        reconciliation_evidence_sha256="f" * 64,
        receipt_sha256="1" * 64,
        business_output_sha256=hashlib.sha256(_OUTPUT).hexdigest(),
        output_byte_length=len(_OUTPUT),
        provider="future-provider",
        publication_target_sha256="e" * 64,
    )


def _target() -> ExternalPublicationTarget:
    return ExternalPublicationTarget(
        schema_version=_TARGET_SCHEMA,
        provider="future-provider",
        destination_id="destination-288",
    )


def _fresh_request(
    **overrides: object,
) -> ExternalPublicationFreshOperationRequest:
    values: dict[str, object] = {
        "execution_evidence_path": Path("execution-evidence.json"),
        "plan_reconciliation_evidence_path": Path("plan-reconciliation.json"),
        "output_path": Path("business-output.bin"),
        "ledger_directory": Path("ledger"),
        "plan": _plan(),
        "approval": _approval(),
        "target": _target(),
        "transport": object(),
    }
    values.update(overrides)
    return ExternalPublicationFreshOperationRequest(**values)  # type: ignore[arg-type]


def _resume_request(
    **overrides: object,
) -> ExternalPublicationResumeOperationRequest:
    values: dict[str, object] = {
        "ledger_directory": Path("ledger"),
        "approval": _approval(),
        "execution_evidence_path": Path("execution-evidence.json"),
        "execution_reconciliation_evidence_path": Path("execution-reconciliation.json"),
    }
    values.update(overrides)
    return ExternalPublicationResumeOperationRequest(**values)  # type: ignore[arg-type]


def _forged_instance(cls: type[object], source: object) -> object:
    value = object.__new__(cls)
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        object.__setattr__(value, field.name, getattr(source, field.name))
    return value


def _malformed_fresh(field_name: str, value: object) -> object:
    result = _forged_instance(ExternalPublicationExecutionResult, _fresh_result())
    object.__setattr__(result, field_name, value)
    return result


def _malformed_resume(field_name: str, value: object) -> object:
    result = _forged_instance(
        ExternalPublicationExecutionReconciliation, _resume_result()
    )
    object.__setattr__(result, field_name, value)
    return result


def _assert_operation_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationOperationCompatibilityError
    assert isinstance(error, ExternalPublicationOperationError)
    assert isinstance(error, ValueError)
    assert isinstance(error.detail, ExternalPublicationOperationFailureDetail)
    assert str(error) == "external publication operation is blocked"
    assert error.detail.classification == classification


# ---------------------------------------------------------------------------
# Public surface, request envelopes, and source audit
# ---------------------------------------------------------------------------


def test_public_models_are_frozen_exact_dataclasses_and_are_exported() -> None:
    assert dataclasses.is_dataclass(ExternalPublicationFreshOperationRequest)
    assert dataclasses.is_dataclass(ExternalPublicationResumeOperationRequest)
    assert dataclasses.is_dataclass(ExternalPublicationOperationFailureDetail)
    assert ExternalPublicationFreshOperationRequest.__dataclass_params__.frozen
    assert ExternalPublicationResumeOperationRequest.__dataclass_params__.frozen
    assert ExternalPublicationOperationFailureDetail.__dataclass_params__.frozen
    assert [
        field.name
        for field in dataclasses.fields(ExternalPublicationFreshOperationRequest)
    ] == [
        "execution_evidence_path",
        "plan_reconciliation_evidence_path",
        "output_path",
        "ledger_directory",
        "plan",
        "approval",
        "target",
        "transport",
    ]
    assert [
        field.name
        for field in dataclasses.fields(ExternalPublicationResumeOperationRequest)
    ] == [
        "ledger_directory",
        "approval",
        "execution_evidence_path",
        "execution_reconciliation_evidence_path",
    ]
    assert (
        operation_module.ExternalPublicationFreshOperationRequest
        is ExternalPublicationFreshOperationRequest
    )
    assert (
        operation_module.ExternalPublicationResumeOperationRequest
        is ExternalPublicationResumeOperationRequest
    )
    assert "ExternalPublicationFreshOperationRequest" in operation_module.__all__
    assert "ExternalPublicationResumeOperationRequest" in operation_module.__all__
    assert "run_external_publication_operation" in operation_module.__all__


def test_request_construction_preserves_every_supplied_object_identity() -> None:
    values = {
        "execution_evidence_path": object(),
        "plan_reconciliation_evidence_path": object(),
        "output_path": object(),
        "ledger_directory": object(),
        "plan": object(),
        "approval": object(),
        "target": object(),
        "transport": object(),
    }
    request = ExternalPublicationFreshOperationRequest(**values)  # type: ignore[arg-type]
    for field in dataclasses.fields(request):
        assert getattr(request, field.name) is values[field.name]

    resume_values = {
        "ledger_directory": object(),
        "approval": object(),
        "execution_evidence_path": object(),
        "execution_reconciliation_evidence_path": object(),
    }
    resume = ExternalPublicationResumeOperationRequest(**resume_values)  # type: ignore[arg-type]
    for field in dataclasses.fields(resume):
        assert getattr(resume, field.name) is resume_values[field.name]


def test_request_models_reject_mutation_after_construction() -> None:
    fresh = _fresh_request()
    resume = _resume_request()
    with pytest.raises(dataclasses.FrozenInstanceError):
        fresh.output_path = Path("other")  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        resume.approval = _approval()  # type: ignore[misc]


def test_dispatcher_signature_and_default_dependencies_are_exact() -> None:
    parameters = list(
        inspect.signature(run_external_publication_operation).parameters.values()
    )
    assert [parameter.name for parameter in parameters] == [
        "request",
        "phase285_function",
        "phase287_function",
    ]
    assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters[1].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[2].kind is inspect.Parameter.KEYWORD_ONLY
    assert (
        parameters[1].default
        is phase285_module.execute_and_persist_approved_external_publication
    )
    assert (
        parameters[2].default
        is phase287_module.resume_external_publication_reconciliation_closure
    )
    assert (
        operation_module.run_external_publication_operation
        is run_external_publication_operation
    )


def test_operation_source_has_no_direct_lower_boundary_or_filesystem_route_logic() -> (
    None
):
    source = inspect.getsource(operation_module)
    tree = ast.parse(source)
    assert not any(
        isinstance(node, (ast.For, ast.While, ast.AsyncFor)) for node in ast.walk(tree)
    )
    forbidden = (
        "execute_approved_external_publication",
        "claim_external_publication_attempt",
        "external_publication_consumption_key",
        "external_publication_attempt_claim_path",
        "reconcile_external_publication_execution",
        "persist_external_publication_execution_reconciliation",
        "load_external_publication_attempt_claim",
        "load_external_publication_execution_result",
        "persist_external_publication_execution_result",
        "load_external_publication_execution_reconciliation",
        "external_publication_execution_result_digest",
        "external_publication_execution_reconciliation_digest",
        "serialize_external_publication_execution_result_canonical",
        "serialize_external_publication_execution_reconciliation_canonical",
        "read_bytes",
        "write_bytes",
        "mkdir",
        "unlink",
        "socket",
        "subprocess",
        "environ",
        "random",
        "uuid",
        "datetime",
        "monotonic",
    )
    assert all(name not in source for name in forbidden)


# ---------------------------------------------------------------------------
# Exact dispatch, order, identities, and route isolation
# ---------------------------------------------------------------------------


def test_fresh_route_calls_only_phase285_once_with_exact_keyword_identities() -> None:
    request = ExternalPublicationFreshOperationRequest(
        execution_evidence_path=Path("fresh-execution.json"),
        plan_reconciliation_evidence_path=Path("plan-evidence.json"),
        output_path=Path("output.bin"),
        ledger_directory=Path("ledger"),
        plan=plan,
        approval=approval,
        target=target,
        transport=transport,
    )
    calls: list[dict[str, object]] = []
    result = _fresh_result()

    def phase285(**kwargs: object) -> ExternalPublicationExecutionResult:
        calls.append(kwargs)
        return result

    returned = run_external_publication_operation(
        request,
        phase285_function=phase285,
        phase287_function=object(),
    )
    assert returned is result
    assert len(calls) == 1
    assert list(calls[0]) == [
        "execution_evidence_path",
        "reconciliation_evidence_path",
        "output_path",
        "ledger_directory",
        "plan",
        "approval",
        "target",
        "transport",
    ]
    assert calls[0]["execution_evidence_path"] is request.execution_evidence_path
    assert (
        calls[0]["reconciliation_evidence_path"]
        is request.plan_reconciliation_evidence_path
    )
    assert calls[0]["output_path"] is request.output_path
    assert calls[0]["ledger_directory"] is request.ledger_directory
    assert calls[0]["plan"] is request.plan
    assert calls[0]["approval"] is request.approval
    assert calls[0]["target"] is request.target
    assert calls[0]["transport"] is request.transport


def test_resume_route_calls_only_phase287_once_with_exact_keyword_identities() -> None:
    request = ExternalPublicationResumeOperationRequest(
        ledger_directory=ledger_directory,
        approval=approval,
        execution_evidence_path=fresh_execution_path,
        execution_reconciliation_evidence_path=plan_evidence_path,
    )
    calls: list[dict[str, object]] = []
    result = _resume_result()

    def phase287(**kwargs: object) -> ExternalPublicationExecutionReconciliation:
        calls.append(kwargs)
        return result

    returned = run_external_publication_operation(
        request,
        phase285_function=object(),
        phase287_function=phase287,
    )
    assert returned is result
    assert len(calls) == 1
    assert list(calls[0]) == [
        "ledger_directory",
        "approval",
        "execution_evidence_path",
        "reconciliation_evidence_path",
    ]
    assert calls[0]["ledger_directory"] is request.ledger_directory
    assert calls[0]["approval"] is request.approval
    assert calls[0]["execution_evidence_path"] is request.execution_evidence_path
    assert (
        calls[0]["reconciliation_evidence_path"]
        is request.execution_reconciliation_evidence_path
    )


class _FreshRequestSubclass(ExternalPublicationFreshOperationRequest):
    pass


class _ResumeRequestSubclass(ExternalPublicationResumeOperationRequest):
    pass


# These are deliberately supplied as runtime envelopes only; their field
# values are not validated by the dispatcher before exact-type routing.
plan = _plan()
approval = _approval()
target = _target()
ledger_directory = Path("ledger")
fresh_execution_path = Path("fresh-execution-evidence.json")
plan_evidence_path = Path("plan-reconciliation-evidence.json")
output_path = Path("business-output.bin")
transport = object()


@pytest.mark.parametrize(
    "request_value",
    [
        None,
        SimpleNamespace(),
        {},
        {"mode": "fresh"},
        _FreshRequestSubclass(
            Path("execution.json"),
            Path("plan.json"),
            Path("output.bin"),
            Path("ledger"),
            _plan(),
            _approval(),
            _target(),
            object(),
        ),
        _ResumeRequestSubclass(
            Path("ledger"),
            _approval(),
            Path("execution.json"),
            Path("reconciliation.json"),
        ),
    ],
)
def test_non_exact_requests_are_rejected_before_both_dependencies(
    request_value: object,
) -> None:
    calls = {"phase285": 0, "phase287": 0}

    def phase285(**_kwargs: object) -> object:
        calls["phase285"] += 1
        return _fresh_result()

    def phase287(**_kwargs: object) -> object:
        calls["phase287"] += 1
        return _resume_result()

    with pytest.raises(ValueError) as error:
        run_external_publication_operation(
            request_value,  # type: ignore[arg-type]
            phase285_function=phase285,
            phase287_function=phase287,
        )
    _assert_operation_error(error.value, "request_contract")
    assert calls == {"phase285": 0, "phase287": 0}


def test_unsupported_request_rejects_even_with_malformed_dependencies() -> None:
    with pytest.raises(ValueError) as error:
        run_external_publication_operation(
            object(),
            phase285_function=object(),  # type: ignore[arg-type]
            phase287_function=object(),  # type: ignore[arg-type]
        )
    _assert_operation_error(error.value, "request_contract")


def test_fresh_route_ignores_unused_noncallable_phase287_dependency() -> None:
    calls = 0

    def phase285(**_kwargs: object) -> ExternalPublicationExecutionResult:
        nonlocal calls
        calls += 1
        return _fresh_result()

    result = run_external_publication_operation(
        _fresh_request(),
        phase285_function=phase285,
        phase287_function=None,  # type: ignore[arg-type]
    )
    assert type(result) is ExternalPublicationExecutionResult
    assert calls == 1


def test_resume_route_ignores_unused_noncallable_phase285_dependency() -> None:
    calls = 0

    def phase287(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        nonlocal calls
        calls += 1
        return _resume_result()

    result = run_external_publication_operation(
        _resume_request(),
        phase285_function=None,  # type: ignore[arg-type]
        phase287_function=phase287,
    )
    assert type(result) is ExternalPublicationExecutionReconciliation
    assert calls == 1


def test_noncallable_selected_dependency_fails_before_lower_route() -> None:
    fresh_calls = 0
    resume_calls = 0

    def phase287(**_kwargs: object) -> ExternalPublicationExecutionReconciliation:
        nonlocal resume_calls
        resume_calls += 1
        return _resume_result()

    with pytest.raises(ValueError) as fresh_error:
        run_external_publication_operation(
            _fresh_request(),
            phase285_function=None,  # type: ignore[arg-type]
            phase287_function=phase287,
        )
    _assert_operation_error(fresh_error.value, "configuration")

    def phase285(**_kwargs: object) -> ExternalPublicationExecutionResult:
        nonlocal fresh_calls
        fresh_calls += 1
        return _fresh_result()

    with pytest.raises(ValueError) as resume_error:
        run_external_publication_operation(
            _resume_request(),
            phase285_function=phase285,
            phase287_function=None,  # type: ignore[arg-type]
        )
    _assert_operation_error(resume_error.value, "configuration")
    assert fresh_calls == 0
    assert resume_calls == 0


# ---------------------------------------------------------------------------
# Exact result contracts and status-neutral success
# ---------------------------------------------------------------------------


def test_fresh_result_is_exact_and_returned_by_identity() -> None:
    result = _fresh_result()
    returned = run_external_publication_operation(
        _fresh_request(),
        phase285_function=lambda **_kwargs: result,
        phase287_function=object(),
    )
    assert type(returned) is ExternalPublicationExecutionResult
    assert returned is result


@pytest.mark.parametrize("status", ["matched", "lineage_mismatch"])
def test_resume_matched_and_lineage_mismatch_are_exact_identity_successes(
    status: str,
) -> None:
    result = _resume_result(status)
    fresh_calls = 0

    def forbidden_fresh(**_kwargs: object) -> object:
        nonlocal fresh_calls
        fresh_calls += 1
        raise AssertionError("fresh route must not be called")

    returned = run_external_publication_operation(
        _resume_request(),
        phase285_function=forbidden_fresh,
        phase287_function=lambda **_kwargs: result,
    )
    assert type(returned) is ExternalPublicationExecutionReconciliation
    assert returned is result
    assert returned.status == status
    assert fresh_calls == 0


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("schema_version", "wrong-schema"),
        ("regeneration_id", ""),
        ("publication_attempt_claim_sha256", "A" * 64),
        ("publication_plan_sha256", "B" * 64),
        ("publication_approval_sha256", "C" * 64),
        ("business_output_sha256", "D" * 63),
        ("output_byte_length", True),
        ("provider", "not a slug"),
        ("publication_target_sha256", "E" * 64),
        ("publication_id", ""),
        ("status", "failed"),
    ],
)
def test_malformed_exact_fresh_result_is_rejected_without_fallback(
    field_name: str,
    bad_value: object,
) -> None:
    calls = {"phase285": 0, "phase287": 0}
    result = _malformed_fresh(field_name, bad_value)

    def phase285(**_kwargs: object) -> object:
        calls["phase285"] += 1
        return result

    def phase287(**_kwargs: object) -> object:
        calls["phase287"] += 1
        return _resume_result()

    with pytest.raises(ValueError) as error:
        run_external_publication_operation(
            _fresh_request(), phase285_function=phase285, phase287_function=phase287
        )
    _assert_operation_error(error.value, "fresh_result_contract")
    assert calls == {"phase285": 1, "phase287": 0}


def test_wrong_subclass_lookalike_and_mapping_fresh_results_are_rejected() -> None:
    class ResultSubclass(ExternalPublicationExecutionResult):
        pass

    source = _fresh_result()
    values = [
        _forged_instance(ResultSubclass, source),
        SimpleNamespace(),
        {},
        {"status": "published"},
    ]
    for value in values:
        calls = {"phase285": 0, "phase287": 0}

        def phase285(**_kwargs: object) -> object:
            calls["phase285"] += 1
            return value

        def phase287(**_kwargs: object) -> object:
            calls["phase287"] += 1
            return _resume_result()

        with pytest.raises(ValueError) as error:
            run_external_publication_operation(
                _fresh_request(),
                phase285_function=phase285,
                phase287_function=phase287,
            )
        _assert_operation_error(error.value, "fresh_result_contract")
        assert calls == {"phase285": 1, "phase287": 0}


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("schema_version", "wrong-schema"),
        ("claim_sha256", "A" * 64),
        ("execution_evidence_sha256", "B" * 63),
        ("status", "published"),
        ("mismatched_fields", ("provider",)),
    ],
)
def test_malformed_exact_resume_result_is_rejected_without_fallback(
    field_name: str,
    bad_value: object,
) -> None:
    calls = {"phase285": 0, "phase287": 0}
    result = _malformed_resume(field_name, bad_value)

    def phase285(**_kwargs: object) -> object:
        calls["phase285"] += 1
        return _fresh_result()

    def phase287(**_kwargs: object) -> object:
        calls["phase287"] += 1
        return result

    with pytest.raises(ValueError) as error:
        run_external_publication_operation(
            _resume_request(), phase285_function=phase285, phase287_function=phase287
        )
    _assert_operation_error(error.value, "resume_result_contract")
    assert calls == {"phase285": 0, "phase287": 1}


def test_wrong_subclass_lookalike_and_mapping_resume_results_are_rejected() -> None:
    class ResultSubclass(ExternalPublicationExecutionReconciliation):
        pass

    source = _resume_result()
    values = [
        _forged_instance(ResultSubclass, source),
        SimpleNamespace(),
        {},
        {"status": "matched"},
    ]
    for value in values:
        calls = {"phase285": 0, "phase287": 0}

        def phase285(**_kwargs: object) -> object:
            calls["phase285"] += 1
            return _fresh_result()

        def phase287(**_kwargs: object) -> object:
            calls["phase287"] += 1
            return value

        with pytest.raises(ValueError) as error:
            run_external_publication_operation(
                _resume_request(),
                phase285_function=phase285,
                phase287_function=phase287,
            )
        _assert_operation_error(error.value, "resume_result_contract")
        assert calls == {"phase285": 0, "phase287": 1}


# ---------------------------------------------------------------------------
# Known error identity, unexpected error sanitization, and no retry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentinel",
    [
        ExternalPublicationError("claim"),
        ExternalPublicationExecutionError("plan"),
        ExternalPublicationExecutionAmbiguousError("transport_execution"),
        ExternalPublicationExecutionEvidenceError("persistence"),
        ExternalPublicationExecutionOrchestrationError("dependency_error"),
    ],
)
def test_known_fresh_lower_errors_propagate_by_identity_and_never_resume(
    sentinel: ValueError,
) -> None:
    calls = {"phase285": 0, "phase287": 0}

    def phase285(**_kwargs: object) -> object:
        calls["phase285"] += 1
        raise sentinel

    def phase287(**_kwargs: object) -> object:
        calls["phase287"] += 1
        return _resume_result()

    with pytest.raises(type(sentinel)) as error:
        run_external_publication_operation(
            _fresh_request(), phase285_function=phase285, phase287_function=phase287
        )
    assert error.value is sentinel
    assert calls == {"phase285": 1, "phase287": 0}


@pytest.mark.parametrize(
    "sentinel",
    [
        phase287_module.ExternalPublicationExecutionReconciliationResumeError(
            "dependency_error"
        ),
        ExternalPublicationError("claim"),
        ExternalPublicationExecutionReconciliationOrchestrationError(
            "dependency_error"
        ),
        ExternalPublicationExecutionReconciliationError("claim"),
        ExternalPublicationExecutionReconciliationEvidenceError("persistence"),
    ],
)
def test_known_resume_lower_errors_propagate_by_identity_and_never_fresh(
    sentinel: ValueError,
) -> None:
    calls = {"phase285": 0, "phase287": 0}

    def phase285(**_kwargs: object) -> object:
        calls["phase285"] += 1
        return _fresh_result()

    def phase287(**_kwargs: object) -> object:
        calls["phase287"] += 1
        raise sentinel

    with pytest.raises(type(sentinel)) as error:
        run_external_publication_operation(
            _resume_request(), phase285_function=phase285, phase287_function=phase287
        )
    assert error.value is sentinel
    assert calls == {"phase285": 0, "phase287": 1}


def test_unexpected_fresh_dependency_exception_is_fixed_and_not_retried() -> None:
    calls = {"phase285": 0, "phase287": 0}

    def phase285(**_kwargs: object) -> object:
        calls["phase285"] += 1
        raise RuntimeError("private path/provider/credential detail")

    def phase287(**_kwargs: object) -> object:
        calls["phase287"] += 1
        return _resume_result()

    with pytest.raises(ValueError) as error:
        run_external_publication_operation(
            _fresh_request(), phase285_function=phase285, phase287_function=phase287
        )
    _assert_operation_error(error.value, "dependency_error")
    assert "private" not in str(error.value)
    assert "provider" not in str(error.value)
    assert "credential" not in str(error.value)
    assert calls == {"phase285": 1, "phase287": 0}


def test_unexpected_resume_dependency_exception_is_fixed_and_not_retried() -> None:
    calls = {"phase285": 0, "phase287": 0}

    def phase285(**_kwargs: object) -> object:
        calls["phase285"] += 1
        return _fresh_result()

    def phase287(**_kwargs: object) -> object:
        calls["phase287"] += 1
        raise RuntimeError("private reconciliation/path detail")

    with pytest.raises(ValueError) as error:
        run_external_publication_operation(
            _resume_request(), phase285_function=phase285, phase287_function=phase287
        )
    _assert_operation_error(error.value, "dependency_error")
    assert "private" not in str(error.value)
    assert "reconciliation" not in str(error.value)
    assert "path" not in str(error.value)
    assert calls == {"phase285": 0, "phase287": 1}


def test_dispatcher_does_not_touch_filesystem_or_call_unselected_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def forbidden(name: str):
        def fail(*_args: object, **_kwargs: object) -> object:
            calls.append(name)
            raise AssertionError(f"forbidden call: {name}")

        return fail

    for owner, name in (
        (phase285_module, "execute_and_persist_approved_external_publication"),
        (phase287_module, "resume_external_publication_reconciliation_closure"),
        (publication_module, "claim_external_publication_attempt"),
        (publication_module, "external_publication_attempt_claim_path"),
        (publication_module, "external_publication_consumption_key"),
        (phase286_module, "reconcile_and_persist_external_publication_execution"),
        (reconciliation_module, "reconcile_external_publication_execution"),
        (
            reconciliation_evidence_module,
            "persist_external_publication_execution_reconciliation",
        ),
        (execution_evidence_module, "persist_external_publication_execution_result"),
        (execution_module, "execute_approved_external_publication"),
    ):
        monkeypatch.setattr(owner, name, forbidden(name), raising=False)

    for name in ("open", "read_bytes", "write_bytes", "mkdir", "unlink"):
        monkeypatch.setattr(Path, name, forbidden(f"Path.{name}"))

    result = run_external_publication_operation(
        _fresh_request(),
        phase285_function=lambda **_kwargs: _fresh_result(),
        phase287_function=object(),
    )
    assert result.status == "published"
    assert calls == []


# ---------------------------------------------------------------------------
# Real Phase 285 -> Phase 287 lifecycle with local durable sidecars
# ---------------------------------------------------------------------------


def _real_fixture(
    tmp_path: Path,
) -> tuple[
    ExternalPublicationFreshOperationRequest,
    ExternalPublicationResumeOperationRequest,
    Path,
    Path,
    Path,
    list[tuple[object, bytes]],
]:
    output = tmp_path / "business-output.bin"
    output.write_bytes(_OUTPUT)
    plan_reconciliation_path = tmp_path / "plan-reconciliation-evidence.json"
    output_digest = hashlib.sha256(_OUTPUT).hexdigest()
    plan_reconciliation = PublicationRegenerationExportReconciliation(
        schema_version=_EXPORT_RECONCILIATION_SCHEMA,
        regeneration_id="regen-288-real",
        receipt_sha256="f" * 64,
        status="matched",
        expected_business_output_sha256=output_digest,
        expected_output_byte_length=len(_OUTPUT),
        observed_business_output_sha256=output_digest,
        observed_output_byte_length=len(_OUTPUT),
    )
    persist_publication_regeneration_export_reconciliation(
        plan_reconciliation_path, plan_reconciliation
    )
    target_value = ExternalPublicationTarget(
        schema_version=_TARGET_SCHEMA,
        provider="future-provider",
        destination_id="destination-288-real",
    )
    plan_value = build_external_publication_plan(
        reconciliation_evidence_path=plan_reconciliation_path,
        target=target_value,
    )
    approval_value = approve_external_publication(
        plan_value,
        approved_by="reviewer-288-real",
        approval_id="approval-288-real",
    )
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    execution_path = tmp_path / "execution-evidence.json"
    reconciliation_path = tmp_path / "execution-reconciliation-evidence.json"
    transport_calls: list[tuple[object, bytes]] = []

    def fake_transport(
        target_object: object, payload: bytes
    ) -> ExternalPublicationTransportReceipt:
        transport_calls.append((target_object, payload))
        return ExternalPublicationTransportReceipt("publication-288-real")

    fresh = ExternalPublicationFreshOperationRequest(
        execution_evidence_path=execution_path,
        plan_reconciliation_evidence_path=plan_reconciliation_path,
        output_path=output,
        ledger_directory=ledger,
        plan=plan_value,
        approval=approval_value,
        target=target_value,
        transport=fake_transport,
    )
    resume = ExternalPublicationResumeOperationRequest(
        ledger_directory=ledger,
        approval=approval_value,
        execution_evidence_path=execution_path,
        execution_reconciliation_evidence_path=reconciliation_path,
    )
    return fresh, resume, ledger, execution_path, reconciliation_path, transport_calls


def test_real_fresh_then_resume_twice_is_explicit_and_provider_once(
    tmp_path: Path,
) -> None:
    fresh, resume, ledger, execution_path, reconciliation_path, transport_calls = (
        _real_fixture(tmp_path)
    )

    first_fresh = run_external_publication_operation(
        fresh,
        phase287_function=object(),
    )
    assert type(first_fresh) is ExternalPublicationExecutionResult
    assert first_fresh.status == "published"
    assert len(transport_calls) == 1
    assert transport_calls[0][0] is fresh.target
    assert transport_calls[0][1] == _OUTPUT

    claim_path = external_publication_attempt_claim_path(
        ledger, external_publication_consumption_key(resume.approval)
    )
    claim_before = claim_path.read_bytes()
    execution_before = execution_path.read_bytes()

    first_resume = run_external_publication_operation(
        resume,
        phase285_function=object(),
    )
    assert type(first_resume) is ExternalPublicationExecutionReconciliation
    assert first_resume.status == "matched"
    assert len(transport_calls) == 1
    reconciliation_before_second = reconciliation_path.read_bytes()

    second_resume = run_external_publication_operation(
        resume,
        phase285_function=object(),
    )
    assert type(second_resume) is ExternalPublicationExecutionReconciliation
    assert second_resume.status == "matched"
    assert len(transport_calls) == 1
    assert reconciliation_before_second == reconciliation_path.read_bytes()
    assert claim_before == claim_path.read_bytes()
    assert execution_before == execution_path.read_bytes()
    assert (
        load_external_publication_execution_reconciliation(reconciliation_path)
        == second_resume
    )

    second_execution_path = tmp_path / "second-execution-evidence.json"
    second_fresh = dataclasses.replace(
        fresh,
        execution_evidence_path=second_execution_path,
    )
    fallback_calls = 0

    def forbidden_resume(**_kwargs: object) -> object:
        nonlocal fallback_calls
        fallback_calls += 1
        raise AssertionError("blocked fresh operation must not fall back to resume")

    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError) as error:
        run_external_publication_operation(
            second_fresh,
            phase287_function=forbidden_resume,
        )
    assert error.value.detail.classification == "already_consumed"
    assert fallback_calls == 0
    assert len(transport_calls) == 1


def test_real_lineage_mismatch_resume_is_returned_without_fresh_or_provider(
    tmp_path: Path,
) -> None:
    output = tmp_path / "business-output.bin"
    output.write_bytes(_OUTPUT)
    plan_reconciliation_path = tmp_path / "plan-reconciliation-evidence.json"
    output_digest = hashlib.sha256(_OUTPUT).hexdigest()
    persist_publication_regeneration_export_reconciliation(
        plan_reconciliation_path,
        PublicationRegenerationExportReconciliation(
            schema_version=_EXPORT_RECONCILIATION_SCHEMA,
            regeneration_id="regen-288-mismatch",
            receipt_sha256="f" * 64,
            status="matched",
            expected_business_output_sha256=output_digest,
            expected_output_byte_length=len(_OUTPUT),
            observed_business_output_sha256=output_digest,
            observed_output_byte_length=len(_OUTPUT),
        ),
    )
    target_value = _target()
    plan_value = build_external_publication_plan(
        reconciliation_evidence_path=plan_reconciliation_path,
        target=target_value,
    )
    approval_value = approve_external_publication(
        plan_value,
        approved_by="reviewer-288-mismatch",
        approval_id="approval-288-mismatch",
    )
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    claim = claim_external_publication_attempt(ledger, plan_value, approval_value)
    claim_path = external_publication_attempt_claim_path(
        ledger, external_publication_consumption_key(approval_value)
    )
    assert claim_path.is_file()
    execution_path = tmp_path / "execution-evidence.json"
    persist_external_publication_execution_result(
        execution_path,
        ExternalPublicationExecutionResult(
            schema_version=_FRESH_SCHEMA,
            regeneration_id=claim.regeneration_id,
            publication_attempt_claim_sha256=claim.digest,
            publication_plan_sha256=claim.publication_plan_sha256,
            publication_approval_sha256=claim.publication_approval_sha256,
            business_output_sha256=claim.business_output_sha256,
            output_byte_length=claim.output_byte_length,
            provider="different-provider",
            publication_target_sha256=claim.publication_target_sha256,
            publication_id="publication-288-mismatch",
            status="published",
        ),
    )
    reconciliation_path = tmp_path / "reconciliation-evidence.json"
    request = ExternalPublicationResumeOperationRequest(
        ledger_directory=ledger,
        approval=approval_value,
        execution_evidence_path=execution_path,
        execution_reconciliation_evidence_path=reconciliation_path,
    )
    fresh_calls = 0

    def forbidden_fresh(**_kwargs: object) -> object:
        nonlocal fresh_calls
        fresh_calls += 1
        raise AssertionError("lineage mismatch must not invoke fresh publication")

    result = run_external_publication_operation(
        request,
        phase285_function=forbidden_fresh,
    )
    assert type(result) is ExternalPublicationExecutionReconciliation
    assert result.status == "lineage_mismatch"
    assert result.mismatched_fields == ("provider",)
    assert (
        load_external_publication_execution_reconciliation(reconciliation_path)
        == result
    )
    assert fresh_calls == 0
