"""Real Phase 121 -> 114 -> 107 segment with Phase-155 provenance and synthetic Phase 100 seam."""

# ruff: noqa: E501,E701,E702,F401,I001

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import PersistedExecutionOutcome
from ai_office.engine.persisted_outcome_classification_dispatch_continuation_boundary import (
    PersistedOutcomeClassificationDispatchContinuationCompatibilityError,
    route_persisted_outcome_classification_dispatch_continuation_boundary,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_continuation_boundary import (
    route_persisted_transition_outcome_classification_cycle_continuation_boundary,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationCompatibilityError,
    route_persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_reentry_continuation_boundary import (
    route_persisted_transition_outcome_classification_cycle_reentry_continuation_boundary,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_STEP_IDS = ("one", "two", "three", "four", "five", "six")
_SENTINEL = object()


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": step_id, "name": step_id.capitalize(), "employee": step_id[0], "instructions": step_id}
                for step_id in _STEP_IDS
            ],
        }
    )


def predecessor_event(
    step_id: str,
    position: int,
    provider: object = "other",
    request_id: object = _SENTINEL,
    output_text: object = "output",
) -> RuntimeStepEvent:
    resolved_request_id = f"request-{step_id}" if request_id is _SENTINEL else request_id
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step_id,
        position,
        step_id[0],
        "running",
        "succeeded",
        provider,  # type: ignore[arg-type]
        None,
        f"response-{step_id}",
        resolved_request_id,  # type: ignore[arg-type]
        output_text,  # type: ignore[arg-type]
        None,
    )


def terminal_event(status: str) -> RuntimeStepEvent:
    if status == "succeeded":
        return RuntimeStepEvent(
            "step_succeeded",
            "w",
            "six",
            6,
            "s",
            "running",
            "succeeded",
            "openai",
            None,
            "response-six",
            "request-six",
            "output-six",
            None,
        )
    return RuntimeStepEvent(
        "step_failed",
        "w",
        "six",
        6,
        "s",
        "running",
        "failed",
        "openai",
        "api_error",
        None,
        "request-six",
        None,
        "safe failure",
    )


def setup(
    tmp_path: Path, status: str, *, earlier_empty: tuple[int, ...] = (2,)
) -> dict[str, object]:
    supplied_workflow = workflow()
    state = WorkflowExecutionState(
        "w",
        status,
        "six",
        6,
        "s",
        tuple(_STEP_IDS) if status == "succeeded" else tuple(_STEP_IDS[:5]),
        None if status == "succeeded" else "api_error",
    )
    events = [
        predecessor_event(
            step_id, position, output_text="" if position in earlier_empty else "output"
        )
        for position, step_id in enumerate(_STEP_IDS[:5], 1)
    ]
    events[4] = predecessor_event(
        "five", 5, provider="openai", request_id=None, output_text=""
    )
    events.append(terminal_event(status))
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = "".join(
        serialize_runtime_step_event_jsonl(event) for event in events
    ).encode("utf-8")
    terminal_bytes = serialize_runtime_step_event_jsonl(events[-1]).encode("utf-8")
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result = WorkflowExecutionPersistenceResult(
        state_path,
        events_path,
        len(state_bytes),
        len(terminal_bytes),
    )
    return {
        "result": result,
        "workflow": supplied_workflow,
        "state_path": state_path,
        "events_path": events_path,
    }


def reload_and_assert_provenance(
    values: dict[str, object], status: str, *, earlier_empty: tuple[int, ...] = (2,)
) -> None:
    """Public-loader reload proves the empty outputs are really persisted and the
    reloaded terminal state/history matches the expected outcome contract."""
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"],  # type: ignore[arg-type]
            values["events_path"],  # type: ignore[arg-type]
        )
    )
    state, events = loaded.state, loaded.events
    for position in earlier_empty:
        assert events[position - 1].step_id == _STEP_IDS[position - 1]
        assert events[position - 1].output_text == ""
    assert events[4].step_id == "five"
    assert events[4].output_text == ""
    assert events[4].request_id is None
    assert state.status == status
    assert state.current_step_id == "six"
    assert state.current_step_index == 6
    assert state.current_employee_id == "s"
    assert state.completed_step_ids == (
        tuple(_STEP_IDS) if status == "succeeded" else tuple(_STEP_IDS[:5])
    )
    assert state.last_failure_category == (
        None if status == "succeeded" else "api_error"
    )
    terminal = events[-1]
    assert terminal.step_id == "six"
    assert terminal.step_index == 6
    assert terminal.employee_id == "s"
    assert terminal.provider == "openai"
    if status == "succeeded":
        assert terminal.event_type == "step_succeeded"
        assert terminal.next_status == "succeeded"
        assert terminal.failure_category is None
        assert terminal.response_id == "response-six"
        assert terminal.request_id == "request-six"
        assert terminal.output_text == "output-six"
        assert terminal.message is None
    else:
        assert terminal.event_type == "step_failed"
        assert terminal.next_status == "failed"
        assert terminal.failure_category == "api_error"
        assert terminal.response_id is None
        assert terminal.request_id == "request-six"
        assert terminal.output_text is None
        assert terminal.message == "safe failure"


def six_step_outcome(status: str) -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w",
        "six",
        6,
        "s",
        None if status == "succeeded" else "api_error",
    )


def run_real_segment(
    values: dict[str, object], status: str
) -> tuple[object, dict[str, int], list[tuple[str, tuple[object, ...]]], list[object]]:
    """Real Phase 121 -> 114 -> 107 segment delegating to a synthetic Phase 100 seam."""
    calls = {"phase121": 0, "phase114": 0, "phase107": 0, "seam": 0}
    handoffs: list[tuple[str, tuple[object, ...]]] = []
    seam_values: list[object] = []

    def seam(result: object, workflow: object, state: object, events: object) -> object:
        calls["seam"] += 1
        handoffs.append(("seam", (result, workflow, state, events)))
        seam_values.append(six_step_outcome(status))
        return seam_values[-1]

    def phase107(result: object, workflow: object, state: object, events: object) -> object:
        calls["phase107"] += 1
        handoffs.append(("phase107", (result, workflow, state, events)))
        return route_persisted_transition_outcome_classification_cycle_continuation_boundary(
            result, workflow, state, events, phase100_function=seam  # type: ignore[arg-type]
        )

    def phase114(result: object, workflow: object, state: object, events: object) -> object:
        calls["phase114"] += 1
        handoffs.append(("phase114", (result, workflow, state, events)))
        return route_persisted_transition_outcome_classification_cycle_reentry_continuation_boundary(
            result, workflow, state, events, phase107_function=phase107  # type: ignore[arg-type]
        )

    def phase121(result: object, workflow: object, state: object, events: object) -> object:
        calls["phase121"] += 1
        handoffs.append(("phase121", (result, workflow, state, events)))
        return route_persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary(
            result, workflow, state, events, phase114_function=phase114  # type: ignore[arg-type]
        )

    out = phase121(
        values["result"], values["workflow"], values["state_path"], values["events_path"]
    )
    return out, calls, handoffs, seam_values


def assert_segment_ok(
    values: dict[str, object],
    out: object,
    calls: dict[str, int],
    handoffs: list[tuple[str, tuple[object, ...]]],
    seam_values: list[object],
) -> None:
    # Each route exactly once, return value identity, canonical four-argument
    # order, and no retry (any retry or external execution would add calls).
    assert calls == {"phase121": 1, "phase114": 1, "phase107": 1, "seam": 1}
    assert out is seam_values[0]
    expected = tuple(
        values[key] for key in ("result", "workflow", "state_path", "events_path")
    )
    assert [name for name, _ in handoffs] == ["phase121", "phase114", "phase107", "seam"]
    for _, args in handoffs:
        assert all(
            actual is wanted for actual, wanted in zip(args, expected, strict=True)
        )


def assert_targets_unchanged(
    values: dict[str, object], before: tuple[bytes, bytes]
) -> None:
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_real_segment_synthetic_seam_delegates_once(tmp_path: Path, status: str) -> None:
    values = setup(tmp_path, status)
    reload_and_assert_provenance(values, status)
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    out, calls, handoffs, seam_values = run_real_segment(values, status)
    assert_segment_ok(values, out, calls, handoffs, seam_values)
    assert_targets_unchanged(values, before)
    # Inline next-seam reference (Phase 163 amendment): the same persisted
    # Phase-155 history is still rejected by real Phase 100 before any Phase 93
    # dependency, proving the fallback stays bounded to the 121/114/107 segment.
    phase93_calls = {"phase93": 0}

    def phase93_seam(*_: object) -> object:
        phase93_calls["phase93"] += 1
        pytest.fail("Phase 93 must not be called")

    with pytest.raises(
        PersistedOutcomeClassificationDispatchContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_dispatch_continuation_boundary(
            values["result"],  # type: ignore[arg-type]
            values["workflow"],  # type: ignore[arg-type]
            values["state_path"],  # type: ignore[arg-type]
            values["events_path"],  # type: ignore[arg-type]
            phase93_function=phase93_seam,  # type: ignore[arg-type]
        )
    assert (
        type(caught.value)
        is PersistedOutcomeClassificationDispatchContinuationCompatibilityError
    )
    assert caught.value.detail.classification == "terminal_contract"
    assert phase93_calls == {"phase93": 0}
    assert_targets_unchanged(values, before)


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_real_segment_multiple_earlier_empty_delegates_once(
    tmp_path: Path, status: str
) -> None:
    values = setup(tmp_path, status, earlier_empty=(2, 3))
    reload_and_assert_provenance(values, status, earlier_empty=(2, 3))
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    out, calls, handoffs, seam_values = run_real_segment(values, status)
    assert_segment_ok(values, out, calls, handoffs, seam_values)
    assert_targets_unchanged(values, before)


def test_real_segment_rejects_none_predecessor_output_before_seam(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "succeeded")
    events_path = values["events_path"]
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("two", 2, "other", request_id=None, output_text=None)
    )
    events_path.write_text(lines[0] + replacement + "".join(lines[2:]), encoding="utf-8")  # type: ignore[union-attr]
    before = values["state_path"].read_bytes(), events_path.read_bytes()  # type: ignore[union-attr]
    calls = {"phase114": 0, "phase107": 0, "seam": 0}

    def fail(*_: object) -> object:
        calls["phase114"] += 1
        calls["phase107"] += 1
        calls["seam"] += 1
        pytest.fail("no dependency may be called")

    with pytest.raises(
        PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        route_persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary(
            values["result"],  # type: ignore[arg-type]
            values["workflow"],  # type: ignore[arg-type]
            values["state_path"],  # type: ignore[arg-type]
            values["events_path"],  # type: ignore[arg-type]
            phase114_function=fail,  # type: ignore[arg-type]
        )
    assert (
        type(caught.value)
        is PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationCompatibilityError
    )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == {"phase114": 0, "phase107": 0, "seam": 0}
    assert_targets_unchanged(values, before)


def test_real_segment_rejects_non_string_predecessor_output_before_seam(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "succeeded")
    events_path = values["events_path"]
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    import json

    payload = json.loads(lines[0])
    payload["output_text"] = 1
    lines[0] = json.dumps(payload, separators=(",", ":")) + "\n"
    events_path.write_text("".join(lines), encoding="utf-8")  # type: ignore[union-attr]
    before = values["state_path"].read_bytes(), events_path.read_bytes()  # type: ignore[union-attr]
    calls = {"phase114": 0, "phase107": 0, "seam": 0}

    def fail(*_: object) -> object:
        calls["phase114"] += 1
        calls["phase107"] += 1
        calls["seam"] += 1
        pytest.fail("no dependency may be called")

    with pytest.raises(
        PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        route_persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary(
            values["result"],  # type: ignore[arg-type]
            values["workflow"],  # type: ignore[arg-type]
            values["state_path"],  # type: ignore[arg-type]
            values["events_path"],  # type: ignore[arg-type]
            phase114_function=fail,  # type: ignore[arg-type]
        )
    assert (
        type(caught.value)
        is PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationCompatibilityError
    )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == {"phase114": 0, "phase107": 0, "seam": 0}
    assert_targets_unchanged(values, before)
