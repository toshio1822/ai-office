"""Phase 151 lower-segment regression: empty succeeded predecessor output through the real Phase 105 → 98 → 91 chain.

The synthetic Phase 84 seam is the only stubbed boundary, so this proves the three
real boundaries accept an exact empty built-in ``str`` predecessor ``output_text`` and
return the synthetic runtime result unchanged, without any real provider/network/tool
execution. Phase 84 itself remains strict (next explicit seam, out of scope for
Phase 151).
"""

# ruff: noqa: E501

from pathlib import Path

from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedRunningExecutionCycleContinuationCompatibilityError,
    PreparedStepExecutionStart,
    route_persisted_running_execution_cycle_continuation_boundary,
    route_persisted_running_execution_dispatch_continuation_boundary,
    route_persisted_running_execution_dispatch_phase_bridge_cycle_reentry_continuation,
)
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    ModelInvocationSuccess,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition

_STEP_IDS = ("one", "two", "three", "four", "five", "six")
_STEP_NAMES = ("One", "Two", "Three", "Four", "Five", "Six")
_STEP_INSTRUCTIONS = ("a", "b", "c", "d", "e", "f")
_CANONICAL = ("result", "start", "workflow", "employee", "state_path", "events_path", "resolved_tools", "api_key", "approval", "transport")


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": step_id, "name": name, "employee": "e", "instructions": instructions}
                for step_id, name, instructions in zip(_STEP_IDS, _STEP_NAMES, _STEP_INSTRUCTIONS, strict=True)
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "e",
            "name": "E",
            "role": "R",
            "instructions": "system",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )


def make_inputs(
    tmp_path: Path,
    *,
    empty_step_index: int,
    replacement: tuple[int, object] | None = None,
) -> dict[str, object]:
    """Running state at step six with succeeded history for steps one through five.

    The succeeded predecessor at ``empty_step_index`` (1-based, steps 1..5) uses
    exact ``output_text == ""``; every other output is a non-empty exact string.
    ``replacement`` optionally overrides one predecessor's output (for the
    rejection cases). All request IDs are exact non-empty strings throughout to
    isolate the ``output_text`` repair.
    """
    state_value = WorkflowExecutionState(
        "w",
        "running",
        "six",
        6,
        "e",
        _STEP_IDS[:5],
        None,
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(
        serialize_workflow_execution_state_json(state_value), encoding="utf-8"
    )
    events = []
    for index, step_id in enumerate(_STEP_IDS[:5], start=1):
        if replacement is not None and replacement[0] == index:
            output = replacement[1]
        else:
            output = "" if index == empty_step_index else f"output-{index}"
        events.append(
            RuntimeStepEvent(
                "step_succeeded",
                "w",
                step_id,
                index,
                "e",
                "running",
                "succeeded",
                "openai",
                None,
                f"response-{index}",
                f"request-{index}",
                output,  # type: ignore[arg-type]
                None,
            )
        )
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )
    request = ModelInvocationRequest("model", "system", "f", ("tool",))
    tools = (ToolDefinition("tool", "Tool", ()),)
    return {
        "result": RunningStatePersistenceResult(len(state_path.read_bytes())),
        "start": PreparedStepExecutionStart(request, state_value),
        "workflow": workflow(),
        "employee": employee(),
        "state_path": state_path,
        "events_path": events_path,
        "resolved_tools": tools,
        "api_key": OpenAIApiKey(value=SecretStr("synthetic")),
        "approval": approve_model_invocation_execution(
            request,
            tools,
            provider="openai",
            approved_by="test",
            approval_id="approval-id",
        ),
        "transport": lambda _: None,
    }


def synthetic_success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w",
        "six",
        6,
        "e",
        ModelInvocationSuccess("openai", "response-6", None, "completed", ("ok",), "ok"),
    )


def synthetic_failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w",
        "six",
        6,
        "e",
        ModelInvocationFailure("openai", "api_error", "safe", None, None, None, None),
    )


def _chain(seam: object) -> tuple[object, dict[str, list[tuple[object, ...]]]]:
    """Real Phase 105 → 98 → 91 chain with only the Phase 84 seam stubbed.

    Every real boundary and the seam record the canonical ten positional
    arguments they receive so the test can assert exactly-once identity/order.
    The production boundaries invoke their dependencies positionally, so the
    recorded tuples are the canonical ten arguments in order.
    """
    calls: dict[str, list[tuple[object, ...]]] = {
        "phase105": [],
        "phase98": [],
        "phase91": [],
        "seam": [],
    }

    def seam_wrapper(*args: object, **kwargs: object) -> object:
        calls["seam"].append(args)
        return seam(*args, **kwargs)  # type: ignore[operator]

    def phase91_wrapper(*args: object, **kwargs: object) -> object:
        calls["phase91"].append(args)
        return route_persisted_running_execution_dispatch_phase_bridge_cycle_reentry_continuation(
            *args, **kwargs, phase84_function=seam_wrapper  # type: ignore[arg-type]
        )

    def phase98_wrapper(*args: object, **kwargs: object) -> object:
        calls["phase98"].append(args)
        return route_persisted_running_execution_dispatch_continuation_boundary(
            *args, **kwargs, phase91_function=phase91_wrapper  # type: ignore[arg-type]
        )

    def route(*args: object, **kwargs: object) -> object:
        calls["phase105"].append(args)
        return route_persisted_running_execution_cycle_continuation_boundary(
            *args, **kwargs, phase98_function=phase98_wrapper  # type: ignore[arg-type]
        )

    return route, calls


def _invoke(route: object, values: dict[str, object]) -> object:
    return route(*(values[name] for name in _CANONICAL))  # type: ignore[operator]


def _assert_exactly_once_canonical(
    calls: dict[str, list[tuple[object, ...]]],
    values: dict[str, object],
) -> None:
    canonical = tuple(values[name] for name in _CANONICAL)
    for name in ("phase105", "phase98", "phase91", "seam"):
        assert len(calls[name]) == 1, name
        assert len(calls[name][0]) == 10, name
        assert all(
            left is right
            for left, right in zip(calls[name][0], canonical, strict=True)
        ), name


def _assert_targets_unchanged(values: dict[str, object], before: tuple[bytes, bytes]) -> None:
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert state_path.read_bytes() == before[0]  # type: ignore[union-attr]
    assert events_path.read_bytes() == before[1]  # type: ignore[union-attr]


def test_earlier_empty_predecessor_output_passes_real_chain_and_returns_success_unchanged(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path, empty_step_index=2)
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    expected = synthetic_success()
    transport_calls = 0

    def transport(_: object) -> object:
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("transport must not be called")

    values["transport"] = transport
    route, calls = _chain(lambda *_: expected)
    result = _invoke(route, values)
    assert result is expected
    _assert_exactly_once_canonical(calls, values)
    _assert_targets_unchanged(values, before)
    assert transport_calls == 0


def test_immediate_empty_predecessor_output_passes_real_chain_and_returns_success_unchanged(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path, empty_step_index=5)
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    expected = synthetic_success()
    transport_calls = 0

    def transport(_: object) -> object:
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("transport must not be called")

    values["transport"] = transport
    route, calls = _chain(lambda *_: expected)
    result = _invoke(route, values)
    assert result is expected
    _assert_exactly_once_canonical(calls, values)
    _assert_targets_unchanged(values, before)
    assert transport_calls == 0


def test_earlier_empty_predecessor_output_passes_real_chain_and_returns_failure_unchanged(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path, empty_step_index=3)
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    expected = synthetic_failure()
    transport_calls = 0

    def transport(_: object) -> object:
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("transport must not be called")

    values["transport"] = transport
    route, calls = _chain(lambda *_: expected)
    result = _invoke(route, values)
    assert result is expected
    _assert_exactly_once_canonical(calls, values)
    _assert_targets_unchanged(values, before)
    assert transport_calls == 0


def test_immediate_empty_predecessor_output_passes_real_chain_and_returns_failure_unchanged(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path, empty_step_index=5)
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    expected = synthetic_failure()
    transport_calls = 0

    def transport(_: object) -> object:
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("transport must not be called")

    values["transport"] = transport
    route, calls = _chain(lambda *_: expected)
    result = _invoke(route, values)
    assert result is expected
    _assert_exactly_once_canonical(calls, values)
    _assert_targets_unchanged(values, before)
    assert transport_calls == 0


def test_non_string_predecessor_output_still_rejected_before_real_chain(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path, empty_step_index=2, replacement=(2, 123))
    route, calls = _chain(lambda *_: synthetic_success())
    try:
        _invoke(route, values)
    except PersistedRunningExecutionCycleContinuationCompatibilityError as caught:
        assert caught.detail.classification == "persistence_result_contract"
    else:
        raise AssertionError("expected persistence_result_contract rejection")
    assert calls["phase98"] == [] and calls["phase91"] == [] and calls["seam"] == []


def test_none_predecessor_output_still_rejected_before_real_chain(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path, empty_step_index=2, replacement=(2, None))
    route, calls = _chain(lambda *_: synthetic_success())
    try:
        _invoke(route, values)
    except PersistedRunningExecutionCycleContinuationCompatibilityError as caught:
        assert caught.detail.classification == "persistence_result_contract"
    else:
        raise AssertionError("expected persistence_result_contract rejection")
    assert calls["phase98"] == [] and calls["phase91"] == [] and calls["seam"] == []
