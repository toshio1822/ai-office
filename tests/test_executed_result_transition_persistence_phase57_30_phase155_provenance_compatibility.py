"""Real Phase 57 -> 50 -> 43 -> 36 -> 30 lower chain with exact Phase-155 provenance."""

# ruff: noqa: E501,E701,E702,F401,I001

import json
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError,
    route_executed_result_transition_persistence_phase_bridge_reentry,
)
from ai_office.engine.executed_result_transition_persistence_bridge_reentry import (
    route_executed_result_transition_persistence_bridge_reentry,
)
from ai_office.engine.executed_result_transition_reentry import (
    persist_executed_result_transition_reentry,
)
from ai_office.engine.executed_result_transition_routing_reentry import (
    route_executed_result_transition_reentry,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.runtime.executed_step_transition_persistence import (
    persist_executed_step_transition,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
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
                    "name": step_id.upper(),
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
        ModelInvocationSuccess("openai", "r6", "q6", "done", ("out",), "out"),
    )


def runtime_failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w",
        "s6",
        6,
        "e",
        ModelInvocationFailure("openai", "api_error", "safe", "q6", 500, None, None),
    )


def predecessor_event(
    step_id: str,
    position: int,
    output_text: object = "output",
    request_id: object = _SENTINEL,
) -> RuntimeStepEvent:
    resolved_request_id = (
        f"request-{step_id}" if request_id is _SENTINEL else request_id
    )
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


def setup(
    tmp_path: Path,
    *,
    empty_earlier: tuple[int, ...] = (2,),
    immediate_empty: bool = True,
    bad: object = _SENTINEL,
    bad_position: int = 2,
) -> dict[str, object]:
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state = WorkflowExecutionState(
        "w", "running", "s6", 6, "e", tuple(_STEP_IDS[:5]), None
    )
    state_path.write_text(
        serialize_workflow_execution_state_json(state), encoding="utf-8"
    )
    records = []
    for position, step_id in enumerate(_STEP_IDS[:5], 1):
        output: object = "output"
        if position in empty_earlier or (position == 5 and immediate_empty):
            output = ""
        if bad is not _SENTINEL and position == bad_position:
            output = bad
        request_id: object = None if position == 5 else f"request-{step_id}"
        records.append(
            serialize_runtime_step_event_jsonl(
                predecessor_event(step_id, position, output, request_id)
            )
        )
    events_path.write_text("".join(records), encoding="utf-8")
    return {
        "result": runtime_success(),
        "workflow": workflow(),
        "state_path": state_path,
        "events_path": events_path,
        "before_state": state_path.read_bytes(),
        "before_events": events_path.read_bytes(),
    }


def assert_provenance(
    values: dict[str, object],
    *,
    empty_earlier: tuple[int, ...] = (2,),
    immediate_empty: bool = True,
    bad: object = _SENTINEL,
    bad_position: int = 2,
) -> None:
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]  # type: ignore[arg-type]
        )
    )
    assert history.state.status == "running"
    assert history.state.current_step_id == "s6"
    assert history.state.current_step_index == 6
    assert history.state.completed_step_ids == ("s1", "s2", "s3", "s4", "s5")
    expected_outputs = [
        bad
        if bad is not _SENTINEL and position == bad_position
        else ""
        if position in empty_earlier or (position == 5 and immediate_empty)
        else "output"
        for position in range(1, 6)
    ]
    assert [event.output_text for event in history.events] == expected_outputs
    assert [event.request_id for event in history.events] == [
        "request-s1",
        "request-s2",
        "request-s3",
        "request-s4",
        None,
    ]
    assert [event.provider for event in history.events] == ["openai"] * 5
    assert all(
        type(event.response_id) is str and event.response_id
        for event in history.events
    )


def run_chain(
    values: dict[str, object],
) -> tuple[
    object,
    dict[str, int],
    list[tuple[str, tuple[object, ...]]],
    list[tuple[object, ...]],
]:
    calls = {"phase57": 0, "phase50": 0, "phase43": 0, "phase36": 0, "phase30": 0}
    handoffs: list[tuple[str, tuple[object, ...]]] = []
    phase30_args: list[tuple[object, ...]] = []

    def phase30(result: object, state: object, events: object) -> object:
        calls["phase30"] += 1
        phase30_args.append((result, state, events))
        return persist_executed_step_transition(result, state, events)

    def phase36(result: object, workflow: object, state: object, events: object) -> object:
        calls["phase36"] += 1
        handoffs.append(("phase36", (result, workflow, state, events)))
        return persist_executed_result_transition_reentry(
            result, workflow, state, events, persistence_function=phase30
        )

    def phase43(result: object, workflow: object, state: object, events: object) -> object:
        calls["phase43"] += 1
        handoffs.append(("phase43", (result, workflow, state, events)))
        return route_executed_result_transition_reentry(
            result, workflow, state, events, transition_reentry_function=phase36
        )

    def phase50(result: object, workflow: object, state: object, events: object) -> object:
        calls["phase50"] += 1
        handoffs.append(("phase50", (result, workflow, state, events)))
        return route_executed_result_transition_persistence_bridge_reentry(
            result, workflow, state, events, transition_routing_function=phase43
        )

    def phase57(result: object, workflow: object, state: object, events: object) -> object:
        calls["phase57"] += 1
        handoffs.append(("phase57", (result, workflow, state, events)))
        return route_executed_result_transition_persistence_phase_bridge_reentry(
            result, workflow, state, events, phase50_function=phase50
        )

    out = phase57(
        values["result"],
        values["workflow"],
        values["state_path"],
        values["events_path"],
    )
    return out, calls, handoffs, phase30_args


def assert_chain_ok(
    values: dict[str, object],
    out: object,
    calls: dict[str, int],
    handoffs: list[tuple[str, tuple[object, ...]]],
    phase30_args: list[tuple[object, ...]],
    result: object,
    *,
    success: bool,
) -> None:
    assert calls == {
        "phase57": 1,
        "phase50": 1,
        "phase43": 1,
        "phase36": 1,
        "phase30": 1,
    }
    assert type(out) is WorkflowExecutionPersistenceResult
    assert out.state_path is values["state_path"]
    assert out.events_path is values["events_path"]
    assert out.state_bytes_written == len(values["state_path"].read_bytes())  # type: ignore[union-attr]
    assert out.event_bytes_appended == len(values["events_path"].read_bytes()) - len(  # type: ignore[union-attr]
        values["before_events"]  # type: ignore[arg-type]
    )
    expected_args = (
        result,
        values["workflow"],
        values["state_path"],
        values["events_path"],
    )
    assert [name for name, _ in handoffs] == ["phase57", "phase50", "phase43", "phase36"]
    for _, args in handoffs:
        assert all(
            actual is wanted for actual, wanted in zip(args, expected_args, strict=True)
        )
    assert phase30_args == [(result, values["state_path"], values["events_path"])]
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]  # type: ignore[arg-type]
        )
    )
    assert len(history.events) == 6
    final = history.state
    event = history.events[-1]
    if success:
        assert final.status == "succeeded"
        assert final.completed_step_ids == tuple(_STEP_IDS)
        assert final.last_failure_category is None
        assert event.event_type == "step_succeeded"
        assert event.previous_status == "running"
        assert event.next_status == "succeeded"
        assert event.provider == "openai"
        assert event.response_id == "r6"
        assert event.request_id == "q6"
        assert event.output_text == "out"
        assert event.message is None
    else:
        assert final.status == "failed"
        assert final.completed_step_ids == tuple(_STEP_IDS[:5])
        assert final.last_failure_category == "api_error"
        assert event.event_type == "step_failed"
        assert event.previous_status == "running"
        assert event.next_status == "failed"
        assert event.provider == "openai"
        assert event.response_id is None
        assert event.request_id == "q6"
        assert event.output_text is None
        assert event.message == "safe"


def test_success_with_empty_provenance_persists_through_real_lower_path(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    assert_provenance(values)
    out, calls, handoffs, phase30_args = run_chain(values)
    assert_chain_ok(
        values, out, calls, handoffs, phase30_args, values["result"], success=True
    )


def test_failure_with_empty_provenance_persists_through_real_lower_path(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    values["result"] = runtime_failure()
    assert_provenance(values)
    out, calls, handoffs, phase30_args = run_chain(values)
    assert_chain_ok(
        values, out, calls, handoffs, phase30_args, values["result"], success=False
    )


def test_success_multiple_earlier_empty_persists_through_real_lower_path(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, empty_earlier=(2, 3, 4))
    assert_provenance(values, empty_earlier=(2, 3, 4))
    out, calls, handoffs, phase30_args = run_chain(values)
    assert_chain_ok(
        values, out, calls, handoffs, phase30_args, values["result"], success=True
    )


def test_failure_multiple_earlier_empty_persists_through_real_lower_path(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, empty_earlier=(2, 3, 4))
    values["result"] = runtime_failure()
    assert_provenance(values, empty_earlier=(2, 3, 4))
    out, calls, handoffs, phase30_args = run_chain(values)
    assert_chain_ok(
        values, out, calls, handoffs, phase30_args, values["result"], success=False
    )


def assert_raw_provenance(
    values: dict[str, object],
    *,
    expected_outputs: list[object],
) -> None:
    events_path = values["events_path"]  # type: ignore[arg-type]
    records = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["output_text"] for record in records] == expected_outputs
    assert [record["request_id"] for record in records] == [
        "request-s1",
        "request-s2",
        "request-s3",
        "request-s4",
        None,
    ]
    assert [record["provider"] for record in records] == ["openai"] * 5
    assert [record["step_id"] for record in records] == ["s1", "s2", "s3", "s4", "s5"]


def test_earlier_none_output_rejected_at_phase57_lower_chain_untouched(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, bad=None)
    assert_raw_provenance(
        values, expected_outputs=["output", None, "output", "output", ""]
    )
    before_state, before_events = values["before_state"], values["before_events"]
    calls = {"phase50": 0, "phase43": 0, "phase36": 0, "phase30": 0}

    def fail(*_: object) -> object:
        calls["phase50"] += 1
        pytest.fail("Phase 50 must not be called")

    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            values["result"],
            values["workflow"],
            values["state_path"],
            values["events_path"],
            phase50_function=fail,
        )
    assert caught.value.detail.classification == "runtime_contract"
    assert calls == {"phase50": 0, "phase43": 0, "phase36": 0, "phase30": 0}
    assert values["state_path"].read_bytes() == before_state  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == before_events  # type: ignore[union-attr]


def test_immediate_none_output_rejected_at_phase57_lower_chain_untouched(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, bad=None, bad_position=5)
    assert_raw_provenance(
        values, expected_outputs=["output", "", "output", "output", None]
    )
    before_state, before_events = values["before_state"], values["before_events"]
    calls = {"phase50": 0, "phase43": 0, "phase36": 0, "phase30": 0}

    def fail(*_: object) -> object:
        calls["phase50"] += 1
        pytest.fail("Phase 50 must not be called")

    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            values["result"],
            values["workflow"],
            values["state_path"],
            values["events_path"],
            phase50_function=fail,
        )
    assert caught.value.detail.classification == "runtime_contract"
    assert calls == {"phase50": 0, "phase43": 0, "phase36": 0, "phase30": 0}
    assert values["state_path"].read_bytes() == before_state  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == before_events  # type: ignore[union-attr]
