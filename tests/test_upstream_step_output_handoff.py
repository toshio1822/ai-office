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
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
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
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    LoadedWorkflowExecutionHistory,
    RunningStatePersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


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


def _history(output_text: str = "predecessor output") -> LoadedWorkflowExecutionHistory:
    return LoadedWorkflowExecutionHistory(
        state=WorkflowExecutionState(
            workflow_id="workflow",
            status="succeeded",
            current_step_id="step-1",
            current_step_index=1,
            current_employee_id="employee-1",
            completed_step_ids=("step-1",),
            last_failure_category=None,
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


def _phase190_decision() -> WorkflowProgressionDecision:
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
    )


def _phase190_start(
    upstream: tuple[UpstreamStepOutput, ...],
) -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        request=ModelInvocationRequest(
            model="model",
            system_instructions="employee-2 instructions",
            task_instructions="second task",
            allowed_tools=(),
            upstream_inputs=upstream,
        ),
        running_state=WorkflowExecutionState(
            workflow_id="workflow",
            status="running",
            current_step_id="step-2",
            current_step_index=2,
            current_employee_id="employee-2",
            completed_step_ids=("step-1",),
            last_failure_category=None,
        ),
    )


def _write_history(
    tmp_path: Path, output_text: str = "authoritative"
) -> tuple[Path, Path]:
    state_path = tmp_path / "state.json"
    events_path = tmp_path / "events.jsonl"
    history = _history(output_text)
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
) -> None:
    workflow = _workflow()
    employee = _employee()

    def phase147(*args: object) -> RunningStatePersistenceResult:
        phase147_calls.append(args)
        return RunningStatePersistenceResult(1)

    def phase155(*args: object) -> object:
        transport_calls.append(args)
        return object()

    route_approved_workflow_continuation_cycle(
        _phase190_decision(),
        workflow,
        object(),
        employee,
        state_path,
        events_path,
        (),
        object(),
        execution_approval,
        object(),
        phase145_function=lambda *args: _prepared_next(),
        phase146_function=lambda *args: prepared_start,
        phase147_function=phase147,
        phase155_function=phase155,
        phase172_function=lambda *args: object(),
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
    upstream = (
        UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "  exact  "),
    )

    request = build_model_invocation_request(
        _step_request(), upstream_inputs=upstream
    )

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
        "model", "system", "task", (),
        (UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "one"),),
    )
    second = ModelInvocationRequest(
        "model", "system", "task", (),
        (UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "two"),),
    )

    assert build_model_invocation_execution_fingerprint(first, ()) != (
        build_model_invocation_execution_fingerprint(second, ())
    )


def test_12_fingerprint_changes_when_only_upstream_provenance_changes() -> None:
    first = ModelInvocationRequest(
        "model", "system", "task", (),
        (UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "same"),),
    )
    second = ModelInvocationRequest(
        "model", "system", "task", (),
        (UpstreamStepOutput("workflow", "step-1", 2, "employee-2", "same"),),
    )

    assert build_model_invocation_execution_fingerprint(first, ()) != (
        build_model_invocation_execution_fingerprint(second, ())
    )


def test_13_approval_validation_rejects_changed_upstream() -> None:
    original = ModelInvocationRequest(
        "model", "system", "task", (),
        (UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "approved"),),
    )
    changed = ModelInvocationRequest(
        "model", "system", "task", (),
        (UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "changed"),),
    )
    approval = approve_model_invocation_execution(
        original, (), provider="openai", approved_by="reviewer", approval_id="id"
    )

    with pytest.raises(ModelInvocationExecutionApprovalError):
        validate_model_invocation_execution_approval(
            changed, (), approval, provider="openai"
        )


def test_14_prepared_step_start_reconstructs_exact_predecessor_output() -> None:
    result = prepare_prepared_step_execution_start(
        _prepared_next(), _history("handoff")
    )

    assert result.request.upstream_inputs == (
        UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "handoff"),
    )
    assert result.request.system_instructions == "employee-2 instructions"
    assert result.request.task_instructions == "second task"
    assert result.running_state == WorkflowExecutionState(
        "workflow", "running", "step-2", 2, "employee-2", ("step-1",), None
    )


def test_15_phase190_rejects_wrong_phase146_upstream_before_running_or_provider(
    tmp_path: Path,
) -> None:
    state_path, events_path = _write_history(tmp_path)
    before = state_path.read_bytes(), events_path.read_bytes()
    wrong_start = _phase190_start(
        (UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "wrong"),)
    )
    phase147_calls: list[object] = []
    transport_calls: list[object] = []

    with pytest.raises(Phase190Error) as caught:
        _phase190_common(
            state_path,
            events_path,
            wrong_start,
            object(),
            phase147_calls,
            transport_calls,
        )

    assert caught.value.detail.classification == "phase146_contract"
    assert phase147_calls == []
    assert transport_calls == []
    assert (state_path.read_bytes(), events_path.read_bytes()) == before


def test_16_phase190_rejects_stale_approval_before_running_or_provider(
    tmp_path: Path,
) -> None:
    state_path, events_path = _write_history(tmp_path)
    before = state_path.read_bytes(), events_path.read_bytes()
    authoritative = (
        UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "authoritative"),
    )
    start = _phase190_start(authoritative)
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

    with pytest.raises(Phase190Error) as caught:
        _phase190_common(
            state_path,
            events_path,
            start,
            stale,
            phase147_calls,
            transport_calls,
        )

    assert caught.value.detail.classification == "approval_contract"
    assert phase147_calls == []
    assert transport_calls == []
    assert (state_path.read_bytes(), events_path.read_bytes()) == before
