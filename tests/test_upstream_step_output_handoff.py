"""Focused Phase-216 upstream-output handoff contract tests."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PreparedStepExecutionStart,
    UpstreamStepOutputHandoffCompatibilityError,
    build_immediate_predecessor_upstream_inputs,
    prepare_prepared_step_execution_start,
)
from ai_office.engine.approved_workflow_continuation_cycle import (
    ApprovedWorkflowContinuationCycleCompatibilityError as Phase190Error,
)
from ai_office.engine.approved_workflow_continuation_cycle import (
    route_approved_workflow_continuation_cycle,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.persisted_continuation_runtime_facts import (
    build_persisted_continuation_runtime_facts,
)
from ai_office.engine.workflow_approval_evidence import (
    build_execution_approval_evidence_for_tools,
    persist_execution_approval_evidence,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.execution_evidence import build_execution_evidence_context
from ai_office.execution_target import DIRECT_OPENAI_EXECUTION_TARGET
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationExecutionApprovalError,
    ModelInvocationRequest,
    UpstreamStepOutput,
    approve_model_invocation_execution,
    build_model_invocation_execution_fingerprint,
    build_model_invocation_request,
    build_model_invocation_task_input,
    validate_model_invocation_execution_approval,
)
from ai_office.planning.step_execution_request import StepExecutionRequest
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesRawHttpResponse,
    execute_openai_model_invocation,
)
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    WorkflowRunBinding,
    transition_workflow_execution_from_step_result,
)
from ai_office.storage import (
    LoadedWorkflowExecutionHistory,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from tests._run_test_support import TestRun as RunFixture
from tests._run_test_support import create_test_run


def _step_request() -> StepExecutionRequest:
    return StepExecutionRequest(
        workflow_id="workflow",
        workflow_name="Workflow",
        step_index=2,
        step_id="step-2",
        step_name="Second",
        employee_id="employee-2",
        employee_name="Employee 2",
        employee_role="worker",
        model="model",
        allowed_tools=(),
        employee_instructions="system instructions",
        step_instructions="task instructions",
    )


def _history(
    output_text: str = "predecessor output",
    *,
    binding: WorkflowRunBinding | None = None,
) -> LoadedWorkflowExecutionHistory:
    return LoadedWorkflowExecutionHistory(
        state=WorkflowExecutionState(
            workflow_id="workflow",
            status="succeeded",
            current_step_id="step-1",
            current_step_index=1,
            current_employee_id="employee-1",
            completed_step_ids=("step-1",),
            last_failure_category=None,
            binding=binding,
        ),
        events=(
            RuntimeStepEvent(
                event_type="step_succeeded",
                workflow_id="workflow",
                step_id="step-1",
                step_index=1,
                employee_id="employee-1",
                previous_status="running",
                next_status="succeeded",
                provider="openai",
                failure_category=None,
                response_id="response-1",
                request_id="request-1",
                output_text=output_text,
                message=None,
                binding=binding,
            ),
        ),
    )


def _workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "Synthetic workflow",
            "steps": [
                {
                    "id": "step-1",
                    "name": "First",
                    "employee": "employee-1",
                    "instructions": "first task",
                },
                {
                    "id": "step-2",
                    "name": "Second",
                    "employee": "employee-2",
                    "instructions": "second task",
                },
            ],
        }
    )


def _employee() -> EmployeeDefinition:
    return EmployeeDefinition(
        id="employee-2",
        name="Employee 2",
        role="worker",
        instructions="employee-2 instructions",
        model="model",
        allowed_tools=[],
    )


def _run_employees() -> tuple[EmployeeDefinition, ...]:
    return (
        EmployeeDefinition(
            id="employee-1",
            name="Employee 1",
            role="worker",
            instructions="employee-1 instructions",
            model="model",
            allowed_tools=[],
        ),
        _employee(),
    )


def _prepared_next() -> PreparedWorkflowStep:
    return PreparedWorkflowStep(
        workflow_id="workflow",
        step_id="step-2",
        step_index=2,
        employee_id="employee-2",
        employee_instructions="employee-2 instructions",
        step_instructions="second task",
        model="model",
        allowed_tool_names=(),
    )


def _phase190_decision(
    *, binding: WorkflowRunBinding | None = None
) -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        decision="prepare_next_step",
        workflow_id="workflow",
        current_step_id="step-1",
        current_step_index=1,
        current_employee_id="employee-1",
        next_step_id="step-2",
        next_step_index=2,
        next_employee_id="employee-2",
        reason="next_step_available",
        binding=binding,
    )


def _phase190_start(
    upstream: tuple[UpstreamStepOutput, ...],
    *,
    binding: WorkflowRunBinding | None = None,
) -> PreparedStepExecutionStart:
    history = _history("authoritative", binding=binding)
    facts = build_persisted_continuation_runtime_facts(
        "workflow",
        2,
        history,
        state_source_sha256=hashlib.sha256(
            serialize_workflow_execution_state_json(history.state).encode("utf-8")
        ).hexdigest(),
    )
    return PreparedStepExecutionStart(
        request=ModelInvocationRequest(
            model="model",
            system_instructions="employee-2 instructions",
            task_instructions="second task",
            allowed_tools=(),
            upstream_inputs=upstream,
            runtime_facts=facts,
            run_id=None if binding is None else binding.run_id,
            manifest_digest=(None if binding is None else binding.manifest_digest),
            run_input=None if binding is None else f"input-{binding.run_id}",
        ),
        running_state=WorkflowExecutionState(
            workflow_id="workflow",
            status="running",
            current_step_id="step-2",
            current_step_index=2,
            current_employee_id="employee-2",
            completed_step_ids=("step-1",),
            last_failure_category=None,
            binding=binding,
        ),
    )


def _write_history(
    tmp_path: Path,
    output_text: str = "authoritative",
    *,
    run: RunFixture | None = None,
) -> tuple[Path, Path]:
    state_path = tmp_path / "state.json" if run is None else run.state_path
    events_path = tmp_path / "events.jsonl" if run is None else run.events_path
    history = _history(output_text, binding=None if run is None else run.binding)
    if run is not None:
        request = ModelInvocationRequest(
            "model",
            "system instructions",
            "first task",
            (),
            binding=run.binding,
            run_input=f"input-{run.binding.run_id}",
        )
        approval = approve_model_invocation_execution(
            request,
            (),
            provider="openai",
            approved_by="history-reviewer",
            approval_id="history-approval",
            execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
        )
        persist_execution_approval_evidence(
            run.store,
            build_execution_approval_evidence_for_tools(
                request,
                (),
                approval,
                workflow_id="workflow",
                step_id="step-1",
                step_index=1,
                employee_id="employee-1",
                target=DIRECT_OPENAI_EXECUTION_TARGET,
            ),
        )
        evidence = build_execution_evidence_context(
            store_root=run.store.root,
            binding=run.binding,
            workflow_id="workflow",
            step_id="step-1",
            step_index=1,
            employee_id="employee-1",
            request=request,
            resolved_tools=(),
            approval=approval,
            target=DIRECT_OPENAI_EXECUTION_TARGET,
        )
        body = json.dumps(
            {
                "id": "response-1",
                "object": "response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": output_text}],
                    }
                ],
            },
            separators=(",", ":"),
        ).encode()
        invocation = execute_openai_model_invocation(
            request,
            (),
            OpenAIApiKey(value="test-key"),
            approval,
            transport=lambda _request: OpenAIResponsesRawHttpResponse(
                200,
                "synthetic",
                (("x-request-id", "request-1"),),
                body,
            ),
            execution_evidence=evidence,
        )
        history = LoadedWorkflowExecutionHistory(
            state=history.state,
            events=(
                transition_workflow_execution_from_step_result(
                    WorkflowExecutionState(
                        "workflow",
                        "running",
                        "step-1",
                        1,
                        "employee-1",
                        (),
                        None,
                        binding=run.binding,
                    ),
                    StepRuntimeExecutionSuccess(
                        "workflow",
                        "step-1",
                        1,
                        "employee-1",
                        invocation,
                        binding=run.binding,
                    ),
                ).event,
            ),
        )
    state_path.write_bytes(
        serialize_workflow_execution_state_json(history.state).encode("utf-8")
    )
    events_path.write_bytes(
        serialize_runtime_step_event_jsonl(history.events[0]).encode("utf-8")
    )
    return state_path, events_path


def _phase190_common(
    state_path: Path,
    events_path: Path,
    prepared_start: PreparedStepExecutionStart,
    execution_approval: object,
    phase147_calls: list[object],
    transport_calls: list[object],
    *,
    binding: WorkflowRunBinding | None = None,
) -> None:
    workflow = _workflow()
    employee = _employee()

    del prepared_start, phase147_calls
    preparation_approval = NextStepPreparationApproval(
        True,
        "workflow",
        "step-1",
        1,
        "step-2",
        2,
        "employee-2",
    )

    def transport(request: object) -> object:
        transport_calls.append(request)
        return object()

    route_approved_workflow_continuation_cycle(
        _phase190_decision(binding=binding),
        workflow,
        preparation_approval,
        employee,
        state_path,
        events_path,
        (),
        OpenAIApiKey(value="test-key"),
        execution_approval,
        transport,
    )


def test_01_reconstructs_exact_immediate_predecessor_only() -> None:
    value = build_immediate_predecessor_upstream_inputs(
        "workflow", 2, _history("exact predecessor")
    )

    assert value == (
        UpstreamStepOutput(
            workflow_id="workflow",
            step_id="step-1",
            step_index=1,
            employee_id="employee-1",
            output_text="exact predecessor",
        ),
    )
    assert type(value) is tuple
    assert len(value) == 1


def test_02_reconstructs_empty_output_without_replacing_it() -> None:
    value = build_immediate_predecessor_upstream_inputs("workflow", 2, _history(""))

    assert value[0].output_text == ""


def test_03_reconstructs_multiline_unicode_and_whitespace_exactly() -> None:
    sentinel = "  first line\n日本語 😀\n\tlast line  "

    value = build_immediate_predecessor_upstream_inputs(
        "workflow", 2, _history(sentinel)
    )

    assert value[0].output_text == sentinel


def test_04_rejects_workflow_identity_mismatch_without_exposing_output() -> None:
    with pytest.raises(UpstreamStepOutputHandoffCompatibilityError) as caught:
        build_immediate_predecessor_upstream_inputs(
            "other-workflow", 2, _history("secret output")
        )

    assert caught.value.detail.classification == "workflow_identity"
    assert "secret output" not in str(caught.value)


def test_05_rejects_next_step_index_or_history_identity_mismatch() -> None:
    with pytest.raises(UpstreamStepOutputHandoffCompatibilityError) as caught:
        build_immediate_predecessor_upstream_inputs("workflow", 3, _history())

    assert caught.value.detail.classification == "next_step_index"

    wrong_event = replace(_history().events[0], step_id="wrong-step")
    wrong_history = LoadedWorkflowExecutionHistory(_history().state, (wrong_event,))
    with pytest.raises(UpstreamStepOutputHandoffCompatibilityError) as caught:
        build_immediate_predecessor_upstream_inputs("workflow", 2, wrong_history)

    assert caught.value.detail.classification == "history_identity"


def test_06_request_builder_default_is_legacy_empty_upstream() -> None:
    request = build_model_invocation_request(_step_request())

    assert request.upstream_inputs == ()
    assert request.system_instructions == "system instructions"
    assert request.task_instructions == "task instructions"
    assert build_model_invocation_task_input(request) == "task instructions"


def test_07_request_builder_attaches_the_exact_upstream_tuple() -> None:
    upstream = (UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "  exact  "),)

    request = build_model_invocation_request(_step_request(), upstream_inputs=upstream)

    assert request.upstream_inputs is upstream
    assert request.upstream_inputs[0].output_text == "  exact  "
    with pytest.raises(TypeError):
        build_model_invocation_request(  # type: ignore[arg-type]
            _step_request(), upstream_inputs=[upstream[0]]
        )


def test_08_task_input_without_upstream_is_exact_legacy_text() -> None:
    task = "  keep this\n日本語\twithout changes  "
    request = ModelInvocationRequest("model", "system", task, ())

    assert build_model_invocation_task_input(request) == task


def test_09_task_input_is_canonical_task_side_json_and_system_stays_separate() -> None:
    upstream = UpstreamStepOutput(
        "workflow", "step-1", 1, "employee-1", "  sentinel\n日本語 😀  "
    )
    request = ModelInvocationRequest(
        "model",
        "system instruction must remain separate",
        "task\n",
        (),
        (upstream,),
    )

    assert build_model_invocation_task_input(request) == (
        '{"task_instructions":"task\\n","upstream_inputs":[{"employee_id":"employee-1",'
        '"output_text":"  sentinel\\n日本語 😀  ","step_id":"step-1","step_index":1,'
        '"workflow_id":"workflow"}]}'
    )
    assert request.system_instructions == "system instruction must remain separate"


def test_10_empty_upstream_fingerprint_matches_legacy_payload() -> None:
    request = ModelInvocationRequest("model", "system", "task", ())
    expected_payload = json.dumps(
        {
            "allowed_tools": [],
            "model": "model",
            "resolved_tools": [],
            "system_instructions": "system",
            "task_instructions": "task",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    expected = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()

    assert build_model_invocation_execution_fingerprint(request, ()) == expected


def test_11_fingerprint_changes_when_only_upstream_output_changes() -> None:
    first = ModelInvocationRequest(
        "model",
        "system",
        "task",
        (),
        (UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "one"),),
    )
    second = ModelInvocationRequest(
        "model",
        "system",
        "task",
        (),
        (UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "two"),),
    )

    assert build_model_invocation_execution_fingerprint(first, ()) != (
        build_model_invocation_execution_fingerprint(second, ())
    )


def test_12_fingerprint_changes_when_only_upstream_provenance_changes() -> None:
    first = ModelInvocationRequest(
        "model",
        "system",
        "task",
        (),
        (UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "same"),),
    )
    second = ModelInvocationRequest(
        "model",
        "system",
        "task",
        (),
        (UpstreamStepOutput("workflow", "step-1", 2, "employee-2", "same"),),
    )

    assert build_model_invocation_execution_fingerprint(first, ()) != (
        build_model_invocation_execution_fingerprint(second, ())
    )


def test_13_approval_validation_rejects_changed_upstream() -> None:
    original = ModelInvocationRequest(
        "model",
        "system",
        "task",
        (),
        (UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "approved"),),
    )
    changed = ModelInvocationRequest(
        "model",
        "system",
        "task",
        (),
        (UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "changed"),),
    )
    approval = approve_model_invocation_execution(
        original, (), provider="openai", approved_by="reviewer", approval_id="id"
    )
    legacy_approval = approve_model_invocation_execution(
        ModelInvocationRequest("model", "system", "task", ()),
        (),
        provider="openai",
        approved_by="reviewer",
        approval_id="legacy-id",
    )
    validate_model_invocation_execution_approval(
        original, (), approval, provider="openai"
    )

    for candidate, candidate_approval in (
        (original, legacy_approval),
        (changed, legacy_approval),
        (changed, approval),
    ):
        with pytest.raises(ModelInvocationExecutionApprovalError):
            validate_model_invocation_execution_approval(
                candidate, (), candidate_approval, provider="openai"
            )


def test_14_prepared_step_start_reconstructs_exact_predecessor_output() -> None:
    result = prepare_prepared_step_execution_start(
        _prepared_next(), _history("handoff"), state_source_sha256="a" * 64
    )

    assert result.request.upstream_inputs == (
        UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "handoff"),
    )
    assert result.request.system_instructions == "employee-2 instructions"
    assert result.request.task_instructions == "second task"
    assert result.running_state == WorkflowExecutionState(
        "workflow", "running", "step-2", 2, "employee-2", ("step-1",), None
    )


def test_phase190_rejects_stale_approval_before_running_or_provider(
    tmp_path: Path,
) -> None:
    run = create_test_run(tmp_path, "run-upstream", _workflow(), _run_employees())
    state_path, events_path = _write_history(tmp_path, run=run)
    before = state_path.read_bytes(), events_path.read_bytes()
    authoritative = (
        UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "authoritative"),
    )
    start = _phase190_start(authoritative, binding=run.binding)
    approval = approve_model_invocation_execution(
        start.request, (), provider="openai", approved_by="reviewer", approval_id="id"
    )
    stale = ModelInvocationExecutionApproval(
        approved=True,
        provider=approval.provider,
        request_fingerprint="0" * 64,
        approved_by=approval.approved_by,
        approval_id=approval.approval_id,
    )
    phase147_calls: list[object] = []
    transport_calls: list[object] = []

    invalid_approvals = (
        stale,
        None,
        object(),
        replace(approval, provider="other"),
    )
    for invalid in invalid_approvals:
        phase147_calls.clear()
        transport_calls.clear()
        with pytest.raises(Phase190Error) as caught:
            _phase190_common(
                state_path,
                events_path,
                start,
                invalid,
                phase147_calls,
                transport_calls,
                binding=run.binding,
            )

        assert caught.value.detail.classification == "approval_contract"
        assert phase147_calls == []
        assert transport_calls == []
        assert (state_path.read_bytes(), events_path.read_bytes()) == before
