"""Real Phase 120 -> 113 -> 106 segment with Phase-155 provenance and synthetic Phase 99 seam."""

# ruff: noqa: E501,E701,E702,F401,I001

import json
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    route_runtime_result_transition_persistence_cycle_continuation_boundary,
    route_runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary,
    route_runtime_result_transition_persistence_cycle_reentry_continuation_boundary,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_STEP_IDS = ("s1", "s2", "s3", "s4", "s5", "s6")
_SENTINEL = object()


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": step_id,
                    "name": step_id.capitalize(),
                    "employee": "e",
                    "instructions": step_id,
                }
                for step_id in _STEP_IDS
            ],
        }
    )


def runtime_success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w",
        "s6",
        6,
        "e",
        ModelInvocationSuccess("openai", "r", "q", "done", ("out",), "out"),
    )


def runtime_failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w",
        "s6",
        6,
        "e",
        ModelInvocationFailure("openai", "api_error", "safe", "q", 500, None, None),
    )


def predecessor_event(
    step_id: str,
    position: int,
    output_text: object = "output",
    request_id: object = _SENTINEL,
) -> RuntimeStepEvent:
    resolved_request_id = f"request-{step_id}" if request_id is _SENTINEL else request_id
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step_id,
        position,
        "e",
        "running",
        "succeeded",
        "openai",
        None,
        f"response-{step_id}",
        resolved_request_id,  # type: ignore[arg-type]
        output_text,  # type: ignore[arg-type]
        None,
    )


def setup(tmp_path: Path, empty_earlier: tuple[int, ...] = (2,)) -> dict[str, object]:
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state = WorkflowExecutionState(
        "w", "running", "s6", 6, "e", tuple(_STEP_IDS[:5]), None
    )
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text(
        "".join(
            serialize_runtime_step_event_jsonl(
                predecessor_event(
                    step_id,
                    position,
                    output_text=""
                    if position == 5 or position in empty_earlier
                    else "output",
                    request_id=None if position == 5 else _SENTINEL,
                )
            )
            for position, step_id in enumerate(_STEP_IDS[:5], 1)
        ),
        encoding="utf-8",
    )
    return {
        "result": runtime_success(),
        "workflow": workflow(),
        "state_path": state_path,
        "events_path": events_path,
    }


def assert_pre_seam_provenance(
    state_path: Path, events_path: Path, empty_earlier: tuple[int, ...] = (2,)
) -> None:
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(state_path, events_path)
    )
    assert history.state.status == "running"
    assert history.state.current_step_id == "s6"
    assert history.state.current_step_index == 6
    assert history.state.completed_step_ids == ("s1", "s2", "s3", "s4", "s5")
    assert [event.output_text for event in history.events] == [
        "" if position == 5 or position in empty_earlier else "output"
        for position in range(1, 6)
    ]
    assert [event.request_id for event in history.events] == [
        "request-s1",
        "request-s2",
        "request-s3",
        "request-s4",
        None,
    ]
    assert all(
        type(event.response_id) is str and event.response_id
        for event in history.events
    )
    assert all(event.provider == "openai" for event in history.events)


def persist_seam(
    result: object, _workflow: object, state_path: Path, events_path: Path
) -> WorkflowExecutionPersistenceResult:
    invocation = result.invocation_result  # type: ignore[union-attr]
    current = load_workflow_execution_state(state_path)
    successful = type(result) is StepRuntimeExecutionSuccess
    next_state = WorkflowExecutionState(
        "w",
        "succeeded" if successful else "failed",
        result.step_id,  # type: ignore[union-attr]
        result.step_index,  # type: ignore[union-attr]
        result.employee_id,  # type: ignore[union-attr]
        current.completed_step_ids + ((result.step_id,) if successful else ()),  # type: ignore[union-attr]
        None if successful else invocation.category,
    )
    event = RuntimeStepEvent(
        "step_succeeded" if successful else "step_failed",
        result.workflow_id,  # type: ignore[union-attr]
        result.step_id,  # type: ignore[union-attr]
        result.step_index,  # type: ignore[union-attr]
        result.employee_id,  # type: ignore[union-attr]
        "running",
        next_state.status,
        invocation.provider,
        None if successful else invocation.category,
        invocation.response_id if successful else None,
        invocation.request_id,
        invocation.text if successful else None,
        None if successful else invocation.message,
    )
    state_bytes = serialize_workflow_execution_state_json(next_state).encode("utf-8")
    event_bytes = serialize_runtime_step_event_jsonl(event).encode("utf-8")
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(events_path.read_bytes() + event_bytes)
    return WorkflowExecutionPersistenceResult(
        state_path, events_path, len(state_bytes), len(event_bytes)
    )


def run_chain(
    values: dict[str, object],
    empty_earlier: tuple[int, ...] = (2,),
) -> tuple[object, dict[str, int], list[tuple[str, tuple[object, ...]]], list[object]]:
    calls = {"phase120": 0, "phase113": 0, "phase106": 0, "seam": 0}
    handoffs: list[tuple[str, tuple[object, ...]]] = []
    seam_values: list[object] = []

    def seam(result: object, workflow: object, state: object, events: object) -> object:
        calls["seam"] += 1
        handoffs.append(("seam", (result, workflow, state, events)))
        assert_pre_seam_provenance(state, events, empty_earlier)  # type: ignore[arg-type]
        seam_values.append(persist_seam(result, workflow, state, events))
        return seam_values[-1]

    def phase106(result: object, workflow: object, state: object, events: object) -> object:
        calls["phase106"] += 1
        handoffs.append(("phase106", (result, workflow, state, events)))
        return route_runtime_result_transition_persistence_cycle_continuation_boundary(
            result, workflow, state, events, phase99_function=seam  # type: ignore[arg-type]
        )

    def phase113(result: object, workflow: object, state: object, events: object) -> object:
        calls["phase113"] += 1
        handoffs.append(("phase113", (result, workflow, state, events)))
        return route_runtime_result_transition_persistence_cycle_reentry_continuation_boundary(
            result, workflow, state, events, phase106_function=phase106  # type: ignore[arg-type]
        )

    def phase120(result: object, workflow: object, state: object, events: object) -> object:
        calls["phase120"] += 1
        handoffs.append(("phase120", (result, workflow, state, events)))
        return route_runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary(
            result, workflow, state, events, phase113_function=phase113  # type: ignore[arg-type]
        )

    out = phase120(
        values["result"], values["workflow"], values["state_path"], values["events_path"]
    )
    return out, calls, handoffs, seam_values


def assert_chain_ok(
    values: dict[str, object],
    out: object,
    calls: dict[str, int],
    handoffs: list[tuple[str, tuple[object, ...]]],
    seam_values: list[object],
) -> None:
    assert calls == {"phase120": 1, "phase113": 1, "phase106": 1, "seam": 1}
    assert out is seam_values[0]
    expected = tuple(
        values[key] for key in ("result", "workflow", "state_path", "events_path")
    )
    assert [name for name, _ in handoffs] == ["phase120", "phase113", "phase106", "seam"]
    for _, args in handoffs:
        assert all(
            actual is wanted for actual, wanted in zip(args, expected, strict=True)
        )


@pytest.mark.parametrize("result_factory", [runtime_success, runtime_failure])
def test_combined_earlier_empty_immediate_empty_none_reaches_seam_once(
    tmp_path: Path, result_factory: object
) -> None:
    values = setup(tmp_path)
    values["result"] = result_factory()  # type: ignore[operator]
    out, calls, handoffs, seam_values = run_chain(values)
    assert_chain_ok(values, out, calls, handoffs, seam_values)


@pytest.mark.parametrize("result_factory", [runtime_success, runtime_failure])
def test_multiple_earlier_empty_plus_immediate_empty_none_reaches_seam_once(
    tmp_path: Path, result_factory: object
) -> None:
    values = setup(tmp_path, empty_earlier=(2, 3, 4))
    values["result"] = result_factory()  # type: ignore[operator]
    out, calls, handoffs, seam_values = run_chain(values, empty_earlier=(2, 3, 4))
    assert_chain_ok(values, out, calls, handoffs, seam_values)


def test_earlier_predecessor_none_output_rejected_before_phase113_106_seam(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("s2", 2, output_text=None, request_id="request-s2")
    )
    events.write_text(lines[0] + replacement + "".join(lines[2:]), encoding="utf-8")  # type: ignore[union-attr]
    records = [
        json.loads(line)
        for line in events.read_text(encoding="utf-8").splitlines()  # type: ignore[union-attr]
    ]
    assert [record["output_text"] for record in records] == [
        "output",
        None,
        "output",
        "output",
        "",
    ]
    assert [record["request_id"] for record in records] == [
        "request-s1",
        "request-s2",
        "request-s3",
        "request-s4",
        None,
    ]
    assert [record["step_id"] for record in records] == ["s1", "s2", "s3", "s4", "s5"]
    before = values["state_path"].read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    calls = {"phase113": 0, "phase106": 0, "seam": 0}

    def fail(*_: object) -> object:
        calls["phase113"] += 1
        pytest.fail("Phase 113 must not be called")

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary(
            values["result"],  # type: ignore[arg-type]
            values["workflow"],  # type: ignore[arg-type]
            values["state_path"],  # type: ignore[arg-type]
            values["events_path"],  # type: ignore[arg-type]
            phase113_function=fail,  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "runtime_contract"
    assert calls["phase113"] == 0 and calls["phase106"] == 0 and calls["seam"] == 0
    assert (values["state_path"].read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


def test_immediate_predecessor_none_output_rejected_before_phase113_106_seam(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("s5", 5, output_text=None, request_id=None)
    )
    events.write_text("".join(lines[:4]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    records = [
        json.loads(line)
        for line in events.read_text(encoding="utf-8").splitlines()  # type: ignore[union-attr]
    ]
    assert [record["output_text"] for record in records] == [
        "output",
        "",
        "output",
        "output",
        None,
    ]
    assert [record["request_id"] for record in records] == [
        "request-s1",
        "request-s2",
        "request-s3",
        "request-s4",
        None,
    ]
    assert [record["step_id"] for record in records] == ["s1", "s2", "s3", "s4", "s5"]
    before = values["state_path"].read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    calls = {"phase113": 0, "phase106": 0, "seam": 0}

    def fail(*_: object) -> object:
        calls["phase113"] += 1
        pytest.fail("Phase 113 must not be called")

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary(
            values["result"],  # type: ignore[arg-type]
            values["workflow"],  # type: ignore[arg-type]
            values["state_path"],  # type: ignore[arg-type]
            values["events_path"],  # type: ignore[arg-type]
            phase113_function=fail,  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "runtime_contract"
    assert calls["phase113"] == 0 and calls["phase106"] == 0 and calls["seam"] == 0
    assert (values["state_path"].read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]
