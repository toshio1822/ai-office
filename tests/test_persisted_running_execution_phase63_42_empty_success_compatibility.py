"""Phase 153 lower-chain regression: real Phase 63 → 56 → 49 → 42 with a synthetic Phase 36 seam.

Proves the final locally-strict predecessor-history segment accepts an exact empty
built-in ``str`` predecessor ``output_text`` through the real Phase 63, Phase 56,
Phase 49, and Phase 42 boundaries in sequence. The synthetic Phase 36 seam is the
only stubbed boundary, so this proves the four real boundaries accept an exact
empty built-in ``str`` predecessor ``output_text`` and return the synthetic runtime
result unchanged, without any real provider/network/tool execution. Phase 42 and
Phase 36 themselves remain unchanged (Phase 42 never revalidates predecessor event
history; the seam is test-only).
"""

# ruff: noqa: E501

from pathlib import Path

from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedRunningExecutionRoutingPhaseBridgeCompatibilityError,
    PreparedStepExecutionStart,
    route_persisted_running_execution_bridge_reentry,
    route_persisted_running_execution_phase_bridge_reentry,
    route_persisted_running_execution_routing_phase_bridge_reentry,
)
from ai_office.engine.persisted_running_execution_routing_reentry import (
    route_persisted_running_execution_reentry,
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
    """Real Phase 63 → 56 → 49 → 42 chain with only the Phase 36 seam stubbed.

    Every real boundary records the canonical ten positional arguments it
    receives so the test can assert exactly-once identity/order. The seam records
    the exact Phase-42→Phase-36 call shape (seven positional arguments plus the
    ``transport`` keyword) so the test can assert exactly-once identity there too.
    """
    calls: dict[str, list[tuple[object, ...]]] = {
        "phase63": [],
        "phase56": [],
        "phase49": [],
        "phase42": [],
        "seam": [],
    }

    def seam_wrapper(*args: object, **kwargs: object) -> object:
        calls["seam"].append((args, kwargs))
        return seam(*args, **kwargs)  # type: ignore[operator]

    def phase42_wrapper(*args: object, **kwargs: object) -> object:
        calls["phase42"].append(args)
        return route_persisted_running_execution_reentry(
            *args, **kwargs, execution_reentry_function=seam_wrapper  # type: ignore[arg-type]
        )

    def phase49_wrapper(*args: object, **kwargs: object) -> object:
        calls["phase49"].append(args)
        return route_persisted_running_execution_bridge_reentry(
            *args, **kwargs, execution_routing_function=phase42_wrapper  # type: ignore[arg-type]
        )

    def phase56_wrapper(*args: object, **kwargs: object) -> object:
        calls["phase56"].append(args)
        return route_persisted_running_execution_phase_bridge_reentry(
            *args, **kwargs, phase49_function=phase49_wrapper  # type: ignore[arg-type]
        )

    def route(*args: object, **kwargs: object) -> object:
        calls["phase63"].append(args)
        return route_persisted_running_execution_routing_phase_bridge_reentry(
            *args, **kwargs, phase56_function=phase56_wrapper  # type: ignore[arg-type]
        )

    return route, calls


def _invoke(route: object, values: dict[str, object]) -> object:
    return route(*(values[name] for name in _CANONICAL))  # type: ignore[operator]


def _assert_exactly_once_canonical(
    calls: dict[str, list[tuple[object, ...]]],
    values: dict[str, object],
) -> None:
    canonical = tuple(values[name] for name in _CANONICAL)
    for name in ("phase63", "phase56", "phase49", "phase42"):
        assert len(calls[name]) == 1, name
        assert len(calls[name][0]) == 10, name
        assert all(
            left is right
            for left, right in zip(calls[name][0], canonical, strict=True)
        ), name


def _assert_seam_shape(
    calls: dict[str, list[tuple[object, ...]]],
    values: dict[str, object],
) -> None:
    assert len(calls["seam"]) == 1
    args, kwargs = calls["seam"][0]  # type: ignore[misc]
    expected_args = (
        values["start"],
        values["state_path"],
        values["workflow"],
        values["employee"],
        values["resolved_tools"],
        values["api_key"],
        values["approval"],
    )
    assert len(args) == len(expected_args)
    assert all(
        left is right
        for left, right in zip(args, expected_args, strict=True)
    )
    assert kwargs == {"transport": values["transport"]}


def _assert_targets_unchanged(values: dict[str, object], before: tuple[bytes, bytes]) -> None:
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert state_path.read_bytes() == before[0]  # type: ignore[union-attr]
    assert events_path.read_bytes() == before[1]  # type: ignore[union-attr]


def test_earlier_empty_predecessor_output_passes_real_lower_chain_and_returns_success_unchanged(
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
    route, calls = _chain(lambda *_args, **_kwargs: expected)
    result = _invoke(route, values)
    assert result is expected
    _assert_exactly_once_canonical(calls, values)
    _assert_seam_shape(calls, values)
    _assert_targets_unchanged(values, before)
    assert transport_calls == 0


def test_immediate_empty_predecessor_output_passes_real_lower_chain_and_returns_success_unchanged(
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
    route, calls = _chain(lambda *_args, **_kwargs: expected)
    result = _invoke(route, values)
    assert result is expected
    _assert_exactly_once_canonical(calls, values)
    _assert_seam_shape(calls, values)
    _assert_targets_unchanged(values, before)
    assert transport_calls == 0


def test_earlier_empty_predecessor_output_passes_real_lower_chain_and_returns_failure_unchanged(
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
    route, calls = _chain(lambda *_args, **_kwargs: expected)
    result = _invoke(route, values)
    assert result is expected
    _assert_exactly_once_canonical(calls, values)
    _assert_seam_shape(calls, values)
    _assert_targets_unchanged(values, before)
    assert transport_calls == 0


def test_immediate_empty_predecessor_output_passes_real_lower_chain_and_returns_failure_unchanged(
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
    route, calls = _chain(lambda *_args, **_kwargs: expected)
    result = _invoke(route, values)
    assert result is expected
    _assert_exactly_once_canonical(calls, values)
    _assert_seam_shape(calls, values)
    _assert_targets_unchanged(values, before)
    assert transport_calls == 0


def test_non_string_predecessor_output_still_rejected_before_real_lower_chain(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path, empty_step_index=2, replacement=(2, 123))
    route, calls = _chain(lambda *_args, **_kwargs: synthetic_success())
    try:
        _invoke(route, values)
    except PersistedRunningExecutionRoutingPhaseBridgeCompatibilityError as caught:
        assert caught.detail.classification == "persistence_result_contract"
    else:
        raise AssertionError("expected persistence_result_contract rejection")
    assert calls["phase56"] == [] and calls["phase49"] == [] and calls["phase42"] == []
    assert calls["seam"] == []


def test_none_predecessor_output_still_rejected_before_real_lower_chain(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path, empty_step_index=2, replacement=(2, None))
    route, calls = _chain(lambda *_args, **_kwargs: synthetic_success())
    try:
        _invoke(route, values)
    except PersistedRunningExecutionRoutingPhaseBridgeCompatibilityError as caught:
        assert caught.detail.classification == "persistence_result_contract"
    else:
        raise AssertionError("expected persistence_result_contract rejection")
    assert calls["phase56"] == [] and calls["phase49"] == [] and calls["phase42"] == []
    assert calls["seam"] == []
