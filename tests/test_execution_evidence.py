"""Behavioral coverage for Milestone 3 immutable execution evidence."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

import ai_office.providers.openai.responses_execution as responses_execution_module
from ai_office.cli import app
from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.workflow_approval_evidence import (
    build_execution_approval_evidence_for_tools,
    persist_execution_approval_evidence,
)
from ai_office.execution_evidence import (
    ExecutionAttemptAlreadyClaimedError,
    ExecutionEvidenceConflictError,
    ExecutionEvidenceError,
    ExecutionEvidenceLoadError,
    ExecutionEvidencePersistenceError,
    build_execution_evidence_context,
    execution_attempt_evidence_path,
    execution_evidence_of_result,
    execution_normalized_result_evidence_path,
    execution_raw_response_evidence_path,
    inspect_run_execution_evidence,
    list_run_execution_evidence,
    load_normalized_result_evidence,
    load_raw_response_evidence,
    persist_raw_response_evidence,
)
from ai_office.execution_target import DIRECT_OPENAI_EXECUTION_TARGET
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationRequest,
    ModelInvocationSuccess,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesRawHttpResponse,
    OpenAIResponsesTransportError,
    execute_openai_model_invocation,
)
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    transition_workflow_execution_from_step_result,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceInputError,
    WorkflowExecutionPersistenceTargets,
    parse_runtime_step_event,
    persist_workflow_execution_transition,
    serialize_runtime_step_event_jsonl,
)
from tests._run_test_support import TestRun as RunFixture
from tests._run_test_support import create_test_run

runner = CliRunner()


@dataclass(frozen=True)
class EvidenceFixture:
    run: RunFixture
    request: ModelInvocationRequest
    approval: ModelInvocationExecutionApproval
    context: object


def _workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "evidence-workflow",
            "name": "Evidence Workflow",
            "description": "A deterministic execution-evidence fixture.",
            "steps": [
                {
                    "id": "step-1",
                    "name": "Evidence Step",
                    "employee": "employee-1",
                    "instructions": "Return the safe result.",
                    "business_approval_required": False,
                },
                {
                    "id": "step-2",
                    "name": "Evidence Step Two",
                    "employee": "employee-2",
                    "instructions": "Return the second safe result.",
                    "business_approval_required": False,
                },
            ],
        }
    )


def _employee() -> EmployeeDefinition:
    return EmployeeDefinition(
        id="employee-1",
        name="Evidence Employee",
        role="Evidence test employee",
        instructions="Use the evidence fixture instructions.",
        model="evidence-model",
        allowed_tools=[],
    )


def _employee_two() -> EmployeeDefinition:
    return EmployeeDefinition(
        id="employee-2",
        name="Evidence Employee Two",
        role="Second evidence test employee",
        instructions="Use the second evidence fixture instructions.",
        model="evidence-model",
        allowed_tools=[],
    )


def _fixture(root: Path, *, run_id: str = "run-evidence") -> EvidenceFixture:
    workflow = _workflow()
    run = create_test_run(
        root,
        run_id,
        workflow,
        (_employee(), _employee_two()),
        with_business_approvals=False,
    )
    invocation = ModelInvocationRequest(
        model="evidence-model",
        system_instructions="Use the evidence fixture instructions.",
        task_instructions="Return the safe result.",
        allowed_tools=(),
        run_id=run.binding.run_id,
        manifest_digest=run.binding.manifest_digest,
        run_input="fixture input",
    )
    approval = approve_model_invocation_execution(
        invocation,
        (),
        provider="openai",
        approved_by="evidence-reviewer",
        approval_id="execution-evidence-approval",
        execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    approval_evidence = build_execution_approval_evidence_for_tools(
        invocation,
        (),
        approval,
        workflow_id=workflow.id,
        step_id=workflow.steps[0].id,
        step_index=1,
        employee_id=workflow.steps[0].employee,
        target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    persist_execution_approval_evidence(run.store, approval_evidence)
    context = build_execution_evidence_context(
        store_root=run.store.root,
        binding=run.binding,
        workflow_id=workflow.id,
        step_id=workflow.steps[0].id,
        step_index=1,
        employee_id=workflow.steps[0].employee,
        request=invocation,
        resolved_tools=(),
        approval=approval,
        target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    return EvidenceFixture(run, invocation, approval, context)


def _second_step_execution(fixture: EvidenceFixture):
    request = ModelInvocationRequest(
        model="evidence-model",
        system_instructions="Use the evidence fixture instructions.",
        task_instructions="Return the second safe result.",
        allowed_tools=(),
        run_id=fixture.run.binding.run_id,
        manifest_digest=fixture.run.binding.manifest_digest,
        run_input="input-run-evidence",
    )
    approval = approve_model_invocation_execution(
        request,
        (),
        provider="openai",
        approved_by="evidence-reviewer",
        approval_id="execution-evidence-approval-step-2",
        execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    approval_evidence = build_execution_approval_evidence_for_tools(
        request,
        (),
        approval,
        workflow_id="evidence-workflow",
        step_id="step-2",
        step_index=2,
        employee_id="employee-2",
        target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    persist_execution_approval_evidence(fixture.run.store, approval_evidence)
    context = build_execution_evidence_context(
        store_root=fixture.run.store.root,
        binding=fixture.run.binding,
        workflow_id="evidence-workflow",
        step_id="step-2",
        step_index=2,
        employee_id="employee-2",
        request=request,
        resolved_tools=(),
        approval=approval,
        target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    return request, approval, context


def _success_transition(fixture: EvidenceFixture, invocation_result):
    runtime_result = StepRuntimeExecutionSuccess(
        "evidence-workflow",
        "step-1",
        1,
        "employee-1",
        invocation_result,
        binding=fixture.run.binding,
    )
    state = WorkflowExecutionState(
        "evidence-workflow",
        "running",
        "step-1",
        1,
        "employee-1",
        (),
        None,
        binding=fixture.run.binding,
    )
    return transition_workflow_execution_from_step_result(state, runtime_result)


def _event_with_provenance(event, provenance):
    return RuntimeStepEvent(
        event_type=event.event_type,
        workflow_id=event.workflow_id,
        step_id=event.step_id,
        step_index=event.step_index,
        employee_id=event.employee_id,
        previous_status=event.previous_status,
        next_status=event.next_status,
        provider=event.provider,
        failure_category=event.failure_category,
        response_id=event.response_id,
        request_id=event.request_id,
        output_text=event.output_text,
        message=event.message,
        response_diagnostics=event.response_diagnostics,
        run_id=event.run_id,
        manifest_digest=event.manifest_digest,
        execution_attempt_id=provenance[0],
        execution_attempt_evidence_sha256=provenance[1],
        normalized_result_evidence_sha256=provenance[2],
        raw_response_evidence_sha256=provenance[3],
        raw_response_body_sha256=provenance[4],
    )
def _assert_persistence_rejected_without_writes(fixture, transition) -> None:
    state_before = (
        fixture.run.state_path.read_bytes()
        if fixture.run.state_path.exists()
        else None
    )
    events_before = (
        fixture.run.events_path.read_bytes()
        if fixture.run.events_path.exists()
        else None
    )
    with pytest.raises(WorkflowExecutionPersistenceInputError):
        persist_workflow_execution_transition(
            transition,
            WorkflowExecutionPersistenceTargets(
                fixture.run.state_path,
                fixture.run.events_path,
                binding=fixture.run.binding,
            ),
        )
    assert (
        fixture.run.state_path.read_bytes()
        if fixture.run.state_path.exists()
        else None
    ) == state_before
    assert (
        fixture.run.events_path.read_bytes()
        if fixture.run.events_path.exists()
        else None
    ) == events_before


def _success_response(
    *, body_text: str = "safe response", response_id: str = "response-1"
) -> OpenAIResponsesRawHttpResponse:
    payload = {
        "id": response_id,
        "object": "response",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": body_text}],
            }
        ],
    }
    return OpenAIResponsesRawHttpResponse(
        status_code=200,
        reason="synthetic",
        headers=(
            ("Content-Type", "application/json"),
            ("X-Request-Id", "request-1"),
            ("Authorization", "Bearer response-secret"),
        ),
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )


def _api_error_response() -> OpenAIResponsesRawHttpResponse:
    return OpenAIResponsesRawHttpResponse(
        status_code=429,
        reason="synthetic",
        headers=(("x-request-id", "request-api-error"),),
        body=(
            b'{"error":{"message":"safe API error","type":"rate_limit",'
            b'"param":null,"code":"rate_limit"}}'
        ),
    )


def _api_key() -> OpenAIApiKey:
    return OpenAIApiKey(value=SecretStr("synthetic-provider-key"))


def test_claim_is_durable_before_transport_and_response_is_lossless_but_secret_free(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    seen: list[OpenAIResponsesAuthenticatedHttpRequest] = []

    def transport(
        request_value: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        seen.append(request_value)
        attempts = list_run_execution_evidence(
            fixture.run.store.root, fixture.run.binding.run_id
        )
        assert len(attempts) == 1
        assert execution_attempt_evidence_path(
            fixture.run.store.root,
            fixture.run.binding.run_id,
            attempts[0].attempt_id,
        ).is_file()
        return _success_response(body_text="raw-secret-is-not-normal-output")

    result = execute_openai_model_invocation(
        fixture.request,
        (),
        _api_key(),
        fixture.approval,
        transport=transport,
        execution_evidence=fixture.context,
    )

    assert isinstance(result, ModelInvocationSuccess)
    assert len(seen) == 1
    assert seen[0].headers[-1] == ("Authorization", "Bearer synthetic-provider-key")
    attempts = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )
    assert len(attempts) == 1
    attempt = attempts[0]
    raw = load_raw_response_evidence(
        fixture.run.store.root, fixture.run.binding.run_id, attempt.attempt_id
    )
    normalized = load_normalized_result_evidence(
        fixture.run.store.root, fixture.run.binding.run_id, attempt.attempt_id
    )
    assert (
        raw.body == _success_response(body_text="raw-secret-is-not-normal-output").body
    )
    assert normalized.raw_response_evidence_sha256 == raw.digest
    assert normalized.raw_response_body_sha256 == raw.body_sha256
    assert execution_evidence_of_result(result) == (
        attempt.attempt_id,
        attempt.digest,
        normalized.digest,
        raw.digest,
        raw.body_sha256,
    )
    evidence_bytes = b"".join(
        path.read_bytes()
        for path in fixture.run.store.root.glob("run-evidence.execution-*.json")
    )
    assert b"synthetic-provider-key" not in evidence_bytes
    assert b"Authorization" not in evidence_bytes
    assert b"Bearer response-secret" not in evidence_bytes


def test_attempt_claim_persistence_failure_means_zero_transport_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    calls = 0

    def fail_claim(*_: object, **__: object) -> object:
        raise ExecutionEvidencePersistenceError("ambiguous")

    monkeypatch.setattr(
        responses_execution_module, "claim_execution_attempt", fail_claim
    )

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not be entered")

    with pytest.raises(ExecutionEvidencePersistenceError):
        execute_openai_model_invocation(
            fixture.request,
            (),
            _api_key(),
            fixture.approval,
            transport=transport,
            execution_evidence=fixture.context,
        )
    assert calls == 0
    assert (
        list_run_execution_evidence(fixture.run.store.root, fixture.run.binding.run_id)
        == ()
    )


def test_missing_execution_evidence_means_zero_transport_calls(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not be entered")

    with pytest.raises(ExecutionEvidenceError):
        execute_openai_model_invocation(
            fixture.request,
            (),
            _api_key(),
            fixture.approval,
            transport=transport,
            execution_evidence=None,
        )
    assert calls == 0
    assert list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    ) == ()


def test_unresolved_transport_attempt_blocks_restart_replay(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    calls = 0

    def uncertain_transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise OpenAIResponsesTransportError("safe transport uncertainty")

    first = execute_openai_model_invocation(
        fixture.request,
        (),
        _api_key(),
        fixture.approval,
        transport=uncertain_transport,
        execution_evidence=fixture.context,
    )
    assert first.category == "transport_error"  # type: ignore[union-attr]
    assert calls == 1
    with pytest.raises(ExecutionAttemptAlreadyClaimedError):
        execute_openai_model_invocation(
            fixture.request,
            (),
            _api_key(),
            fixture.approval,
            transport=uncertain_transport,
            execution_evidence=fixture.context,
        )
    assert calls == 1


def test_api_error_raw_response_is_linked_to_normalized_result(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    result = execute_openai_model_invocation(
        fixture.request,
        (),
        _api_key(),
        fixture.approval,
        transport=lambda _: _api_error_response(),
        execution_evidence=fixture.context,
    )
    assert result.category == "api_error"  # type: ignore[union-attr]
    attempt = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )[0]
    raw = load_raw_response_evidence(
        fixture.run.store.root, fixture.run.binding.run_id, attempt.attempt_id
    )
    normalized = load_normalized_result_evidence(
        fixture.run.store.root, fixture.run.binding.run_id, attempt.attempt_id
    )
    assert normalized.failure_category == "api_error"
    assert normalized.raw_response_evidence_sha256 == raw.digest
    assert normalized.raw_response_body_sha256 == raw.body_sha256


def test_immutable_response_evidence_is_idempotent_and_conflict_safe(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    result = execute_openai_model_invocation(
        fixture.request,
        (),
        _api_key(),
        fixture.approval,
        transport=lambda _: _success_response(),
        execution_evidence=fixture.context,
    )
    assert isinstance(result, ModelInvocationSuccess)
    attempt = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )[0]
    raw_path = execution_raw_response_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, attempt.attempt_id
    )
    original = raw_path.read_bytes()
    persisted = persist_raw_response_evidence(
        fixture.context, attempt, _success_response()
    )
    assert persisted == load_raw_response_evidence(
        fixture.run.store.root, fixture.run.binding.run_id, attempt.attempt_id
    )
    assert raw_path.read_bytes() == original
    with pytest.raises(ExecutionEvidenceConflictError):
        persist_raw_response_evidence(
            fixture.context, attempt, _success_response(body_text="different")
        )
    assert raw_path.read_bytes() == original


def test_strict_load_rejects_tampering_and_orphan_sidecars(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    execute_openai_model_invocation(
        fixture.request,
        (),
        _api_key(),
        fixture.approval,
        transport=lambda _: _success_response(),
        execution_evidence=fixture.context,
    )
    attempt = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )[0]
    raw_path = execution_raw_response_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, attempt.attempt_id
    )
    raw_path.write_bytes(raw_path.read_bytes() + b"\n")
    with pytest.raises(ExecutionEvidenceLoadError):
        load_raw_response_evidence(
            fixture.run.store.root, fixture.run.binding.run_id, attempt.attempt_id
        )

    orphan = execution_normalized_result_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, "f" * 64
    )
    orphan.write_bytes(b"{}")
    with pytest.raises(ExecutionEvidenceLoadError):
        list_run_execution_evidence(fixture.run.store.root, fixture.run.binding.run_id)


def test_terminal_event_links_exact_attempt_and_normalized_result(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    invocation_result = execute_openai_model_invocation(
        fixture.request,
        (),
        _api_key(),
        fixture.approval,
        transport=lambda _: _success_response(),
        execution_evidence=fixture.context,
    )
    assert isinstance(invocation_result, ModelInvocationSuccess)
    transition = _success_transition(fixture, invocation_result)
    event = transition.event
    assert event.execution_attempt_id is not None
    assert event.normalized_result_evidence_sha256 is not None
    serialized = serialize_runtime_step_event_jsonl(event)
    loaded = parse_runtime_step_event(
        json.loads(serialized), binding=fixture.run.binding
    )
    assert loaded == event
    persist_workflow_execution_transition(
        transition,
        WorkflowExecutionPersistenceTargets(
            fixture.run.state_path,
            fixture.run.events_path,
            binding=fixture.run.binding,
        ),
    )
    inspection = inspect_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )
    assert inspection[0].final_step_outcome_linked is True


def test_forged_normalized_result_reference_cannot_commit_terminal_state(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    result = execute_openai_model_invocation(
        fixture.request,
        (),
        _api_key(),
        fixture.approval,
        transport=lambda _: _success_response(),
        execution_evidence=fixture.context,
    )
    transition = _success_transition(fixture, result)
    provenance = execution_evidence_of_result(result)
    assert provenance is not None
    forged = (provenance[0], provenance[1], "f" * 64, provenance[3], provenance[4])
    bad_transition = replace(
        transition,
        event=_event_with_provenance(transition.event, forged),
    )
    _assert_persistence_rejected_without_writes(fixture, bad_transition)


def test_cross_attempt_result_provenance_cannot_commit_terminal_state(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    first_result = execute_openai_model_invocation(
        fixture.request,
        (),
        _api_key(),
        fixture.approval,
        transport=lambda _: _success_response(),
        execution_evidence=fixture.context,
    )
    second_request, second_approval, second_context = _second_step_execution(fixture)
    second_result = execute_openai_model_invocation(
        second_request,
        (),
        _api_key(),
        second_approval,
        transport=lambda _: _success_response(),
        execution_evidence=second_context,
    )
    first_provenance = execution_evidence_of_result(first_result)
    second_provenance = execution_evidence_of_result(second_result)
    assert first_provenance is not None and second_provenance is not None

    with pytest.raises(FrozenInstanceError):
        first_result.execution_attempt_id = second_provenance[0]  # type: ignore[attr-defined]
    assert execution_evidence_of_result(first_result) == first_provenance

    transition = _success_transition(fixture, first_result)
    bad_transition = replace(
        transition,
        event=_event_with_provenance(transition.event, second_provenance),
    )
    _assert_persistence_rejected_without_writes(fixture, bad_transition)


def test_cli_inspection_is_read_only_and_does_not_emit_raw_body(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    execute_openai_model_invocation(
        fixture.request,
        (),
        _api_key(),
        fixture.approval,
        transport=lambda _: _success_response(body_text="raw-secret-body"),
        execution_evidence=fixture.context,
    )
    result = runner.invoke(
        app,
        [
            "workflows",
            "execution-evidence",
            fixture.run.binding.run_id,
            "--run-store",
            str(fixture.run.store.root),
        ],
    )
    assert result.exit_code == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["operation"] == "execution-evidence"
    assert value["attempts"][0]["raw_response_received"] is True
    assert value["attempts"][0]["ambiguous_or_unresolved"] is True
    assert value["attempts"][0]["final_step_outcome_linked"] is False
    assert "raw-secret-body" not in result.stdout
    assert "synthetic-provider-key" not in result.stdout
    assert "Authorization" not in result.stdout
