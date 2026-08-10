"""Real-default-chain regression for the Phase 140 compatibility repair."""

# ruff: noqa: E501

from pathlib import Path

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PreparedStepExecutionStart,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "default-chain-workflow",
            "name": "Default chain workflow",
            "description": "real default chain regression",
            "steps": [
                {"id": "one", "name": "One", "employee": "one", "instructions": "one"},
                {"id": "two", "name": "Two", "employee": "two", "instructions": "two"},
                {"id": "three", "name": "Three", "employee": "three", "instructions": "three"},
                {"id": "four", "name": "Four", "employee": "four", "instructions": "four"},
                {"id": "five", "name": "Five", "employee": "five", "instructions": "five"},
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "five",
            "name": "Five",
            "role": "synthetic",
            "instructions": "employee instructions",
            "model": "synthetic-model",
            "allowed_tools": ["synthetic-tool"],
        }
    )


def start(definition: WorkflowDefinition) -> PreparedStepExecutionStart:
    person = employee()
    step = definition.steps[4]
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
            5,
            person.id,
            tuple(item.id for item in definition.steps[:4]),
            None,
        ),
    )


def predecessor_targets(
    definition: WorkflowDefinition,
    tmp_path: Path,
) -> tuple[Path, Path, bytes]:
    predecessor = WorkflowExecutionState(
        definition.id,
        "succeeded",
        "four",
        4,
        "four",
        ("one", "two", "three", "four"),
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
            "other" if index < 3 else "openai",
            None,
            f"response-{index}",
            f"request-{index}",
            "output",
            None,
        )
        for index, step in enumerate(definition.steps[:3], 1)
    ]
    events.append(
        RuntimeStepEvent(
            "step_succeeded",
            definition.id,
            "four",
            4,
            "four",
            "running",
            "succeeded",
            "openai",
            None,
            "response-four",
            "request-four",
            "",
            None,
        )
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


def test_phase139_default_dependency_chain_accepts_nonfinal_empty_success(
    tmp_path: Path,
) -> None:
    definition = workflow()
    supplied_start = start(definition)
    supplied_employee = employee()
    state_path, events_path, before_events = predecessor_targets(definition, tmp_path)

    result = route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        supplied_start,
        definition,
        supplied_employee,
        state_path,
        events_path,
    )

    expected_state = serialize_workflow_execution_state_json(
        supplied_start.running_state
    ).encode("utf-8")
    assert type(result) is RunningStatePersistenceResult
    assert result.state_bytes_written == len(expected_state)
    assert state_path.read_bytes() == expected_state
    assert events_path.read_bytes() == before_events
