"""Behavioral tests for the Phase 161 runtime-result transition-persistence facade.

The facade now owns the runtime-result route directly: it validates the exact
running state, predecessor history and runtime result, then performs exactly one
transition/persistence through the current runtime owner.  These tests assert
observable committed state, event history, rollback and zero-write behavior, not
the removed historical wrapper topology.
"""

# ruff: noqa: E501

from __future__ import annotations

import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

import ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase161_module
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError,
    WorkflowProgressionDecision,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as public_route,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceFailureDetail,
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceRollbackError,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_ERROR = RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError


def workflow(count: int = 8) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "runtime-result transition persistence behavior",
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


def successor_result(
    definition: WorkflowDefinition, index: int
) -> StepRuntimeExecutionSuccess:
    step = definition.steps[index - 1]
    return StepRuntimeExecutionSuccess(
        definition.id,
        step.id,
        index,
        step.employee,
        ModelInvocationSuccess(
            "openai", f"response-{index}", f"request-{index}", "completed", ("out",), "out"
        ),
    )


def failing_result(
    definition: WorkflowDefinition, index: int
) -> StepRuntimeExecutionFailure:
    step = definition.steps[index - 1]
    return StepRuntimeExecutionFailure(
        definition.id,
        step.id,
        index,
        step.employee,
        ModelInvocationFailure(
            "openai", "api_error", "safe failure", f"request-{index}", 500, None, None
        ),
    )


_UNSET = object()


def predecessor_event(
    definition: WorkflowDefinition,
    index: int,
    *,
    provider: object = "openai",
    request_id: object = _UNSET,
    output_text: object = _UNSET,
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
        provider,  # type: ignore[arg-type]
        None,
        f"predecessor-response-{index}",
        f"predecessor-request-{index}" if request_id is _UNSET else request_id,  # type: ignore[arg-type]
        f"predecessor-output-{index}" if output_text is _UNSET else output_text,  # type: ignore[arg-type]
        None,
    )


def write_history(
    tmp_path: Path,
    definition: WorkflowDefinition,
    *,
    current: int,
    status: str = "running",
    terminal_provider: object = "other",
    events: list[RuntimeStepEvent] | None = None,
) -> tuple[Path, Path, bytes, bytes]:
    state_path = tmp_path / "state.json"
    events_path = tmp_path / "events.jsonl"
    previous = tuple(step.id for step in definition.steps[: current - 1])
    current_step = definition.steps[current - 1]
    completed = previous + ((current_step.id,) if status == "succeeded" else ())
    state = WorkflowExecutionState(
        definition.id,
        status,  # type: ignore[arg-type]
        current_step.id,
        current,
        current_step.employee,
        completed,
        "api_error" if status == "failed" else None,
    )
    if events is None:
        events = [
            predecessor_event(definition, index)
            for index in range(1, current)
        ]
        if status in ("succeeded", "failed"):
            if status == "succeeded":
                events.append(
                    RuntimeStepEvent(
                        "step_succeeded",
                        definition.id,
                        current_step.id,
                        current,
                        current_step.employee,
                        "running",
                        "succeeded",
                        terminal_provider,  # type: ignore[arg-type]
                        None,
                        f"terminal-response-{current}",
                        f"terminal-request-{current}",
                        "out",
                        None,
                    )
                )
            else:
                events.append(
                    RuntimeStepEvent(
                        "step_failed",
                        definition.id,
                        current_step.id,
                        current,
                        current_step.employee,
                        "running",
                        "failed",
                        terminal_provider,  # type: ignore[arg-type]
                        "api_error",
                        None,
                        f"terminal-request-{current}",
                        None,
                        "safe failure",
                    )
                )
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = "".join(
        serialize_runtime_step_event_jsonl(event) for event in events
    ).encode("utf-8")
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes


def running_case(
    tmp_path: Path,
    *,
    index: int = 6,
    events: list[RuntimeStepEvent] | None = None,
) -> dict[str, object]:
    definition = workflow()
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path, events_path, state_bytes, event_bytes = write_history(
        tmp_path, definition, current=index, events=events
    )
    return {
        "workflow": definition,
        "result": successor_result(definition, index),
        "state": state_path,
        "events": events_path,
        "before": (state_bytes, event_bytes),
    }


def stop_case(tmp_path: Path, *, status: str, index: int = 6) -> dict[str, object]:
    definition = workflow(index)
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path, events_path, state_bytes, event_bytes = write_history(
        tmp_path, definition, current=index, status=status
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
            "persisted_failure", definition.id, step.id, index, step.employee, "api_error"
        )
    return {
        "workflow": definition,
        "result": result,
        "state": state_path,
        "events": events_path,
        "before": (state_bytes, event_bytes),
    }


def assert_classification(callable_object: object, expected: str) -> None:
    with pytest.raises(_ERROR) as caught:
        callable_object()  # type: ignore[operator]
    assert caught.value.detail.classification == expected


def route_case(case: dict[str, object]) -> object:
    return public_route(
        case["result"],
        case["workflow"],
        case["state"],
        case["events"],
    )


def committed_history(case: dict[str, object]) -> object:
    return load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            case["state"], case["events"]  # type: ignore[arg-type]
        )
    )


def test_public_facade_has_no_historical_injection_seam() -> None:
    parameters = tuple(inspect.signature(public_route).parameters.values())
    assert tuple(parameter.name for parameter in parameters) == (
        "result",
        "workflow",
        "state_path",
        "events_path",
    )
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        and parameter.annotation is object
        for parameter in parameters
    )


def test_valid_success_persists_exact_state_and_single_event(tmp_path: Path) -> None:
    case = running_case(tmp_path)
    result = case["result"]
    before = case["before"]
    persisted = route_case(case)
    assert type(persisted) is WorkflowExecutionPersistenceResult
    assert persisted.state_path is case["state"]
    assert persisted.events_path is case["events"]
    history = committed_history(case)
    state = history.state
    assert state.status == "succeeded"
    assert state.workflow_id == "workflow"
    assert state.current_step_index == 6
    assert state.current_step_id == "step-6"
    assert state.current_employee_id == "employee-6"
    assert state.completed_step_ids == (
        "step-1", "step-2", "step-3", "step-4", "step-5", "step-6",
    )
    assert state.last_failure_category is None
    assert len(history.events) == 6
    assert history.events[:-1] == tuple(
        predecessor_event(case["workflow"], index)  # type: ignore[arg-type]
        for index in range(1, 6)
    )
    event = history.events[-1]
    assert event.event_type == "step_succeeded"
    assert event.previous_status == "running"
    assert event.next_status == "succeeded"
    assert event.provider == result.invocation_result.provider  # type: ignore[union-attr]
    assert event.response_id == result.invocation_result.response_id  # type: ignore[union-attr]
    assert event.request_id == result.invocation_result.request_id  # type: ignore[union-attr]
    assert event.output_text == result.invocation_result.text  # type: ignore[union-attr]
    assert event.message is None
    assert persisted.state_bytes_written == len(case["state"].read_bytes())  # type: ignore[union-attr]
    assert persisted.event_bytes_appended == (
        len(case["events"].read_bytes()) - len(before[1])  # type: ignore[union-attr]
    )
    assert case["events"].read_bytes().startswith(before[1])  # type: ignore[union-attr]
    assert case["events"].read_bytes()[len(before[1]):] == (  # type: ignore[union-attr]
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
    )


def test_valid_failure_persists_exact_state_and_single_event(tmp_path: Path) -> None:
    case = running_case(tmp_path)
    case["result"] = failing_result(case["workflow"], 6)  # type: ignore[arg-type]
    before = case["before"]
    persisted = route_case(case)
    assert type(persisted) is WorkflowExecutionPersistenceResult
    history = committed_history(case)
    state = history.state
    assert state.status == "failed"
    assert state.completed_step_ids == (
        "step-1", "step-2", "step-3", "step-4", "step-5",
    )
    assert state.last_failure_category == "api_error"
    assert len(history.events) == 6
    event = history.events[-1]
    assert event.event_type == "step_failed"
    assert event.previous_status == "running"
    assert event.next_status == "failed"
    assert event.provider == "openai"
    assert event.request_id == "request-6"
    assert event.failure_category == "api_error"
    assert event.response_id is None
    assert event.output_text is None
    assert event.message == "safe failure"
    assert persisted.event_bytes_appended == (
        len(case["events"].read_bytes()) - len(before[1])  # type: ignore[union-attr]
    )


@pytest.mark.parametrize("index", [1, 2, 3, 5, 6, 8])
def test_valid_result_at_any_index_persists_once(tmp_path: Path, index: int) -> None:
    case = running_case(tmp_path, index=index)
    before = case["before"]
    route_case(case)
    history = committed_history(case)
    assert history.state.status == "succeeded"
    assert history.state.current_step_index == index
    assert len(history.events) == index
    assert history.events[-1].step_index == index
    appended = case["events"].read_bytes()[len(before[1]):]  # type: ignore[union-attr]
    assert appended.count(b"\n") == 1


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_stop_routes_are_identity_preserving_and_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    case = stop_case(tmp_path, status=status)
    before = case["before"]
    calls = 0

    def unexpected_owner(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("stop route must not persist a transition")

    monkeypatch.setattr(phase161_module, "persist_executed_step_transition", unexpected_owner)
    assert route_case(case) is case["result"]
    assert calls == 0
    assert (case["state"].read_bytes(), case["events"].read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_stop_routes_reject_malformed_values_without_writing(
    tmp_path: Path, status: str
) -> None:
    case = stop_case(tmp_path, status=status)
    before = case["before"]
    classification = "completion_contract" if status == "succeeded" else "failure_contract"
    malformed = replace(case["result"], reason="wrong") if status == "succeeded" else replace(
        case["result"], failure_category="not-a-category"
    )
    assert_classification(
        lambda: public_route(
            malformed, case["workflow"], case["state"], case["events"]
        ),
        classification,
    )
    assert (case["state"].read_bytes(), case["events"].read_bytes()) == before  # type: ignore[union-attr]


def test_stop_route_empty_success_output_is_rejected_without_writing(
    tmp_path: Path,
) -> None:
    case = stop_case(tmp_path, status="succeeded")
    events_path = case["events"]
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    payload = json.loads(lines[-1])
    payload["output_text"] = ""
    lines[-1] = json.dumps(payload, separators=(",", ":")) + "\n"
    events_path.write_text("".join(lines), encoding="utf-8")  # type: ignore[union-attr]
    before = case["state"].read_bytes(), case["events"].read_bytes()  # type: ignore[union-attr]
    assert_classification(lambda: route_case(case), "terminal_contract")
    assert (case["state"].read_bytes(), case["events"].read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "succeeded"),
        ("current_step_index", 5),
        ("current_step_id", "step-2"),
        ("current_employee_id", "employee-2"),
        ("completed_step_ids", ("step-1", "step-2")),
        ("last_failure_category", "transport_error"),
        ("workflow_id", "other-workflow"),
    ],
)
def test_malformed_or_stale_state_fails_closed_before_write(
    tmp_path: Path, field: str, value: object
) -> None:
    case = running_case(tmp_path)
    state = load_workflow_execution_state(case["state"])  # type: ignore[arg-type]
    corrupted = replace(state, **{field: value})
    case["state"].write_bytes(  # type: ignore[union-attr]
        serialize_workflow_execution_state_json(corrupted).encode("utf-8")
    )
    before = case["state"].read_bytes(), case["events"].read_bytes()  # type: ignore[union-attr]
    assert_classification(lambda: route_case(case), "runtime_contract")
    assert (case["state"].read_bytes(), case["events"].read_bytes()) == before  # type: ignore[union-attr]


def test_malformed_predecessor_history_fails_closed_before_write(
    tmp_path: Path,
) -> None:
    case = running_case(tmp_path)
    events_path = case["events"]
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    payload = json.loads(lines[1])
    payload["next_status"] = "failed"
    lines[1] = json.dumps(payload, separators=(",", ":")) + "\n"
    events_path.write_text("".join(lines), encoding="utf-8")  # type: ignore[union-attr]
    before = case["state"].read_bytes(), case["events"].read_bytes()  # type: ignore[union-attr]
    assert_classification(lambda: route_case(case), "runtime_contract")
    assert (case["state"].read_bytes(), case["events"].read_bytes()) == before  # type: ignore[union-attr]


def test_truncated_predecessor_history_fails_closed_before_write(
    tmp_path: Path,
) -> None:
    case = running_case(tmp_path)
    events_path = case["events"]
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    events_path.write_text("".join(lines[:-1]), encoding="utf-8")  # type: ignore[union-attr]
    before = case["state"].read_bytes(), case["events"].read_bytes()  # type: ignore[union-attr]
    assert_classification(lambda: route_case(case), "runtime_contract")
    assert (case["state"].read_bytes(), case["events"].read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "other-workflow"),
        ("step_id", "step-2"),
        ("step_index", 5),
        ("employee_id", "employee-2"),
    ],
)
def test_mismatched_runtime_result_fails_closed_before_write(
    tmp_path: Path, field: str, value: object
) -> None:
    case = running_case(tmp_path)
    case["result"] = replace(case["result"], **{field: value})  # type: ignore[arg-type]
    before = case["state"].read_bytes(), case["events"].read_bytes()  # type: ignore[union-attr]
    assert_classification(lambda: route_case(case), "runtime_contract")
    assert (case["state"].read_bytes(), case["events"].read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", ["success", "failure"])
def test_invocation_contract_rejections_fail_closed_before_write(
    tmp_path: Path, kind: str
) -> None:
    case = running_case(tmp_path)
    result = case["result"]
    if kind == "success":
        broken = replace(
            result,
            invocation_result=replace(  # type: ignore[arg-type]
                result.invocation_result, response_id=""  # type: ignore[union-attr]
            ),
        )
    else:
        case["result"] = failing_result(case["workflow"], 6)  # type: ignore[arg-type]
        broken = replace(
            case["result"],
            invocation_result=replace(  # type: ignore[arg-type]
                case["result"].invocation_result,  # type: ignore[union-attr]
                category="not-a-category",
            ),
        )
    case["result"] = broken
    before = case["state"].read_bytes(), case["events"].read_bytes()  # type: ignore[union-attr]
    assert_classification(lambda: route_case(case), "runtime_contract")
    assert (case["state"].read_bytes(), case["events"].read_bytes()) == before  # type: ignore[union-attr]


def test_unsupported_result_is_rejected_without_side_effects(tmp_path: Path) -> None:
    case = running_case(tmp_path)
    before = case["state"].read_bytes(), case["events"].read_bytes()  # type: ignore[union-attr]
    assert_classification(
        lambda: public_route(object(), case["workflow"], case["state"], case["events"]),
        "result_type",
    )
    assert (case["state"].read_bytes(), case["events"].read_bytes()) == before  # type: ignore[union-attr]


def test_target_conflict_and_missing_targets_are_rejected_without_writing(
    tmp_path: Path,
) -> None:
    case = running_case(tmp_path)
    assert_classification(
        lambda: public_route(
            case["result"], case["workflow"], case["state"], case["state"]
        ),
        "target_conflict",
    )
    case["state"].unlink()  # type: ignore[union-attr]
    assert_classification(lambda: route_case(case), "state_target")


def test_provider_compatibility_preserves_immediate_and_earlier_rules(
    tmp_path: Path,
) -> None:
    definition = workflow()
    accepted = [
        predecessor_event(definition, index, provider="other", output_text="")
        for index in range(1, 5)
    ]
    accepted.append(
        predecessor_event(definition, 5, provider="openai", output_text="")
    )
    case = running_case(tmp_path / "accepted", index=6, events=accepted)
    persisted = route_case(case)
    assert type(persisted) is WorkflowExecutionPersistenceResult
    assert committed_history(case).state.status == "succeeded"

    rejected = [
        predecessor_event(definition, index, provider="other", output_text="")
        for index in range(1, 5)
    ]
    rejected.append(
        predecessor_event(definition, 5, provider="other", output_text="")
    )
    bad = running_case(tmp_path / "rejected", index=6, events=rejected)
    before = bad["state"].read_bytes(), bad["events"].read_bytes()  # type: ignore[union-attr]
    assert_classification(lambda: route_case(bad), "runtime_contract")
    assert (bad["state"].read_bytes(), bad["events"].read_bytes()) == before  # type: ignore[union-attr]


def test_accumulated_none_request_id_is_bounded(tmp_path: Path) -> None:
    definition = workflow()
    # Positions 5-7 carry the accumulated None provenance; positions 1-4 do not.
    accepted = [
        predecessor_event(definition, index, provider="openai")
        for index in range(1, 5)
    ]
    accepted.append(
        predecessor_event(definition, 5, provider="openai", request_id=None, output_text="")
    )
    accepted.append(
        predecessor_event(definition, 6, provider="openai", request_id=None, output_text="")
    )
    accepted.append(
        predecessor_event(definition, 7, provider="openai", request_id=None, output_text="")
    )
    case = running_case(tmp_path / "accepted", index=8, events=accepted)
    persisted = route_case(case)
    assert type(persisted) is WorkflowExecutionPersistenceResult
    assert committed_history(case).state.status == "succeeded"

    # Position 4 is below the bounded accumulated-None window.
    rejected = [
        predecessor_event(definition, index, provider="openai")
        for index in range(1, 4)
    ]
    rejected.append(
        predecessor_event(definition, 4, provider="openai", request_id=None, output_text="")
    )
    for index in range(5, 8):
        rejected.append(
            predecessor_event(definition, index, provider="openai", request_id=None, output_text="")
        )
    bad = running_case(tmp_path / "rejected", index=8, events=rejected)
    before = bad["state"].read_bytes(), bad["events"].read_bytes()  # type: ignore[union-attr]
    assert_classification(lambda: route_case(bad), "runtime_contract")
    assert (bad["state"].read_bytes(), bad["events"].read_bytes()) == before  # type: ignore[union-attr]


def test_empty_predecessor_output_remains_accepted(tmp_path: Path) -> None:
    definition = workflow()
    events = []
    for index in range(1, 6):
        provider = "openai" if index == 5 else "other"
        events.append(
            predecessor_event(
                definition, index, provider=provider, output_text=""
            )
        )
    case = running_case(tmp_path, events=events)
    persisted = route_case(case)
    assert type(persisted) is WorkflowExecutionPersistenceResult
    assert committed_history(case).state.status == "succeeded"


def test_persistence_exception_is_sanitized_and_bytes_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = running_case(tmp_path)
    state = case["state"]
    events = case["events"]
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def failing_owner(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"mutated-state")
        events.write_bytes(b"mutated-events")
        raise RuntimeError("secret persistence detail")

    monkeypatch.setattr(phase161_module, "persist_executed_step_transition", failing_owner)
    with pytest.raises(_ERROR) as caught:
        route_case(case)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret persistence detail" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


def test_invalid_persistence_postcondition_is_restored_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = running_case(tmp_path)
    state = case["state"]
    events = case["events"]
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def malformed_owner(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"malformed-state")
        events.write_bytes(b"mutated-events")
        return object()

    monkeypatch.setattr(phase161_module, "persist_executed_step_transition", malformed_owner)
    assert_classification(lambda: route_case(case), "persistence_contract")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


def test_owner_rollback_failure_is_safe_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = running_case(tmp_path)
    state = case["state"]
    events = case["events"]
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def rollback_owner(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"mutated-state")
        events.write_bytes(b"mutated-events")
        raise WorkflowExecutionPersistenceRollbackError(
            WorkflowExecutionPersistenceFailureDetail("persistence"),
            (WorkflowExecutionPersistenceFailureDetail("restore_state"),),
        )

    monkeypatch.setattr(phase161_module, "persist_executed_step_transition", rollback_owner)
    assert_classification(lambda: route_case(case), "dependency_rollback")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


def test_rollback_failure_surfaces_without_a_second_restore_or_persistence_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = running_case(tmp_path)
    calls = 0
    restore_calls = 0

    def malformed_owner(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    def failed_restore(*_: object) -> None:
        nonlocal restore_calls
        restore_calls += 1
        raise _ERROR("dependency_rollback")

    monkeypatch.setattr(phase161_module, "persist_executed_step_transition", malformed_owner)
    monkeypatch.setattr(phase161_module, "_restore_if_changed", failed_restore)
    assert_classification(lambda: route_case(case), "dependency_rollback")
    assert calls == 1
    assert restore_calls == 1


def test_real_default_success_composition(tmp_path: Path) -> None:
    """No injected owner: the whole default route persists through the real owner."""
    case = running_case(tmp_path)
    before = case["before"]
    persisted = route_case(case)
    assert type(persisted) is WorkflowExecutionPersistenceResult
    history = committed_history(case)
    assert history.state.status == "succeeded"
    assert len(history.events) == 6
    assert history.events[-1].event_type == "step_succeeded"
    assert case["events"].read_bytes().startswith(before[1])  # type: ignore[union-attr]


def test_real_default_failure_composition(tmp_path: Path) -> None:
    case = running_case(tmp_path)
    case["result"] = failing_result(case["workflow"], 6)  # type: ignore[arg-type]
    persisted = route_case(case)
    assert type(persisted) is WorkflowExecutionPersistenceResult
    history = committed_history(case)
    assert history.state.status == "failed"
    assert len(history.events) == 6
    assert history.events[-1].event_type == "step_failed"
