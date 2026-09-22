"""Behavioral tests for the Phase 146 prepared-step-start facade."""

# ruff: noqa: E501

from __future__ import annotations

import inspect
import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError,
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class PreparedChild(PreparedWorkflowStep):
    pass


class WorkflowChild(WorkflowDefinition):
    pass


def workflow(count: int = 8) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "prepared-step-start test workflow",
            "steps": [
                {
                    "id": f"step-{index}",
                    "name": f"Step {index}",
                    "employee": f"employee-{index}",
                    "instructions": f"instructions-{index}",
                }
                for index in range(1, count + 1)
            ],
        }
    )


def employee_for(definition: WorkflowDefinition, index: int) -> EmployeeDefinition:
    step = definition.steps[index - 1]
    return EmployeeDefinition.model_validate(
        {
            "id": step.employee,
            "name": step.name,
            "role": "test role",
            "instructions": f"employee instructions-{index}",
            "model": "test-model",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )


def prepared_for(definition: WorkflowDefinition, index: int) -> PreparedWorkflowStep:
    step = definition.steps[index - 1]
    employee = employee_for(definition, index)
    return PreparedWorkflowStep(
        definition.id,
        step.id,
        index,
        employee.id,
        employee.instructions,
        step.instructions,
        employee.model,
        tuple(employee.allowed_tools),
    )


def success_event(
    definition: WorkflowDefinition,
    index: int,
    *,
    provider: object = "openai",
    request_id: object = "request-id",
    output_text: object = "persisted output",
) -> RuntimeStepEvent:
    step = definition.steps[index - 1]
    return RuntimeStepEvent(
        "step_succeeded",
        definition.id,
        step.id,
        index,
        step.employee,
        "running",
        "succeeded",
        provider,
        None,
        f"response-{index}",
        request_id,
        output_text,
        None,
    )


def failure_event(definition: WorkflowDefinition, index: int) -> RuntimeStepEvent:
    step = definition.steps[index - 1]
    return RuntimeStepEvent(
        "step_failed",
        definition.id,
        step.id,
        index,
        step.employee,
        "running",
        "failed",
        "openai",
        "api_error",
        None,
        None,
        None,
        "safe failure",
    )


def write_history(
    directory: Path,
    definition: WorkflowDefinition,
    *,
    current: int,
    status: str = "succeeded",
    predecessor_changes: dict[int, dict[str, object]] | None = None,
) -> tuple[Path, Path, bytes, bytes]:
    predecessor_changes = predecessor_changes or {}
    current_step = definition.steps[current - 1]
    completed = (
        tuple(step.id for step in definition.steps[:current])
        if status == "succeeded"
        else tuple(step.id for step in definition.steps[: current - 1])
    )
    state = WorkflowExecutionState(
        definition.id,
        status,
        current_step.id,
        current,
        current_step.employee,
        completed,
        None if status == "succeeded" else "api_error",
    )
    events = [
        replace(
            success_event(definition, index),
            **predecessor_changes.get(index, {}),
        )
        for index in range(1, current)
    ]
    events.append(
        failure_event(definition, current)
        if status == "failed"
        else success_event(definition, current)
    )
    directory.mkdir(parents=True, exist_ok=True)
    state_path, events_path = directory / "state.json", directory / "events.jsonl"
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8") for event in events
    )
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes


def rewrite_state(path: Path, **changes: object) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    value.update(changes)
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")


def rewrite_event(path: Path, index: int, **changes: object) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    value = json.loads(lines[index])
    value.update(changes)
    lines[index] = json.dumps(value, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def completion(definition: WorkflowDefinition) -> WorkflowProgressionDecision:
    step = definition.steps[-1]
    return WorkflowProgressionDecision(
        "workflow_complete",
        definition.id,
        step.id,
        len(definition.steps),
        step.employee,
        None,
        None,
        None,
        "last_step_succeeded",
    )


def failure(definition: WorkflowDefinition, index: int) -> PersistedExecutionOutcome:
    step = definition.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_failure",
        definition.id,
        step.id,
        index,
        step.employee,
        "api_error",
    )


def assert_classification(callable_object: object, expected: str) -> None:
    with pytest.raises(
        PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError
    ) as caught:
        callable_object()  # type: ignore[operator]
    assert caught.value.detail.classification == expected


def prepare_route(
    definition: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    *,
    current: int,
) -> PreparedStepExecutionStart:
    value = route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
        prepared_for(definition, current + 1),
        definition,
        employee_for(definition, current + 1),
        state_path,
        events_path,
    )
    assert type(value) is PreparedStepExecutionStart
    return value


def test_public_facade_signature_has_no_historical_injection_seam() -> None:
    parameters = tuple(
        inspect.signature(
            route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
        ).parameters.values()
    )
    assert tuple(parameter.name for parameter in parameters) == (
        "result",
        "workflow",
        "employee",
        "state_path",
        "events_path",
    )
    assert all(parameter.annotation is object for parameter in parameters)


def test_valid_prepare_reconstructs_exact_upstream_and_runtime_provenance(
    tmp_path: Path,
) -> None:
    definition = workflow()
    state_path, events_path, before_state, before_events = write_history(
        tmp_path,
        definition,
        current=6,
        predecessor_changes=None,
    )
    rewrite_event(events_path, 5, output_text="line 1\nユニコード\nline 3")
    before_state, before_events = state_path.read_bytes(), events_path.read_bytes()
    prepared = prepared_for(definition, 7)
    state_digest = sha256(state_path.read_bytes()).hexdigest()
    predecessor = success_event(definition, 6, output_text="line 1\nユニコード\nline 3")
    actual = route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
        prepared,
        definition,
        employee_for(definition, 7),
        state_path,
        events_path,
    )

    assert type(actual) is PreparedStepExecutionStart
    assert actual.request.model == prepared.model
    assert actual.request.system_instructions == prepared.employee_instructions
    assert actual.request.task_instructions == prepared.step_instructions
    assert actual.request.allowed_tools == prepared.allowed_tool_names
    assert actual.request.upstream_inputs[0].output_text == "line 1\nユニコード\nline 3"
    assert actual.running_state == WorkflowExecutionState(
        definition.id,
        "running",
        "step-7",
        7,
        "employee-7",
        tuple(f"step-{index}" for index in range(1, 7)),
        None,
    )
    facts = {fact.key: fact for fact in actual.request.runtime_facts.facts}
    assert facts["workflow.status"].provenance.source_sha256 == state_digest
    assert (
        facts["predecessor.step_id"].provenance.source_sha256
        == sha256(
            serialize_runtime_step_event_jsonl(predecessor).encode("utf-8")
        ).hexdigest()
    )
    assert (state_path.read_bytes(), events_path.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize(
    ("changed", "classification"),
    [
        (lambda value: object(), "result_type"),
        (lambda value: replace(value, step_index=1), "prepared_step_contract"),
        (lambda value: replace(value, workflow_id="other"), "prepared_step_contract"),
    ],
)
def test_stale_or_unsupported_prepare_inputs_fail_closed(
    tmp_path: Path,
    changed: object,
    classification: str,
) -> None:
    definition = workflow()
    state_path, events_path, before_state, before_events = write_history(
        tmp_path, definition, current=6
    )
    value = changed(prepared_for(definition, 7))  # type: ignore[operator]
    assert_classification(
        lambda: (
            route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
                value,
                definition,
                employee_for(definition, 7),
                state_path,
                events_path,
            )
        ),
        classification,
    )
    assert (state_path.read_bytes(), events_path.read_bytes()) == (
        before_state,
        before_events,
    )


def test_exact_workflow_and_employee_contracts_are_enforced(tmp_path: Path) -> None:
    definition = workflow()
    state_path, events_path, before_state, before_events = write_history(
        tmp_path, definition, current=6
    )
    prepared = prepared_for(definition, 7)
    assert_classification(
        lambda: (
            route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
                prepared,
                WorkflowChild.model_validate(definition.model_dump()),
                employee_for(definition, 7),
                state_path,
                events_path,
            )
        ),
        "workflow_definition",
    )
    assert_classification(
        lambda: (
            route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
                prepared,
                definition,
                employee_for(definition, 6),
                state_path,
                events_path,
            )
        ),
        "employee_contract",
    )
    assert (state_path.read_bytes(), events_path.read_bytes()) == (
        before_state,
        before_events,
    )


def _assert_history_corruption_fails_closed_without_writes(
    tmp_path: Path, mutation: str
) -> None:
    definition = workflow()
    state_path, events_path, *_ = write_history(tmp_path, definition, current=6)
    if mutation == "state-prefix":
        rewrite_state(state_path, completed_step_ids=["step-1", "step-3"])
    elif mutation == "event-order":
        lines = events_path.read_text(encoding="utf-8").splitlines()
        lines[0], lines[1] = lines[1], lines[0]
        events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif mutation == "provider":
        rewrite_event(events_path, 4, provider="anthropic")
    elif mutation == "request-id":
        rewrite_event(events_path, 4, request_id="")
    else:
        rewrite_event(events_path, 0, output_text=None)
    before_state, before_events = state_path.read_bytes(), events_path.read_bytes()

    assert_classification(
        lambda: prepare_route(definition, state_path, events_path, current=6),
        "terminal_contract",
    )
    assert (state_path.read_bytes(), events_path.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize(
    "mutation", ["state-prefix", "event-order", "provider", "request-id", "output-type"]
)
def test_history_corruption_matrix_is_fail_closed(
    tmp_path: Path, mutation: str
) -> None:
    _assert_history_corruption_fails_closed_without_writes(tmp_path, mutation)


@pytest.mark.parametrize("provider", ["openai", "omniroute"])
def test_current_predecessor_provider_acceptance_is_preserved(
    tmp_path: Path, provider: str
) -> None:
    definition = workflow()
    state_path, events_path, *_ = write_history(
        tmp_path,
        definition,
        current=6,
        predecessor_changes={5: {"provider": provider}},
    )
    result = prepare_route(definition, state_path, events_path, current=6)
    assert result.request.runtime_facts.facts


def test_earlier_non_openai_provider_remains_accepted(tmp_path: Path) -> None:
    definition = workflow()
    state_path, events_path, *_ = write_history(
        tmp_path,
        definition,
        current=6,
        predecessor_changes={1: {"provider": "anthropic"}},
    )
    assert (
        prepare_route(
            definition, state_path, events_path, current=6
        ).running_state.current_step_index
        == 7
    )


def test_current_predecessor_non_openai_provider_is_rejected(tmp_path: Path) -> None:
    definition = workflow()
    state_path, events_path, *_ = write_history(
        tmp_path,
        definition,
        current=6,
        predecessor_changes={5: {"provider": "anthropic"}},
    )
    assert_classification(
        lambda: prepare_route(definition, state_path, events_path, current=6),
        "terminal_contract",
    )


@pytest.mark.parametrize("current", [5, 6, 7])
def test_empty_historical_predecessor_threshold_boundary(
    tmp_path: Path, current: int
) -> None:
    definition = workflow()
    changes = {index: {"output_text": ""} for index in range(1, current)}
    state_path, events_path, *_ = write_history(
        tmp_path, definition, current=current, predecessor_changes=changes
    )
    if current < 6:
        assert_classification(
            lambda: prepare_route(definition, state_path, events_path, current=current),
            "terminal_contract",
        )
    else:
        assert (
            prepare_route(
                definition, state_path, events_path, current=current
            ).running_state.current_step_index
            == current + 1
        )


def test_immediate_and_accumulated_none_request_id_compatibility_is_bounded(
    tmp_path: Path,
) -> None:
    definition = workflow()
    state_path, events_path, *_ = write_history(
        tmp_path / "immediate",
        definition,
        current=6,
        predecessor_changes={5: {"request_id": None}},
    )
    assert (
        prepare_route(
            definition, state_path, events_path, current=6
        ).running_state.current_step_index
        == 7
    )

    state_path, events_path, *_ = write_history(
        tmp_path / "immediate-below",
        definition,
        current=6,
        predecessor_changes={4: {"request_id": None}},
    )
    assert_classification(
        lambda: prepare_route(definition, state_path, events_path, current=6),
        "terminal_contract",
    )

    state_path, events_path, *_ = write_history(
        tmp_path / "accumulated",
        definition,
        current=7,
        predecessor_changes={5: {"request_id": None, "provider": "openai"}},
    )
    assert (
        prepare_route(
            definition, state_path, events_path, current=7
        ).running_state.current_step_index
        == 8
    )

    state_path, events_path, *_ = write_history(
        tmp_path / "accumulated-below",
        definition,
        current=7,
        predecessor_changes={4: {"request_id": None, "provider": "openai"}},
    )
    assert_classification(
        lambda: prepare_route(definition, state_path, events_path, current=7),
        "terminal_contract",
    )

    state_path, events_path, *_ = write_history(
        tmp_path / "accumulated-provider",
        definition,
        current=7,
        predecessor_changes={5: {"request_id": None, "provider": "anthropic"}},
    )
    assert_classification(
        lambda: prepare_route(definition, state_path, events_path, current=7),
        "terminal_contract",
    )


@pytest.mark.parametrize("bad", ["", 4])
def test_empty_or_wrong_type_request_ids_remain_rejected(
    tmp_path: Path, bad: object
) -> None:
    definition = workflow()
    state_path, events_path, *_ = write_history(
        tmp_path,
        definition,
        current=6,
        predecessor_changes={5: {"request_id": bad}},
    )
    assert_classification(
        lambda: prepare_route(definition, state_path, events_path, current=6),
        "terminal_contract",
    )


def test_stop_routes_are_identity_preserving_and_read_only(tmp_path: Path) -> None:
    definition = workflow(6)
    success_state, success_events, success_before_state, success_before_events = (
        write_history(tmp_path / "success", definition, current=6)
    )
    failure_state, failure_events, failure_before_state, failure_before_events = (
        write_history(tmp_path / "failure", definition, current=4, status="failed")
    )
    success = completion(definition)
    failed = failure(definition, 4)
    assert (
        route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
            success, definition, None, success_state, success_events
        )
        is success
    )
    assert (
        route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
            failed, definition, None, failure_state, failure_events
        )
        is failed
    )
    assert (success_state.read_bytes(), success_events.read_bytes()) == (
        success_before_state,
        success_before_events,
    )
    assert (failure_state.read_bytes(), failure_events.read_bytes()) == (
        failure_before_state,
        failure_before_events,
    )


def test_stop_compatibility_and_terminal_strictness_are_preserved(
    tmp_path: Path,
) -> None:
    definition = workflow(6)
    state_path, events_path, *_ = write_history(
        tmp_path / "empty-history",
        definition,
        current=6,
        predecessor_changes={
            1: {"output_text": ""},
            5: {"request_id": None, "output_text": ""},
        },
    )
    result = completion(definition)
    assert (
        route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
            result, definition, None, state_path, events_path
        )
        is result
    )

    state_path, events_path, *_ = write_history(
        tmp_path / "earlier-none",
        definition,
        current=6,
        predecessor_changes={1: {"request_id": None}},
    )
    assert_classification(
        lambda: (
            route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
                result, definition, None, state_path, events_path
            )
        ),
        "terminal_contract",
    )

    state_path, events_path, *_ = write_history(
        tmp_path / "empty-terminal",
        definition,
        current=6,
        predecessor_changes=None,
    )
    rewrite_event(events_path, 5, output_text="")
    assert_classification(
        lambda: (
            route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
                result, definition, None, state_path, events_path
            )
        ),
        "terminal_contract",
    )


def test_terminal_result_contracts_and_targets_fail_closed(tmp_path: Path) -> None:
    definition = workflow(6)
    state_path, events_path, before_state, before_events = write_history(
        tmp_path, definition, current=6
    )
    prepared = (
        prepared_for(definition, 7)
        if len(definition.steps) >= 7
        else prepared_for(workflow(), 7)
    )
    assert_classification(
        lambda: (
            route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
                completion(definition),
                definition,
                employee_for(definition, 1),
                state_path,
                events_path,
            )
        ),
        "completion_contract",
    )
    assert_classification(
        lambda: (
            route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
                completion(definition), definition, None, state_path, state_path
            )
        ),
        "target_conflict",
    )
    assert (state_path.read_bytes(), events_path.read_bytes()) == (
        before_state,
        before_events,
    )
    assert isinstance(prepared, PreparedWorkflowStep)
