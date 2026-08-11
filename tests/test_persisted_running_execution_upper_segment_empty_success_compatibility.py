"""Phase 150 upper-segment regression: empty succeeded predecessor output through the real Phase 126 → 119 → 112 chain.

The synthetic Phase 105 seam is the only stubbed boundary, so this proves the three
real boundaries accept an exact empty built-in ``str`` predecessor ``output_text`` and
return the synthetic runtime result unchanged, without any real provider/network/tool
execution. Phase 105 itself remains strict (next explicit seam, out of scope).
"""

# ruff: noqa: E501

from pathlib import Path

from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError,
    PreparedStepExecutionStart,
    route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary,
    route_persisted_running_execution_cycle_handoff_reentry_continuation_boundary,
    route_persisted_running_execution_cycle_reentry_continuation_boundary,
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


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "a"},
                {"id": "two", "name": "Two", "employee": "e", "instructions": "b"},
                {"id": "three", "name": "Three", "employee": "e", "instructions": "c"},
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


def make_inputs(tmp_path: Path, *, second_output: object = "") -> dict[str, object]:
    """Running state at step three with succeeded history for steps one and two.

    The step-two predecessor uses the supplied ``output_text`` so the test can inject
    an exact empty built-in ``str`` while keeping every other field contract-valid.
    """
    state_value = WorkflowExecutionState(
        "w",
        "running",
        "three",
        3,
        "e",
        ("one", "two"),
        None,
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(
        serialize_workflow_execution_state_json(state_value), encoding="utf-8"
    )
    events = [
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            "one",
            1,
            "e",
            "running",
            "succeeded",
            "openai",
            None,
            "response-1",
            "request-1",
            "first-output",
            None,
        ),
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            "two",
            2,
            "e",
            "running",
            "succeeded",
            "openai",
            None,
            "response-2",
            "request-2",
            second_output,  # type: ignore[arg-type]
            None,
        ),
    ]
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )
    request = ModelInvocationRequest("model", "system", "c", ("tool",))
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
        "three",
        3,
        "e",
        ModelInvocationSuccess("openai", "response-3", None, "completed", ("ok",), "ok"),
    )


def synthetic_failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w",
        "three",
        3,
        "e",
        ModelInvocationFailure("openai", "api_error", "safe", None, None, None, None),
    )


def _chain(seam: object) -> object:
    """Real Phase 126 → 119 → 112 chain with only the Phase 105 seam stubbed."""

    def phase112_with_stub(*args: object, **kwargs: object) -> object:
        return route_persisted_running_execution_cycle_reentry_continuation_boundary(
            *args, **kwargs, phase105_function=seam  # type: ignore[arg-type]
        )

    def phase119_with_stub(*args: object, **kwargs: object) -> object:
        return route_persisted_running_execution_cycle_handoff_reentry_continuation_boundary(
            *args, **kwargs, phase112_function=phase112_with_stub  # type: ignore[arg-type]
        )

    def route(*args: object, **kwargs: object) -> object:
        return route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(
            *args, **kwargs, phase119_function=phase119_with_stub  # type: ignore[arg-type]
        )

    return route


def test_empty_predecessor_output_passes_real_chain_and_returns_success_unchanged(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path)
    expected = synthetic_success()
    result = _chain(lambda *_: expected)(**values)  # type: ignore[arg-type]
    assert result is expected


def test_empty_predecessor_output_passes_real_chain_and_returns_failure_unchanged(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path)
    expected = synthetic_failure()
    result = _chain(lambda *_: expected)(**values)  # type: ignore[arg-type]
    assert result is expected


def test_non_string_predecessor_output_still_rejected_before_real_chain(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path, second_output=123)  # type: ignore[arg-type]
    try:
        _chain(lambda *_: synthetic_success())(**values)  # type: ignore[arg-type]
    except PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError as caught:
        assert caught.detail.classification == "persistence_result_contract"
    else:
        raise AssertionError("expected persistence_result_contract rejection")


def test_none_predecessor_output_still_rejected_before_real_chain(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path, second_output=None)  # type: ignore[arg-type]
    try:
        _chain(lambda *_: synthetic_success())(**values)  # type: ignore[arg-type]
    except PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError as caught:
        assert caught.detail.classification == "persistence_result_contract"
    else:
        raise AssertionError("expected persistence_result_contract rejection")
