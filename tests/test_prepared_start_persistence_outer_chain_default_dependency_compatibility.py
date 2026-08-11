"""Real-default-chain regression for the Phase 148 compatibility repair."""

# ruff: noqa: E501

from pathlib import Path

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PreparedStepExecutionStart,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "phase148-default-chain-workflow",
            "name": "Phase 148 default chain workflow",
            "description": "real Phase 147 default dependency chain regression",
            "steps": [
                {"id": "one", "name": "One", "employee": "one", "instructions": "one"},
                {"id": "two", "name": "Two", "employee": "two", "instructions": "two"},
                {"id": "three", "name": "Three", "employee": "three", "instructions": "three"},
                {"id": "four", "name": "Four", "employee": "four", "instructions": "four"},
                {"id": "five", "name": "Five", "employee": "five", "instructions": "five"},
                {"id": "six", "name": "Six", "employee": "six", "instructions": "six"},
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "six",
            "name": "Six",
            "role": "synthetic",
            "instructions": "employee instructions",
            "model": "synthetic-model",
            "allowed_tools": ["synthetic-tool"],
        }
    )


def start(definition: WorkflowDefinition) -> PreparedStepExecutionStart:
    person = employee()
    step = definition.steps[5]
    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            person.model,
            person.instructions,
            step.instructions,
            tuple(person.allowed_tools),
        ),
        WorkflowExecutionState(
            definition.id,
            "running",
            step.id,
            6,
            person.id,
            tuple(item.id for item in definition.steps[:5]),
            None,
        ),
    )


def predecessor_targets(
    definition: WorkflowDefinition,
    tmp_path: Path,
    *,
    empty_steps: tuple[int, ...] = (2, 4),
    terminal_output: str = "",
    terminal_request_id: object = None,
) -> tuple[Path, Path, bytes]:
    """Exact non-final succeeded predecessor state for step 5 with history for steps 1-5.

    Step 4 (immediate predecessor) and step 5 (terminal) use provider "openai" per the
    exact Phase 146/147 provenance contract; earlier steps use a non-openai provider to
    prove provider semantics are unchanged. No provider/tool/network/paid API/credential/
    transport execution occurs anywhere in this module.
    """
    predecessor = WorkflowExecutionState(
        definition.id,
        "succeeded",
        "five",
        5,
        "five",
        ("one", "two", "three", "four", "five"),
        None,
    )
    events = [
        RuntimeStepEvent(
            "step_succeeded",
            definition.id,
            step.id,
            index,
            step.employee,
            "running",
            "succeeded",
            "openai" if index in (4, 5) else "other",
            None,
            f"response-{index}",
            f"request-{index}",
            "" if index in empty_steps else f"output-{index}",
            None,
        )
        for index, step in enumerate(definition.steps[:5], 1)
    ]
    events[-1] = RuntimeStepEvent(
        "step_succeeded",
        definition.id,
        "five",
        5,
        "five",
        "running",
        "succeeded",
        "openai",
        None,
        "response-5",
        terminal_request_id,
        terminal_output,
        None,
    )
    state_bytes = serialize_workflow_execution_state_json(predecessor).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8") for event in events
    )
    state_path = tmp_path / "state.json"
    events_path = tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, event_bytes


def _assert_exact_default_chain_result(
    result: object,
    supplied_start: PreparedStepExecutionStart,
    state_path: Path,
    events_path: Path,
    before_events: bytes,
) -> None:
    expected_state = serialize_workflow_execution_state_json(
        supplied_start.running_state
    ).encode("utf-8")
    assert type(result) is RunningStatePersistenceResult
    assert result.state_bytes_written == len(expected_state)
    assert state_path.read_bytes() == expected_state
    assert load_workflow_execution_state(state_path) == supplied_start.running_state
    assert events_path.read_bytes() == before_events


def test_phase147_real_default_chain_accepts_earlier_empty_predecessor_success(
    tmp_path: Path,
) -> None:
    definition = workflow()
    supplied_start = start(definition)
    supplied_employee = employee()
    state_path, events_path, before_events = predecessor_targets(definition, tmp_path)

    result = route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
        supplied_start,
        definition,
        supplied_employee,
        state_path,
        events_path,
    )

    _assert_exact_default_chain_result(
        result, supplied_start, state_path, events_path, before_events
    )


def test_phase147_real_default_chain_accepts_older_empty_with_later_nonempty_successes(
    tmp_path: Path,
) -> None:
    definition = workflow()
    supplied_start = start(definition)
    supplied_employee = employee()
    state_path, events_path, before_events = predecessor_targets(
        definition,
        tmp_path,
        empty_steps=(2,),
        terminal_output="output-5",
        terminal_request_id="request-5",
    )

    result = route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
        supplied_start,
        definition,
        supplied_employee,
        state_path,
        events_path,
    )

    _assert_exact_default_chain_result(
        result, supplied_start, state_path, events_path, before_events
    )
