"""Real Phase 177 → Phase 172 accumulated-None compatibility regression (Issue #380).

A real Phase 177 continuation can legitimately produce durable predecessor
history where step 5 and step 6 both succeeded with ``request_id=None``
(provider ``"openai"``): step 5 is the aged immediate predecessor of the
Phase-176 input, and step 6's runtime result carries ``request_id=None``.
Once that None provenance accumulates two-deep and then ages during a later
explicit continuation cycle, seven current production entry boundaries
(Phase 161 / 142 / 134 / 143 / 135 / 144 / 136) reject it independently
unless the bounded accumulated request-ID compatibility rule is applied at
each boundary.

These tests run the real public Phase 177 boundary (real Phase 176 / real
Phase 147 capture / real Phase 155 with a synthetic final transport) and then
the real public Phase 172 boundary (Phase 161 → Phase 143 → Phase 144 with
all default production dependencies) to prove the whole real chain persists,
classifies, and progresses the accumulated-None predecessor provenance
without any reconstruction or rewriting.

Requirement-to-test mapping (Issue #380):
- A: eight-step workflow, step-6 runtime success (request_id=None) through
  real Phase 177 -> exact step-7 success -> real Phase 172 exact step-7
  persistence/classification/progression -> exact prepare_next_step for
  step 8; step 8 not prepared/started/executed
  -> test_real_a_eight_step_step7_success_prepares_step8
- B: seven-step workflow, step-6 runtime success (request_id=None) through
  real Phase 177 -> exact final step-7 success -> real Phase 172 exact
  workflow_complete (reason last_step_succeeded)
  -> test_real_b_seven_step_step7_success_workflow_complete
- C: eight-step workflow, step-6 runtime success (request_id=None) through
  real Phase 177 -> exact deterministic step-7 failure -> real Phase 172
  exact failure persistence/classification -> exact persisted_failure, no
  retry/further execution
  -> test_real_c_eight_step_step7_failure_persisted_failure
- D: original stop identity with matching terminal snapshots through real
  Phase 177: exact workflow_complete + matching succeeded terminal
  state/history and exact persisted_failure + matching failed terminal
  state/history -> exact same object by identity, bytes unchanged
  -> test_real_d_stop_identity_matching_terminal_snapshots
"""

# ruff: noqa: E501,E701,E702,F401,I001

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    route_runtime_result_to_persisted_running_execution_orchestration_boundary,
    route_runtime_result_to_prepared_start_persistence_orchestration_boundary,
    route_runtime_result_to_progression_orchestration_boundary,
)
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError,
    route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
)
from ai_office.engine.next_step_preparation import NextStepPreparationApproval
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    ModelInvocationSuccess,
    UpstreamStepOutput,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesRawHttpResponse,
)
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition

phase177 = route_runtime_result_to_persisted_running_execution_orchestration_boundary
phase176 = route_runtime_result_to_prepared_start_persistence_orchestration_boundary
phase147 = route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
phase155 = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
phase172 = route_runtime_result_to_progression_orchestration_boundary

TOOLS = (
    ToolDefinition("tool-one", "Tool One", ()),
    ToolDefinition("tool-two", "Tool Two", ()),
)


def workflow(steps: int) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": f"step-{i}",
                    "name": f"Step {i}",
                    "employee": f"e{i}",
                    "instructions": f"step-{i}",
                }
                for i in range(1, steps + 1)
            ],
        }
    )


def predecessor_event(step_id: str, index: int, **changes: object) -> RuntimeStepEvent:
    return replace(
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            step_id,
            index,
            f"e{index}",
            "running",
            "succeeded",
            "openai",
            None,
            f"response-{step_id}",
            f"request-{step_id}",
            f"output-{step_id}",
            None,
        ),
        **changes,  # type: ignore[arg-type]
    )


def canonical_running_setup(
    root: Path, steps: int = 8, current: int = 6, *, five_request_id: object = None
) -> dict[str, object]:
    """Canonical Phase-155 running state at ``current`` with events 1..current-1.

    Immediate predecessor (current-1): provider=openai, output_text="",
    request_id=None unless ``five_request_id`` (a non-empty built-in string)
    is supplied, in which case that exact request_id is used. Earlier empty
    outputs for indices 2..4 (authorized).
    """
    root.mkdir(parents=True, exist_ok=True)
    state_path, events_path = root / "state", root / "events"
    wf = workflow(steps)
    state = WorkflowExecutionState(
        "w",
        "running",
        wf.steps[current - 1].id,
        current,
        wf.steps[current - 1].employee,
        tuple(step.id for step in wf.steps[: current - 1]),
        None,
    )
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events = []
    for index in range(1, current):
        if index == current - 1:
            if five_request_id is None:
                events.append(
                    predecessor_event(
                        wf.steps[index - 1].id, index, output_text="", request_id=None
                    )
                )
            else:
                events.append(
                    predecessor_event(
                        wf.steps[index - 1].id,
                        index,
                        output_text="",
                        request_id=five_request_id,
                    )
                )
        elif index in (2, 3, 4):
            events.append(
                predecessor_event(wf.steps[index - 1].id, index, output_text="")
            )
        else:
            events.append(predecessor_event(wf.steps[index - 1].id, index))
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )
    return {
        "workflow": wf,
        "state_path": state_path,
        "events_path": events_path,
        "state_before": state_path.read_bytes(),
        "events_before": events_path.read_bytes(),
    }


def terminal_setup(
    root: Path, steps: int = 6, *, fail: bool = False
) -> dict[str, object]:
    """Matching terminal snapshot for a terminal stop at the final step.

    Succeeded: state status="succeeded", completed 1..steps, no failure
    category; history = steps-1 normal successors + one exact final
    step_succeeded terminal event.  Failed: state status="failed",
    completed 1..steps-1, failure_category="api_error"; history = steps-1
    normal successors + one exact final step_failed terminal event.
    """
    root.mkdir(parents=True, exist_ok=True)
    state_path, events_path = root / "state", root / "events"
    wf = workflow(steps)
    last = steps
    if fail:
        state = WorkflowExecutionState(
            "w",
            "failed",
            wf.steps[last - 1].id,
            last,
            wf.steps[last - 1].employee,
            tuple(step.id for step in wf.steps[: last - 1]),
            "api_error",
        )
    else:
        state = WorkflowExecutionState(
            "w",
            "succeeded",
            wf.steps[last - 1].id,
            last,
            wf.steps[last - 1].employee,
            tuple(step.id for step in wf.steps[:last]),
            None,
        )
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events = [predecessor_event(wf.steps[i].id, i + 1) for i in range(last - 1)]
    final_step = wf.steps[last - 1]
    if fail:
        events.append(
            RuntimeStepEvent(
                "step_failed",
                "w",
                final_step.id,
                last,
                final_step.employee,
                "running",
                "failed",
                "openai",
                "api_error",
                None,
                None,
                None,
                "safe failure",  # failure_category, response_id, request_id, output_text, message
            )
        )
    else:
        events.append(
            RuntimeStepEvent(
                "step_succeeded",
                "w",
                final_step.id,
                last,
                final_step.employee,
                "running",
                "succeeded",
                "openai",
                None,
                f"response-{final_step.id}",
                None,
                "output",
                None,
            )
        )
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )
    return {
        "workflow": wf,
        "state_path": state_path,
        "events_path": events_path,
        "state_before": state_path.read_bytes(),
        "events_before": events_path.read_bytes(),
    }


def runtime_success(
    wf: WorkflowDefinition, index: int, request_id_none: bool = False
) -> StepRuntimeExecutionSuccess:
    step = wf.steps[index - 1]
    return StepRuntimeExecutionSuccess(
        "w",
        step.id,
        index,
        step.employee,
        ModelInvocationSuccess(
            "openai",
            f"response-{step.id}",
            None if request_id_none else f"request-{step.id}",
            "completed",
            ("output",),
            "output",
        ),
    )


def runtime_failure(wf: WorkflowDefinition, index: int) -> StepRuntimeExecutionFailure:
    step = wf.steps[index - 1]
    return StepRuntimeExecutionFailure(
        "w",
        step.id,
        index,
        step.employee,
        ModelInvocationFailure(
            "openai", "api_error", "safe failure", f"request-{step.id}", 500, None, None
        ),
    )


def prepare_decision(wf: WorkflowDefinition, index: int) -> WorkflowProgressionDecision:
    current = wf.steps[index - 1]
    following = wf.steps[index]
    return WorkflowProgressionDecision(
        "prepare_next_step",
        "w",
        current.id,
        index,
        current.employee,
        following.id,
        index + 1,
        following.employee,
        "next_step_available",
    )


def complete_decision(wf: WorkflowDefinition, index: int) -> WorkflowProgressionDecision:
    step = wf.steps[index - 1]
    return WorkflowProgressionDecision(
        "workflow_complete",
        "w",
        step.id,
        index,
        step.employee,
        None,
        None,
        None,
        "last_step_succeeded",
    )


def failure_outcome(wf: WorkflowDefinition, index: int) -> PersistedExecutionOutcome:
    step = wf.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_failure", "w", step.id, index, step.employee, "api_error"
    )


def approval_for(decision: WorkflowProgressionDecision) -> NextStepPreparationApproval:
    return NextStepPreparationApproval(
        True,
        decision.workflow_id,
        decision.current_step_id,
        decision.current_step_index,
        decision.next_step_id,
        decision.next_step_index,
        decision.next_employee_id,
    )


def employee_for(decision: WorkflowProgressionDecision) -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": decision.next_employee_id,
            "name": "Next Employee",
            "role": "role",
            "instructions": "employee instructions",
            "model": "model-name",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )


def prepared_for(
    wf: WorkflowDefinition,
    decision: WorkflowProgressionDecision,
    employee: EmployeeDefinition,
) -> object:
    step = wf.steps[decision.next_step_index - 1]
    return __import__(
        "ai_office.engine.next_step_preparation", fromlist=["PreparedWorkflowStep"]
    ).PreparedWorkflowStep(
        wf.id,
        step.id,
        decision.next_step_index,
        employee.id,
        employee.instructions,
        step.instructions,
        employee.model,
        tuple(employee.allowed_tools),
    )


def prepared_start_for(
    wf: WorkflowDefinition, prepared: object
) -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            prepared.model,
            prepared.employee_instructions,
            prepared.step_instructions,
            prepared.allowed_tool_names,
        ),
        WorkflowExecutionState(
            prepared.workflow_id,
            "running",
            prepared.step_id,
            prepared.step_index,
            prepared.employee_id,
            tuple(step.id for step in wf.steps[: prepared.step_index - 1]),
            None,
        ),
    )


def constructed_start(values: dict[str, object]) -> PreparedStepExecutionStart:
    """Build a valid step-7 PreparedStepExecutionStart without touching files."""
    wf = values["workflow"]  # type: ignore[arg-type]
    decision = prepare_decision(wf, 6)
    employee = employee_for(decision)
    prepared = prepared_for(wf, decision, employee)
    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            prepared.model,
            prepared.employee_instructions,
            prepared.step_instructions,
            prepared.allowed_tool_names,
            (
                UpstreamStepOutput(
                    wf.id,
                    wf.steps[5].id,
                    6,
                    wf.steps[5].employee,
                    "output",
                ),
            ),
        ),
        WorkflowExecutionState(
            prepared.workflow_id,
            "running",
            prepared.step_id,
            prepared.step_index,
            prepared.employee_id,
            tuple(step.id for step in wf.steps[: prepared.step_index - 1]),
            None,
        ),
    )


def success_transport(
    calls: list[OpenAIResponsesAuthenticatedHttpRequest],
) -> object:
    def transport(
        request_value: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        calls.append(request_value)
        return OpenAIResponsesRawHttpResponse(
            status_code=200,
            reason="synthetic",
            headers=(("x-request-id", "request_123"),),
            body=json.dumps(
                {
                    "id": "resp_123",
                    "object": "response",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "ok"}],
                        }
                    ],
                },
                ensure_ascii=False,
            ).encode(),
        )

    return transport


def failure_transport(
    calls: list[OpenAIResponsesAuthenticatedHttpRequest],
) -> object:
    def transport(
        request_value: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        calls.append(request_value)
        return OpenAIResponsesRawHttpResponse(
            status_code=500,
            reason="synthetic error",
            headers=(("x-request-id", "request_123"),),
            body=json.dumps(
                {
                    "error": {
                        "message": "safe failure",
                        "type": "server_error",
                        "param": None,
                        "code": None,
                    }
                },
                ensure_ascii=False,
            ).encode(),
        )

    return transport


def loaded_events(base: Path) -> list[RuntimeStepEvent]:
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(base / "state", base / "events")
    )
    return list(loaded.events)


def run_phase177_success(
    values: dict[str, object],
    calls: list[OpenAIResponsesAuthenticatedHttpRequest],
) -> StepRuntimeExecutionSuccess:
    """Run real Phase 177 for the step-6 runtime success with None request-id.

    Real Phase 176 commits the step-6 success (accumulating the step-5 None
    predecessor and the step-6 None runtime), real Phase 155 then executes
    step 7 through the synthetic final transport and returns the exact step-7
    runtime result without persisting it.
    """
    wf = values["workflow"]  # type: ignore[arg-type]
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    start = constructed_start(values)
    execution_approval = approve_model_invocation_execution(
        start.request, TOOLS, provider="openai", approved_by="test", approval_id="approval-id"
    )
    out = phase177(
        runtime_success(wf, 6, request_id_none=True),
        wf,
        approval,
        employee,
        state_path,
        events_path,
        TOOLS,
        OpenAIApiKey(value=SecretStr("synthetic")),
        execution_approval,
        success_transport(calls),
    )
    assert type(out) is StepRuntimeExecutionSuccess
    return out


def run_phase177_failure(
    values: dict[str, object],
    calls: list[OpenAIResponsesAuthenticatedHttpRequest],
) -> StepRuntimeExecutionFailure:
    """Run real Phase 177 with the deterministic failure transport."""
    wf = values["workflow"]  # type: ignore[arg-type]
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    start = constructed_start(values)
    execution_approval = approve_model_invocation_execution(
        start.request, TOOLS, provider="openai", approved_by="test", approval_id="approval-id"
    )
    out = phase177(
        runtime_success(wf, 6, request_id_none=True),
        wf,
        approval,
        employee,
        state_path,
        events_path,
        TOOLS,
        OpenAIApiKey(value=SecretStr("synthetic")),
        execution_approval,
        failure_transport(calls),
    )
    assert type(out) is StepRuntimeExecutionFailure
    return out


def test_real_a_eight_step_step7_success_prepares_step8(tmp_path: Path) -> None:
    values = canonical_running_setup(tmp_path / "a", steps=8, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    step7 = run_phase177_success(values, calls)
    assert step7.workflow_id == "w"
    assert step7.step_id == "step-7"
    assert step7.step_index == 7
    assert step7.employee_id == "e7"
    assert len(calls) == 1

    # Exact running snapshot before Phase 172: step 7 running, six committed
    # succeeded events with accumulated step-5 and step-6 None provenance.
    snapshot_state = load_workflow_execution_state(state_path)
    assert snapshot_state.status == "running"
    assert snapshot_state.current_step_id == "step-7"
    assert snapshot_state.current_step_index == 7
    events = loaded_events(tmp_path / "a")
    assert [event.step_id for event in events] == [
        f"step-{i}" for i in range(1, 7)
    ]
    assert events[4].request_id is None
    assert events[4].provider == "openai"
    assert events[5].request_id is None
    assert events[5].provider == "openai"

    decision = phase172(step7, wf, state_path, events_path)
    assert type(decision) is WorkflowProgressionDecision
    assert decision.decision == "prepare_next_step"
    assert decision.workflow_id == "w"
    assert decision.current_step_id == "step-7"
    assert decision.current_step_index == 7
    assert decision.current_employee_id == "e7"
    assert decision.next_step_id == "step-8"
    assert decision.next_step_index == 8
    assert decision.next_employee_id == "e8"
    assert decision.reason == "next_step_available"

    # exact step-7 success persistence through the real defaults
    final_state = load_workflow_execution_state(state_path)
    assert final_state.current_step_id == "step-7"
    assert final_state.current_step_index == 7
    assert final_state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 8))
    final_events = loaded_events(tmp_path / "a")
    assert [event.step_id for event in final_events] == [
        f"step-{i}" for i in range(1, 8)
    ]
    assert final_events[-1].event_type == "step_succeeded"
    assert final_events[-1].step_id == "step-7"
    assert final_events[-1].provider == "openai"
    # Accumulated None provenance preserved byte-exact.
    assert final_events[4].request_id is None
    assert final_events[5].request_id is None
    # No step-8 preparation/start/execution happened.
    assert len(final_events) == 7


def test_real_b_seven_step_step7_success_workflow_complete(tmp_path: Path) -> None:
    values = canonical_running_setup(tmp_path / "b", steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    step7 = run_phase177_success(values, calls)
    assert step7.step_id == "step-7"
    assert step7.step_index == 7
    assert len(calls) == 1

    decision = phase172(step7, wf, state_path, events_path)
    assert type(decision) is WorkflowProgressionDecision
    assert decision.decision == "workflow_complete"
    assert decision.workflow_id == "w"
    assert decision.current_step_id == "step-7"
    assert decision.current_step_index == 7
    assert decision.current_employee_id == "e7"
    assert decision.next_step_id is None
    assert decision.next_step_index is None
    assert decision.next_employee_id is None
    assert decision.reason == "last_step_succeeded"

    final_state = load_workflow_execution_state(state_path)
    assert final_state.status == "succeeded"
    assert final_state.current_step_index == 7
    assert final_state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 8))
    final_events = loaded_events(tmp_path / "b")
    assert [event.step_id for event in final_events] == [
        f"step-{i}" for i in range(1, 8)
    ]
    assert final_events[-1].event_type == "step_succeeded"
    assert final_events[4].request_id is None
    assert final_events[5].request_id is None


def test_real_c_eight_step_step7_failure_persisted_failure(tmp_path: Path) -> None:
    """Exact Issue #377 C accumulated failure now succeeds via real Phase172.

    Step 5 and step 6 both carry ``request_id=None`` (``openai``); step 7
    fails deterministically through the real Phase177 → Phase172 default path.
    The exact built-in default Phase 144 receives the private active-failure
    opt-in, so the accumulated aged-None provenance is accepted and the exact
    ``PersistedExecutionOutcome(persisted_failure)`` is returned.

    An inline subcase then proves direct strictness is retained: calling the
    default Phase 144 directly (no active-failure opt-in) with the same aged
    step-5 None snapshot still rejects as ``terminal_contract``.
    """
    # Main: exact Issue #377 C accumulated failure via real Phase172.
    values = canonical_running_setup(
        tmp_path / "c", steps=8, current=6, five_request_id=None
    )
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    step7 = run_phase177_failure(values, calls)
    assert step7.workflow_id == "w"
    assert step7.step_id == "step-7"
    assert step7.step_index == 7
    assert step7.employee_id == "e7"
    assert step7.invocation_result.category == "api_error"
    assert len(calls) == 1

    outcome = phase172(step7, wf, state_path, events_path)
    assert type(outcome) is PersistedExecutionOutcome
    assert outcome.outcome == "persisted_failure"
    assert outcome.workflow_id == "w"
    assert outcome.current_step_id == "step-7"
    assert outcome.current_step_index == 7
    assert outcome.current_employee_id == "e7"
    assert outcome.failure_category == "api_error"

    final_state = load_workflow_execution_state(state_path)
    assert final_state.status == "failed"
    assert final_state.current_step_id == "step-7"
    assert final_state.current_step_index == 7
    assert final_state.last_failure_category == "api_error"
    final_events = loaded_events(tmp_path / "c")
    assert [event.step_id for event in final_events] == [
        f"step-{i}" for i in range(1, 8)
    ]
    assert final_events[-1].event_type == "step_failed"
    assert final_events[-1].failure_category == "api_error"
    # Accumulated aged-None provenance preserved byte-exact: step 5 and 6.
    assert final_events[4].request_id is None
    assert final_events[4].provider == "openai"
    assert final_events[5].request_id is None
    assert final_events[5].provider == "openai"
    # No retry or further execution: exactly one transport call.
    assert len(calls) == 1

    # Inline subcase: the same aged step-5 None / step-6 None snapshot passed
    # directly to the default Phase 144 (no active-failure opt-in, so the
    # direct/original persisted_failure stop is strict) still rejects with
    # terminal_contract.
    values_direct = canonical_running_setup(
        tmp_path / "c-direct", steps=8, current=6, five_request_id=None
    )
    wf_direct = values_direct["workflow"]  # type: ignore[arg-type]
    state_path_direct = values_direct["state_path"]  # type: ignore[arg-type]
    events_path_direct = values_direct["events_path"]  # type: ignore[arg-type]
    calls_direct: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    step7_direct = run_phase177_failure(values_direct, calls_direct)
    outcome_direct = phase172(
        step7_direct, wf_direct, state_path_direct, events_path_direct
    )
    assert type(outcome_direct) is PersistedExecutionOutcome
    assert outcome_direct.outcome == "persisted_failure"
    assert outcome_direct.failure_category == "api_error"
    with pytest.raises(
        ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as exc:
        route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
            outcome_direct,
            wf_direct,
            state_path_direct,
            events_path_direct,
        )
    assert exc.value.detail.classification == "terminal_contract"


def test_real_d_stop_identity_matching_terminal_snapshots(tmp_path: Path) -> None:
    # Subcase 1: exact workflow_complete with matching succeeded terminal
    # state/history -> exact same object by identity, bytes unchanged.
    values = terminal_setup(tmp_path / "d1", steps=6, fail=False)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    committed_state = state_path.read_bytes()
    committed_events = events_path.read_bytes()
    stop = complete_decision(wf, 6)
    out = phase177(
        stop,
        wf,
        None,
        None,
        state_path,
        events_path,
        None,
        None,
        None,
        None,
    )
    assert out is stop
    assert state_path.read_bytes() == committed_state
    assert events_path.read_bytes() == committed_events

    # Subcase 2: exact persisted_failure with matching failed terminal
    # state/history -> exact same object by identity, bytes unchanged.
    values2 = terminal_setup(tmp_path / "d2", steps=6, fail=True)
    wf2 = values2["workflow"]  # type: ignore[arg-type]
    state_path2 = values2["state_path"]  # type: ignore[arg-type]
    events_path2 = values2["events_path"]  # type: ignore[arg-type]
    committed_state2 = state_path2.read_bytes()
    committed_events2 = events_path2.read_bytes()
    stop2 = failure_outcome(wf2, 6)
    out2 = phase177(
        stop2,
        wf2,
        None,
        None,
        state_path2,
        events_path2,
        None,
        None,
        None,
        None,
    )
    assert out2 is stop2
    assert state_path2.read_bytes() == committed_state2
    assert events_path2.read_bytes() == committed_events2
