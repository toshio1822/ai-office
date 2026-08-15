"""Real Phase173 -> Phase146 canonical Phase-155 stop-route compatibility.

Issue #365: the Phase 146 ``workflow_complete`` / ``persisted_failure`` stop
routes must accept the canonical Phase-155 provenance (immediate predecessor
step-5 ``request_id=None`` with ``output_text == ""``) that real Phase 173
durably commits, and return the exact Phase-173 decision/outcome object by
identity without entering Phase 138.

Requirement-to-test mapping (Issue #365):
- six-step step-6 success: exact StepRuntimeExecutionSuccess -> Phase 173
  durable commit -> exact WorkflowProgressionDecision(workflow_complete) ->
  real Phase 146 accepts canonical history and returns the exact Phase-173
  decision by identity; committed bytes unchanged; persisted step-5
  ``request_id is None`` retained
  -> test_real_phase173_phase146_six_step_success_canonical_provenance
- six-step step-6 failure: exact StepRuntimeExecutionFailure -> Phase 173
  durable commit -> exact PersistedExecutionOutcome(persisted_failure) ->
  real Phase 146 accepts canonical history and returns the exact Phase-173
  outcome by identity; committed bytes unchanged; persisted step-5
  ``request_id is None`` retained
  -> test_real_phase173_phase146_six_step_failure_canonical_provenance
"""

# ruff: noqa: E501,E701,E702,F401,I001

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_runtime_result_to_approved_preparation_orchestration_boundary,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase146Error,
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase146_route,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

phase173 = route_runtime_result_to_approved_preparation_orchestration_boundary
phase146 = phase146_route


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


def predecessor_event(
    step_id: str, index: int, **changes: object
) -> RuntimeStepEvent:
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


def setup(base: Path, steps: int, current: int) -> dict[str, object]:
    """Canonical Phase-155 provenance at ``current``.

    Immediate predecessor (step ``current - 1``) keeps provider="openai",
    ``output_text == ""`` and ``request_id is None``; indexes 2-4 carry
    compatible empty outputs; all other earlier request IDs are exact
    non-empty built-in strings.
    """
    base.mkdir(parents=True, exist_ok=True)
    state_path, events_path = base / "state", base / "events"
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
    state_path.write_text(
        serialize_workflow_execution_state_json(state), encoding="utf-8"
    )
    events = []
    for index in range(1, current):
        if index == current - 1:
            events.append(
                predecessor_event(
                    wf.steps[index - 1].id, index, output_text="", request_id=None
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
    }


def runtime_success(wf: WorkflowDefinition, index: int) -> StepRuntimeExecutionSuccess:
    step = wf.steps[index - 1]
    return StepRuntimeExecutionSuccess(
        "w",
        step.id,
        index,
        step.employee,
        ModelInvocationSuccess(
            "openai",
            f"response-{step.id}",
            f"request-{step.id}",
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


def assert_step5_canonical_retained(values: dict[str, object]) -> None:
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]  # type: ignore[arg-type]
        )
    )
    step5 = history.events[4]
    assert step5.step_id == "step-5"
    assert step5.provider == "openai"
    assert step5.output_text == ""
    assert step5.request_id is None
    assert all(
        event.request_id is None or bool(event.request_id) for event in history.events
    )


def test_real_phase173_phase146_six_step_success_canonical_provenance(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=6, current=6)
    wf = values["workflow"]
    result = runtime_success(wf, 6)
    decision = phase173(
        result, wf, None, None, values["state_path"], values["events_path"]
    )
    assert type(decision) is WorkflowProgressionDecision
    assert decision.decision == "workflow_complete"
    committed = (
        values["state_path"].read_bytes(), values["events_path"].read_bytes()
    )
    returned = phase146(
        decision, wf, None, values["state_path"], values["events_path"]
    )
    assert returned is decision
    assert (
        values["state_path"].read_bytes(), values["events_path"].read_bytes()
    ) == committed
    assert_step5_canonical_retained(values)


def test_real_phase173_phase146_six_step_failure_canonical_provenance(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=6, current=6)
    wf = values["workflow"]
    result = runtime_failure(wf, 6)
    outcome = phase173(
        result, wf, None, None, values["state_path"], values["events_path"]
    )
    assert type(outcome) is PersistedExecutionOutcome
    assert outcome.outcome == "persisted_failure"
    committed = (
        values["state_path"].read_bytes(), values["events_path"].read_bytes()
    )
    returned = phase146(
        outcome, wf, None, values["state_path"], values["events_path"]
    )
    assert returned is outcome
    assert (
        values["state_path"].read_bytes(), values["events_path"].read_bytes()
    ) == committed
    assert_step5_canonical_retained(values)
