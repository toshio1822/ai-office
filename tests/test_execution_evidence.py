"""Behavioral coverage for Milestone 3 immutable execution evidence."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, dataclass, replace
from hashlib import sha256
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
from ai_office.execution_target import (
    DIRECT_OPENAI_EXECUTION_TARGET,
    OPENAI_CHAT_COMPLETIONS_PROTOCOL,
    OPENAI_RESPONSES_PROTOCOL,
    ModelExecutionTarget,
)
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationRequest,
    ModelInvocationSuccess,
    UpstreamStepOutput,
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
    load_workflow_execution_history,
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
    target: ModelExecutionTarget


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


def _fixture(
    root: Path,
    *,
    run_id: str = "run-evidence",
    target: ModelExecutionTarget = DIRECT_OPENAI_EXECUTION_TARGET,
) -> EvidenceFixture:
    workflow = _workflow()
    run = create_test_run(
        root,
        run_id,
        workflow,
        (_employee(), _employee_two()),
        with_business_approvals=False,
        execution_target=target,
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
        provider=target.provider,
        approved_by="evidence-reviewer",
        approval_id="execution-evidence-approval",
        execution_target=target,
    )
    approval_evidence = build_execution_approval_evidence_for_tools(
        invocation,
        (),
        approval,
        workflow_id=workflow.id,
        step_id=workflow.steps[0].id,
        step_index=1,
        employee_id=workflow.steps[0].employee,
        target=target,
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
        target=target,
    )
    return EvidenceFixture(run, invocation, approval, context, target)


def _second_step_execution(fixture: EvidenceFixture):
    request = ModelInvocationRequest(
        model="evidence-model",
        system_instructions="Use the evidence fixture instructions.",
        task_instructions="Return the second safe result.",
        allowed_tools=(),
        upstream_inputs=(
            UpstreamStepOutput(
                workflow_id="evidence-workflow",
                step_id="step-1",
                step_index=1,
                employee_id="employee-1",
                output_text="handoff-marker-from-step-one",
            ),
        ),
        run_id=fixture.run.binding.run_id,
        manifest_digest=fixture.run.binding.manifest_digest,
        run_input="input-run-evidence",
    )
    approval = approve_model_invocation_execution(
        request,
        (),
        provider=fixture.target.provider,
        approved_by="evidence-reviewer",
        approval_id="execution-evidence-approval-step-2",
        execution_target=fixture.target,
    )
    approval_evidence = build_execution_approval_evidence_for_tools(
        request,
        (),
        approval,
        workflow_id="evidence-workflow",
        step_id="step-2",
        step_index=2,
        employee_id="employee-2",
        target=fixture.target,
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
        target=fixture.target,
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
    *,
    body_text: str = "safe response",
    response_id: str = "response-1",
    request_id: str | None = "request-1",
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
    safe_headers: tuple[tuple[str, str], ...] = (("Content-Type", "application/json"),)
    if request_id is not None:
        safe_headers += (("X-Request-Id", request_id),)
    return OpenAIResponsesRawHttpResponse(
        status_code=200,
        reason="synthetic",
        headers=safe_headers + (("Authorization", "Bearer response-secret"),),
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


def _chat_success_response(
    text: str, response_id: str
) -> OpenAIResponsesRawHttpResponse:
    return OpenAIResponsesRawHttpResponse(
        status_code=200,
        reason="synthetic",
        headers=(("Content-Type", "application/json"),),
        body=json.dumps(
            {
                "id": response_id,
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": text},
                    }
                ],
            }
        ).encode("utf-8"),
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
    assert attempt.request_body_base64 is not None
    saved_request_body = base64.b64decode(attempt.request_body_base64)
    assert saved_request_body.decode("utf-8") == seen[0].body
    assert attempt.request_body_length == len(seen[0].body.encode("utf-8"))
    assert seen[0].body not in repr(attempt)
    assert "raw-secret-is-not-normal-output" not in repr(attempt)
    assert attempt.request_body_base64 not in repr(attempt)
    assert "raw-secret-is-not-normal-output" not in str(attempt)
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


def test_chat_completions_handoff_attempts_store_exact_transport_bodies(
    tmp_path: Path,
) -> None:
    target = ModelExecutionTarget(
        provider="example-chat",
        protocol=OPENAI_CHAT_COMPLETIONS_PROTOCOL,
        base_url="https://api.example.com/v1/chat/completions",
        credential_environment_variable="EXAMPLE_CHAT_API_KEY",
        allow_loopback_http=False,
        configuration_fingerprint="a" * 64,
    )
    fixture = _fixture(tmp_path, run_id="run-chat-handoff", target=target)
    transport_bodies: list[str] = []

    def transport(request_value: OpenAIResponsesAuthenticatedHttpRequest):
        transport_bodies.append(request_value.body)
        return _chat_success_response(
            "handoff-marker-from-step-one" if len(transport_bodies) == 1 else "final",
            f"chat-response-{len(transport_bodies)}",
        )

    execute_openai_model_invocation(
        fixture.request,
        (),
        _api_key(),
        fixture.approval,
        transport=transport,
        execution_evidence=fixture.context,
    )
    second_request, second_approval, second_context = _second_step_execution(fixture)
    execute_openai_model_invocation(
        second_request,
        (),
        _api_key(),
        second_approval,
        transport=transport,
        execution_evidence=second_context,
    )

    attempts = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )
    assert len(transport_bodies) == 2
    assert [attempt.step_index for attempt in attempts] == [1, 2]
    stored_bodies = [
        base64.b64decode(attempt.request_body_base64 or b"")
        for attempt in attempts
    ]
    assert stored_bodies == [body.encode("utf-8") for body in transport_bodies]
    assert all(
        attempt.request_body_sha256 == sha256(body.encode("utf-8")).hexdigest()
        for attempt, body in zip(attempts, transport_bodies, strict=True)
    )
    second_chat_body = json.loads(transport_bodies[1])
    second_user_message = next(
        message
        for message in second_chat_body["messages"]
        if message["role"] == "user"
    )
    assert "handoff-marker-from-step-one" in second_user_message["content"]


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


@pytest.mark.parametrize(
    "protocol",
    [OPENAI_RESPONSES_PROTOCOL, OPENAI_CHAT_COMPLETIONS_PROTOCOL],
)
def test_timed_out_attempt_persists_unknown_result_without_response_or_replay(
    tmp_path: Path,
    protocol: str,
) -> None:
    target = (
        DIRECT_OPENAI_EXECUTION_TARGET
        if protocol == OPENAI_RESPONSES_PROTOCOL
        else ModelExecutionTarget(
            provider="example-chat",
            protocol=protocol,
            base_url="https://api.example.com/v1/chat/completions",
            credential_environment_variable="EXAMPLE_CHAT_API_KEY",
            allow_loopback_http=False,
            configuration_fingerprint="a" * 64,
        )
    )
    fixture = _fixture(tmp_path, run_id=f"run-timeout-{protocol}", target=target)
    calls = 0

    def timeout_after_dispatch(_: OpenAIResponsesAuthenticatedHttpRequest):
        nonlocal calls
        calls += 1
        raise OpenAIResponsesTransportError(
            "OpenAI Responses HTTP request timed out; remote outcome is unknown"
        )

    result = execute_openai_model_invocation(
        fixture.request,
        (),
        _api_key(),
        fixture.approval,
        transport=timeout_after_dispatch,
        execution_evidence=fixture.context,
    )

    attempts = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )
    assert result.category == "transport_error"  # type: ignore[union-attr]
    assert result.message == (  # type: ignore[union-attr]
        "OpenAI Responses HTTP request timed out; remote outcome is unknown"
    )
    assert calls == 1
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.request_body_sha256 is not None
    normalized = load_normalized_result_evidence(
        fixture.run.store.root, fixture.run.binding.run_id, attempt.attempt_id
    )
    assert normalized.failure_category == "transport_error"
    assert normalized.status_code is None
    assert normalized.raw_response_evidence_sha256 is None
    assert not execution_raw_response_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, attempt.attempt_id
    ).exists()

    with pytest.raises(ExecutionAttemptAlreadyClaimedError):
        execute_openai_model_invocation(
            fixture.request,
            (),
            _api_key(),
            fixture.approval,
            transport=timeout_after_dispatch,
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


@pytest.mark.parametrize(
    "tamper",
    [
        "missing-normalized-result",
        "event-response-id",
        "event-request-id",
        "event-output",
        "event-attempt-digest",
        "event-linkage-removed",
        "raw-response-body",
    ],
)
def test_run_bound_missing_request_id_requires_intact_execution_evidence_before_reentry(
    tmp_path: Path,
    tamper: str,
) -> None:
    fixture = _fixture(tmp_path)
    result = execute_openai_model_invocation(
        fixture.request,
        (),
        _api_key(),
        fixture.approval,
        transport=lambda _: _success_response(request_id=None),
        execution_evidence=fixture.context,
    )
    assert isinstance(result, ModelInvocationSuccess)
    assert result.request_id is None
    transition = _success_transition(fixture, result)
    targets = WorkflowExecutionPersistenceTargets(
        fixture.run.state_path,
        fixture.run.events_path,
        binding=fixture.run.binding,
    )
    persist_workflow_execution_transition(transition, targets)
    assert load_workflow_execution_history(targets).events[0].request_id is None

    attempt = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )[0]
    if tamper == "missing-normalized-result":
        execution_normalized_result_evidence_path(
            fixture.run.store.root,
            fixture.run.binding.run_id,
            attempt.attempt_id,
        ).unlink()
    elif tamper == "raw-response-body":
        raw_path = execution_raw_response_evidence_path(
            fixture.run.store.root,
            fixture.run.binding.run_id,
            attempt.attempt_id,
        )
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        raw["body_base64"] = "e30="
        raw_path.write_text(json.dumps(raw), encoding="utf-8")
    else:
        event = json.loads(fixture.run.events_path.read_text(encoding="utf-8"))
        if tamper == "event-response-id":
            event["response_id"] = "forged-response"
        elif tamper == "event-request-id":
            event["request_id"] = "forged-request"
        elif tamper == "event-output":
            event["output_text"] = "forged output"
        elif tamper == "event-linkage-removed":
            for field in (
                "execution_attempt_id",
                "execution_attempt_evidence_sha256",
                "normalized_result_evidence_sha256",
                "raw_response_evidence_sha256",
                "raw_response_body_sha256",
            ):
                event.pop(field)
        else:
            event["execution_attempt_evidence_sha256"] = "f" * 64
        fixture.run.events_path.write_text(
            json.dumps(event, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    with pytest.raises(ExecutionEvidenceError):
        load_workflow_execution_history(targets)


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
    first_attempt_path = execution_attempt_evidence_path(
        fixture.run.store.root,
        fixture.run.binding.run_id,
        value["attempts"][0]["attempt_id"],
    )
    assert first_attempt_path.stat().st_mode & 0o777 == 0o600

    cli_args = [
        "workflows",
        "execution-evidence",
        fixture.run.binding.run_id,
        "--run-store",
        str(fixture.run.store.root),
        "--show-content",
        "--attempt-id",
        value["attempts"][0]["attempt_id"],
    ]
    environment = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (source_root, environment.get("PYTHONPATH", "")) if item
    )
    displayed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ai_office.cli import app; from typer.testing import CliRunner; "
            "import sys; result = CliRunner().invoke(app, sys.argv[1:]); "
            "print(result.stdout, end=''); raise SystemExit(result.exit_code)",
            *cli_args,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert displayed.returncode == 0, displayed.stderr
    content = json.loads(displayed.stdout)
    assert content["request"]["body_text"] is not None
    assert content["request"]["body_length"] == len(
        content["request"]["body_text"].encode("utf-8")
    )
    assert content["request"]["body_sha256"]
    assert content["raw_response"]["body_text"] is not None
    assert content["normalized_result"]["text"] == "raw-secret-body"
    assert content["warning"].startswith("Explicit content view")
    assert "synthetic-provider-key" not in displayed.stdout
    assert "Bearer" not in displayed.stdout


def test_request_body_evidence_is_integrity_checked_on_reload(tmp_path: Path) -> None:
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
    path = execution_attempt_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, attempt.attempt_id
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    original = record["request_body_base64"]
    record["request_body_base64"] = original[:-4] + "AAAA"
    path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    with pytest.raises(ExecutionEvidenceLoadError):
        list_run_execution_evidence(fixture.run.store.root, fixture.run.binding.run_id)
