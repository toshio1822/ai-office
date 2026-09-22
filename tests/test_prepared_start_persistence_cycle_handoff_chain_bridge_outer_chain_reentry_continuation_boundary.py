"""Behavioral tests for the Phase 147 prepared-start persistence facade."""

# ruff: noqa: E501

from __future__ import annotations

import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

import ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase147_module
from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as public_route,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    RunningStatePersistenceRollbackError,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_ERROR = (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError
)


def workflow(count: int = 8) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "prepared-start persistence behavior",
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


def start_for(definition: WorkflowDefinition, index: int) -> PreparedStepExecutionStart:
    employee = employee_for(definition, index)
    step = definition.steps[index - 1]
    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            employee.model,
            employee.instructions,
            step.instructions,
            tuple(employee.allowed_tools),
        ),
        WorkflowExecutionState(
            definition.id,
            "running",
            step.id,
            index,
            step.employee,
            tuple(item.id for item in definition.steps[: index - 1]),
            None,
        ),
    )


def success_event(
    definition: WorkflowDefinition,
    index: int,
    *,
    provider: object = "openai",
    request_id: object = "request",
    output_text: object = "output",
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
        f"request-{index}",
        None,
        "safe failure",
    )


def write_history(
    tmp_path: Path,
    definition: WorkflowDefinition,
    *,
    current: int,
    status: str = "succeeded",
    empty_positions: tuple[int, ...] = (),
    none_request_positions: tuple[int, ...] = (),
    provider_overrides: dict[int, object] | None = None,
    request_overrides: dict[int, object] | None = None,
    event_overrides: dict[int, dict[str, object]] | None = None,
) -> tuple[Path, Path, bytes, bytes]:
    provider_overrides = provider_overrides or {}
    request_overrides = request_overrides or {}
    event_overrides = event_overrides or {}
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
    events: list[RuntimeStepEvent] = []
    for index in range(1, current + 1):
        if status == "failed" and index == current:
            event = failure_event(definition, index)
        else:
            provider = provider_overrides.get(
                index,
                "openai" if index in (current - 1, current) else "other",
            )
            request_id = request_overrides.get(
                index, None if index in none_request_positions else f"request-{index}"
            )
            output = "" if index in empty_positions else f"output-{index}"
            event = success_event(
                definition,
                index,
                provider=provider,
                request_id=request_id,
                output_text=output,
            )
        if index in event_overrides:
            event = replace(event, **event_overrides[index])
        events.append(event)

    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8") for event in events
    )
    state_path = tmp_path / "state.json"
    events_path = tmp_path / "events.jsonl"
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes


def prepared_case(
    tmp_path: Path,
    *,
    index: int = 6,
    steps: int = 8,
    empty_positions: tuple[int, ...] = (),
    none_request_positions: tuple[int, ...] = (),
    provider_overrides: dict[int, object] | None = None,
    event_overrides: dict[int, dict[str, object]] | None = None,
) -> dict[str, object]:
    definition = workflow(steps)
    state_path, events_path, state_bytes, event_bytes = write_history(
        tmp_path,
        definition,
        current=index - 1,
        empty_positions=empty_positions,
        none_request_positions=none_request_positions,
        provider_overrides=provider_overrides,
        event_overrides=event_overrides,
    )
    return {
        "workflow": definition,
        "employee": employee_for(definition, index),
        "start": start_for(definition, index),
        "state": state_path,
        "events": events_path,
        "before": (state_bytes, event_bytes),
    }


def stop_case(
    tmp_path: Path,
    *,
    status: str,
    index: int = 6,
    terminal_provider: object = "other",
    empty_positions: tuple[int, ...] = (),
) -> dict[str, object]:
    definition = workflow(index)
    state, events, state_bytes, event_bytes = write_history(
        tmp_path,
        definition,
        current=index,
        status=status,
        empty_positions=empty_positions,
        provider_overrides={index: terminal_provider},
    )
    step = definition.steps[index - 1]
    if status == "succeeded":
        result: object = WorkflowProgressionDecision(
            "workflow_complete",
            definition.id,
            step.id,
            index,
            step.employee,
            None,
            None,
            None,
            "last_step_succeeded",
        )
    else:
        result = PersistedExecutionOutcome(
            "persisted_failure",
            definition.id,
            step.id,
            index,
            step.employee,
            "api_error",
        )
    return {
        "workflow": definition,
        "result": result,
        "state": state,
        "events": events,
        "before": (state_bytes, event_bytes),
    }


def assert_classification(callable_object: object, expected: str) -> None:
    with pytest.raises(_ERROR) as caught:
        callable_object()  # type: ignore[operator]
    assert caught.value.detail.classification == expected


def route_case(case: dict[str, object]) -> object:
    return public_route(
        case["start"],
        case["workflow"],
        case["employee"],
        case["state"],
        case["events"],
    )


def test_public_facade_has_no_historical_injection_seam() -> None:
    parameters = tuple(inspect.signature(public_route).parameters.values())
    assert tuple(parameter.name for parameter in parameters) == (
        "result",
        "workflow",
        "employee",
        "state_path",
        "events_path",
    )
    assert all(parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in parameters)


def test_valid_prepared_start_persists_exact_state_and_preserves_events(
    tmp_path: Path,
) -> None:
    case = prepared_case(tmp_path)
    start = case["start"]
    state = case["state"]
    events = case["events"]
    assert isinstance(start, PreparedStepExecutionStart)
    assert isinstance(state, Path) and isinstance(events, Path)
    before_events = events.read_bytes()

    result = route_case(case)

    expected = serialize_workflow_execution_state_json(start.running_state).encode("utf-8")
    assert type(result) is RunningStatePersistenceResult
    assert result.state_bytes_written == len(expected)
    assert state.read_bytes() == expected
    assert load_workflow_execution_state(state) == start.running_state
    assert events.read_bytes() == before_events
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(state, events)
    )
    assert history.state == start.running_state
    assert len(history.events) == start.running_state.current_step_index - 1


@pytest.mark.parametrize(
    ("empty_positions", "index"),
    [((1, 3, 5), 6), ((2, 4, 5), 7)],
)
def test_empty_predecessor_outputs_remain_accepted(
    tmp_path: Path, empty_positions: tuple[int, ...], index: int
) -> None:
    case = prepared_case(
        tmp_path,
        index=index,
        empty_positions=empty_positions,
    )
    before_events = case["events"].read_bytes()
    result = route_case(case)
    assert type(result) is RunningStatePersistenceResult
    assert case["events"].read_bytes() == before_events


def test_provider_compatibility_preserves_immediate_and_earlier_rules(
    tmp_path: Path,
) -> None:
    accepted = prepared_case(
        tmp_path / "accepted",
        index=7,
        provider_overrides={4: "other", 5: "omniroute"},
    )
    assert type(route_case(accepted)) is RunningStatePersistenceResult

    rejected = prepared_case(
        tmp_path / "rejected",
        index=7,
        provider_overrides={6: "other"},
    )
    before = rejected["before"]
    assert_classification(lambda: route_case(rejected), "terminal_contract")
    assert (rejected["state"].read_bytes(), rejected["events"].read_bytes()) == before


def test_request_id_none_compatibility_is_bounded(
    tmp_path: Path,
) -> None:
    immediate = prepared_case(
        tmp_path / "immediate",
        index=7,
        none_request_positions=(6,),
    )
    assert type(route_case(immediate)) is RunningStatePersistenceResult

    aged = prepared_case(
        tmp_path / "aged",
        index=8,
        none_request_positions=(5,),
        provider_overrides={5: "openai"},
    )
    assert type(route_case(aged)) is RunningStatePersistenceResult

    below_threshold = prepared_case(
        tmp_path / "below-threshold",
        index=6,
        none_request_positions=(4,),
        provider_overrides={4: "openai"},
    )
    assert_classification(lambda: route_case(below_threshold), "terminal_contract")

    early = prepared_case(
        tmp_path / "early",
        index=8,
        none_request_positions=(4,),
    )
    assert_classification(lambda: route_case(early), "terminal_contract")

    non_openai = prepared_case(
        tmp_path / "non-openai",
        index=8,
        none_request_positions=(5,),
        provider_overrides={5: "other"},
    )
    assert_classification(lambda: route_case(non_openai), "terminal_contract")


def test_invalid_start_and_employee_fail_before_persistence(tmp_path: Path) -> None:
    case = prepared_case(tmp_path / "start")
    original = case["before"]
    start = case["start"]
    assert isinstance(start, PreparedStepExecutionStart)
    case["start"] = replace(
        start,
        request=replace(start.request, model="wrong-model"),
    )
    assert_classification(lambda: route_case(case), "start_contract")
    assert (case["state"].read_bytes(), case["events"].read_bytes()) == original

    employee_case = prepared_case(tmp_path / "employee")
    original_employee = employee_case["before"]
    definition = employee_case["workflow"]
    assert isinstance(definition, WorkflowDefinition)
    employee_case["employee"] = employee_for(
        definition, employee_case["start"].running_state.current_step_index - 1
    )
    assert_classification(lambda: route_case(employee_case), "employee_contract")
    assert (
        employee_case["state"].read_bytes(),
        employee_case["events"].read_bytes(),
    ) == original_employee


def test_corrupt_or_stale_persisted_history_fails_closed_before_write(
    tmp_path: Path,
) -> None:
    mutations = [
        ("state-identity", lambda path: rewrite_json(path, workflow_id="stale")),
        ("event-linkage", lambda path: rewrite_event_json(path, 2, step_id="stale")),
        ("malformed", lambda path: path.write_bytes(b"not-json")),
    ]
    for label, mutate in mutations:
        case = prepared_case(tmp_path / label)
        mutate(case["state"] if label != "event-linkage" else case["events"])
        before = (case["state"].read_bytes(), case["events"].read_bytes())
        assert_classification(lambda case=case: route_case(case), "terminal_contract")
        assert (case["state"].read_bytes(), case["events"].read_bytes()) == before


def test_targets_are_validated_before_history_or_persistence(tmp_path: Path) -> None:
    case = prepared_case(tmp_path / "targets")
    case["state"].unlink()
    assert_classification(lambda: route_case(case), "state_target")

    conflict = prepared_case(tmp_path / "conflict")
    conflict["events"] = conflict["state"]
    assert_classification(lambda: route_case(conflict), "target_conflict")


def test_persistence_exception_is_sanitized_and_committed_bytes_are_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepared_case(tmp_path)
    state = case["state"]
    events = case["events"]
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def failing_owner(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"mutated-state")
        events.write_bytes(b"mutated-events")
        raise RuntimeError("secret provider detail")

    monkeypatch.setattr(phase147_module, "persist_prepared_running_state", failing_owner)
    with pytest.raises(_ERROR) as caught:
        route_case(case)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret provider detail" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


def test_invalid_persistence_postcondition_is_restored_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepared_case(tmp_path)
    state = case["state"]
    events = case["events"]
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def malformed_owner(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"malformed-state")
        events.write_bytes(b"mutated-events")
        return object()

    monkeypatch.setattr(phase147_module, "persist_prepared_running_state", malformed_owner)
    assert_classification(lambda: route_case(case), "persistence_contract")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


def test_owner_rollback_failure_is_safe_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepared_case(tmp_path)
    state = case["state"]
    events = case["events"]
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def rollback_owner(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"mutated-state")
        events.write_bytes(b"mutated-events")
        raise RunningStatePersistenceRollbackError("rollback")

    monkeypatch.setattr(phase147_module, "persist_prepared_running_state", rollback_owner)
    assert_classification(lambda: route_case(case), "dependency_rollback")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


def test_rollback_failure_surfaces_without_a_second_restore_or_persistence_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepared_case(tmp_path)
    state = case["state"]
    events = case["events"]
    calls = 0
    restore_calls = 0

    def malformed_owner(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"mutated-state")
        events.write_bytes(b"mutated-events")
        return object()

    def failed_restore(*_: object) -> None:
        nonlocal restore_calls
        restore_calls += 1
        raise _ERROR("dependency_rollback")

    monkeypatch.setattr(phase147_module, "persist_prepared_running_state", malformed_owner)
    monkeypatch.setattr(phase147_module, "_restore_if_changed", failed_restore)
    assert_classification(lambda: route_case(case), "dependency_rollback")
    assert calls == 1
    assert restore_calls == 1


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_stop_routes_are_identity_preserving_and_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    case = stop_case(
        tmp_path,
        status=status,
        terminal_provider="other",
        empty_positions=(2, 4),
    )
    before = case["before"]

    def unexpected_owner(*_: object) -> object:
        raise AssertionError("stop route must not persist a running state")

    monkeypatch.setattr(phase147_module, "persist_prepared_running_state", unexpected_owner)
    result = public_route(
        case["result"],
        case["workflow"],
        None,
        case["state"],
        case["events"],
    )
    assert result is case["result"]
    assert (case["state"].read_bytes(), case["events"].read_bytes()) == before


def test_stop_routes_reject_non_none_employee_without_writing(tmp_path: Path) -> None:
    case = stop_case(tmp_path, status="succeeded")
    definition = case["workflow"]
    assert isinstance(definition, WorkflowDefinition)
    before = case["before"]
    assert_classification(
        lambda: public_route(
            case["result"],
            definition,
            employee_for(definition, 1),
            case["state"],
            case["events"],
        ),
        "completion_contract",
    )
    assert (case["state"].read_bytes(), case["events"].read_bytes()) == before


def test_unsupported_result_is_rejected_without_side_effects(tmp_path: Path) -> None:
    definition = workflow()
    state, events, state_bytes, event_bytes = write_history(
        tmp_path, definition, current=5
    )
    assert_classification(
        lambda: public_route(object(), definition, None, state, events),
        "result_type",
    )
    assert (state.read_bytes(), events.read_bytes()) == (state_bytes, event_bytes)


def rewrite_json(path: Path, **changes: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(changes)
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def rewrite_event_json(path: Path, index: int, **changes: object) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[index])
    payload.update(changes)
    lines[index] = json.dumps(payload, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
