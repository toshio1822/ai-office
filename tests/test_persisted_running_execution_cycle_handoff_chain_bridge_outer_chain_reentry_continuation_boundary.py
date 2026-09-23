"""Behavioral tests for the Phase 155 persisted-running execution facade.

The facade is a compatibility surface, but its active execution owner is the
current persisted-start execution primitive.  These tests therefore assert the
observable provider-at-most-once, approval, provenance, and state/event
immutability contracts rather than historical wrapper topology.
"""

# ruff: noqa: E501

from __future__ import annotations

import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr

import ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase155_module
from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.execution_target import LOCAL_OMNIROUTE_EXECUTION_TARGET
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    ModelInvocationSuccess,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesRawHttpResponse,
)
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition

_ERROR = PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError


def workflow(count: int = 8) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "persisted-running execution behavior",
            "steps": [
                {
                    "id": f"step-{index}",
                    "name": f"Step {index}",
                    "employee": "employee",
                    "instructions": f"step instructions {index}",
                }
                for index in range(1, count + 1)
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "employee",
            "name": "Employee",
            "role": "Role",
            "instructions": "employee instructions",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )


def success_event(
    definition: WorkflowDefinition,
    index: int,
    *,
    provider: object = "other",
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
        provider,  # type: ignore[arg-type]
        None,
        f"response-{index}",
        request_id,  # type: ignore[arg-type]
        output_text,  # type: ignore[arg-type]
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


def write_running_history(
    tmp_path: Path,
    definition: WorkflowDefinition,
    *,
    index: int,
    empty_positions: tuple[int, ...] = (),
    none_request_positions: tuple[int, ...] = (),
    provider_overrides: dict[int, object] | None = None,
    event_overrides: dict[int, dict[str, object]] | None = None,
) -> tuple[Path, Path, bytes, bytes, WorkflowExecutionState]:
    provider_overrides = provider_overrides or {}
    event_overrides = event_overrides or {}
    step = definition.steps[index - 1]
    state = WorkflowExecutionState(
        definition.id,
        "running",
        step.id,
        index,
        step.employee,
        tuple(item.id for item in definition.steps[: index - 1]),
        None,
    )
    events: list[RuntimeStepEvent] = []
    for position in range(1, index):
        event = success_event(
            definition,
            position,
            provider=provider_overrides.get(
                position, "openai" if position == index - 1 else "other"
            ),
            request_id=(
                None if position in none_request_positions else f"request-{position}"
            ),
            output_text="" if position in empty_positions else f"output-{position}",
        )
        if position in event_overrides:
            event = replace(event, **event_overrides[position])
        events.append(event)
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8") for event in events
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path = tmp_path / "state.json"
    events_path = tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes, state


def running_case(
    tmp_path: Path,
    *,
    index: int = 6,
    steps: int = 8,
    empty_positions: tuple[int, ...] = (),
    none_request_positions: tuple[int, ...] = (),
    provider_overrides: dict[int, object] | None = None,
    event_overrides: dict[int, dict[str, object]] | None = None,
    provider: str = "openai",
) -> dict[str, object]:
    definition = workflow(steps)
    state_path, events_path, state_bytes, event_bytes, state = write_running_history(
        tmp_path,
        definition,
        index=index,
        empty_positions=empty_positions,
        none_request_positions=none_request_positions,
        provider_overrides=provider_overrides,
        event_overrides=event_overrides,
    )
    person = employee()
    step = definition.steps[index - 1]
    request = ModelInvocationRequest(
        person.model,
        person.instructions,
        step.instructions,
        tuple(person.allowed_tools),
    )
    tools = (ToolDefinition("tool", "Tool", ()),)
    approval = approve_model_invocation_execution(
        request,
        tools,
        provider=provider,
        approved_by="reviewer",
        approval_id="approval-id",
        execution_target=(
            LOCAL_OMNIROUTE_EXECUTION_TARGET if provider == "omniroute" else None
        ),
    )
    return {
        "result": RunningStatePersistenceResult(len(state_bytes)),
        "start": PreparedStepExecutionStart(request, state),
        "workflow": definition,
        "employee": person,
        "state_path": state_path,
        "events_path": events_path,
        "resolved_tools": tools,
        "api_key": OpenAIApiKey(value=SecretStr("synthetic-key")),
        "approval": approval,
        "transport": lambda _: None,
        "before": (state_bytes, event_bytes),
    }


def stop_case(
    tmp_path: Path,
    *,
    status: str,
    index: int = 6,
    terminal_provider: object = "other",
    empty_positions: tuple[int, ...] = (),
) -> tuple[dict[str, object], object]:
    definition = workflow(index)
    step = definition.steps[index - 1]
    completed = (
        tuple(item.id for item in definition.steps)
        if status == "succeeded"
        else tuple(item.id for item in definition.steps[: index - 1])
    )
    state = WorkflowExecutionState(
        definition.id,
        status,
        step.id,
        index,
        step.employee,
        completed,
        None if status == "succeeded" else "api_error",
    )
    events = [
        success_event(
            definition,
            position,
            provider="other",
            output_text="" if position in empty_positions else f"output-{position}",
        )
        for position in range(1, index)
    ]
    if status == "succeeded":
        events.append(
            success_event(
                definition,
                index,
                provider=terminal_provider,
                output_text="final output",
            )
        )
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
        events.append(failure_event(definition, index))
        result = PersistedExecutionOutcome(
            "persisted_failure",
            definition.id,
            step.id,
            index,
            step.employee,
            "api_error",
        )
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8") for event in events
    )
    state_path = tmp_path / "stop-state.json"
    events_path = tmp_path / "stop-events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return (
        {
            "result": result,
            "start": None,
            "workflow": definition,
            "employee": None,
            "state_path": state_path,
            "events_path": events_path,
            "resolved_tools": None,
            "api_key": None,
            "approval": None,
            "transport": None,
            "before": (state_bytes, event_bytes),
        },
        result,
    )


def success_payload(text: str = "synthetic output") -> dict[str, object]:
    return {
        "id": "response-123",
        "object": "response",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }


def success_transport(
    calls: list[OpenAIResponsesAuthenticatedHttpRequest],
    *,
    text: str = "synthetic output",
) -> object:
    def transport(
        request: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        calls.append(request)
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("x-request-id", "request-123"),),
            json.dumps(success_payload(text)).encode("utf-8"),
        )

    return transport


def failure_transport(
    calls: list[OpenAIResponsesAuthenticatedHttpRequest],
) -> object:
    def transport(
        request: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        calls.append(request)
        return OpenAIResponsesRawHttpResponse(
            429,
            "synthetic",
            (),
            json.dumps(
                {
                    "error": {
                        "message": "safe provider failure",
                        "type": "rate_limit_error",
                        "param": None,
                        "code": None,
                    }
                }
            ).encode("utf-8"),
        )

    return transport


def route(case: dict[str, object]) -> object:
    return route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
        case["result"],
        case["start"],
        case["workflow"],
        case["employee"],
        case["state_path"],
        case["events_path"],
        case["resolved_tools"],
        case["api_key"],
        case["approval"],
        case["transport"],
    )  # type: ignore[arg-type]


def assert_classification(callable_object: object, expected: str) -> None:
    with pytest.raises(_ERROR) as caught:
        callable_object()  # type: ignore[operator]
    assert caught.value.detail.classification == expected


def test_public_facade_has_no_historical_execution_injection_seam() -> None:
    parameters = tuple(
        inspect.signature(
            route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
        ).parameters.values()
    )
    assert tuple(parameter.name for parameter in parameters) == (
        "result",
        "start",
        "workflow",
        "employee",
        "state_path",
        "events_path",
        "resolved_tools",
        "api_key",
        "approval",
        "transport",
    )
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for parameter in parameters
    )
    source = Path(phase155_module.__file__).read_text(encoding="utf-8")
    assert "phase141_function" not in source
    assert (
        "route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry"
        not in source
    )


def test_approved_success_has_one_synthetic_transport_attempt_and_preserves_targets(
    tmp_path: Path,
) -> None:
    case = running_case(tmp_path)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    case["transport"] = success_transport(calls)
    before = case["before"]

    result = route(case)

    assert type(result) is StepRuntimeExecutionSuccess
    assert (
        result.workflow_id,
        result.step_id,
        result.step_index,
        result.employee_id,
    ) == ("workflow", "step-6", 6, "employee")
    assert result.invocation_result.provider == "openai"
    assert result.invocation_result.response_id == "response-123"
    assert result.invocation_result.request_id == "request-123"
    assert result.invocation_result.text == "synthetic output"
    assert len(calls) == 1
    assert case["state_path"].read_bytes() == before[0]  # type: ignore[union-attr]
    assert case["events_path"].read_bytes() == before[1]  # type: ignore[union-attr]


def test_approved_provider_failure_is_one_attempt_and_returns_linked_failure(
    tmp_path: Path,
) -> None:
    case = running_case(tmp_path)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    case["transport"] = failure_transport(calls)
    before = case["before"]

    result = route(case)

    assert type(result) is StepRuntimeExecutionFailure
    assert (
        result.workflow_id,
        result.step_id,
        result.step_index,
        result.employee_id,
    ) == ("workflow", "step-6", 6, "employee")
    assert result.invocation_result.category == "api_error"
    assert len(calls) == 1
    assert case["state_path"].read_bytes() == before[0]  # type: ignore[union-attr]
    assert case["events_path"].read_bytes() == before[1]  # type: ignore[union-attr]


def test_omniroute_approval_target_binding_still_allows_one_synthetic_attempt(
    tmp_path: Path,
) -> None:
    case = running_case(tmp_path, provider="omniroute")
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    case["transport"] = success_transport(calls)

    result = route(case)

    assert type(result) is StepRuntimeExecutionSuccess
    assert result.invocation_result.provider == "omniroute"
    assert len(calls) == 1
    assert calls[0].url == LOCAL_OMNIROUTE_EXECUTION_TARGET.base_url


@pytest.mark.parametrize("field", ["approved", "provider", "request_fingerprint"])
def test_invalid_or_mismatched_approval_is_zero_transport_call(
    tmp_path: Path, field: str
) -> None:
    case = running_case(tmp_path)
    approval = case["approval"]
    if field == "approved":
        case["approval"] = replace(approval, approved=False)  # type: ignore[arg-type]
    elif field == "provider":
        case["approval"] = replace(approval, provider="omniroute")  # type: ignore[arg-type]
    else:
        case["approval"] = replace(approval, request_fingerprint="stale")  # type: ignore[arg-type]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    case["transport"] = success_transport(calls)
    before = case["before"]

    assert_classification(lambda: route(case), "approval_contract")
    assert calls == []
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def test_missing_approval_credential_or_transport_is_zero_call(
    tmp_path: Path,
) -> None:
    for field, value, classification in (
        ("approval", None, "approval_contract"),
        ("api_key", object(), "credential_contract"),
        ("transport", object(), "execution_inputs"),
    ):
        case = running_case(tmp_path / field)
        case[field] = value
        calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
        assert_classification(lambda case=case: route(case), classification)
        assert calls == []


def test_request_and_resolved_tools_mismatch_is_rejected_before_transport(
    tmp_path: Path,
) -> None:
    case = running_case(tmp_path)
    start = case["start"]
    assert isinstance(start, PreparedStepExecutionStart)
    case["start"] = replace(
        start,
        request=replace(start.request, model="wrong-model"),
    )
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    case["transport"] = success_transport(calls)

    assert_classification(lambda: route(case), "start_contract")
    assert calls == []


def test_malformed_or_stale_persisted_evidence_is_zero_transport_call(
    tmp_path: Path,
) -> None:
    mutations = (
        ("state", lambda case: case["state_path"].write_bytes(b"not-json")),
        (
            "event",
            lambda case: case["events_path"].write_bytes(b"{malformed}\n"),
        ),
        (
            "linkage",
            lambda case: case["events_path"].write_bytes(
                case["events_path"]
                .read_bytes()  # type: ignore[union-attr]
                .replace(b"step-5", b"stale-step", 1)
            ),
        ),
    )
    for label, mutate in mutations:
        case = running_case(tmp_path / label)
        mutate(case)
        before = (
            case["state_path"].read_bytes(),  # type: ignore[union-attr]
            case["events_path"].read_bytes(),  # type: ignore[union-attr]
        )
        calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
        case["transport"] = success_transport(calls)

        assert_classification(
            lambda case=case: route(case), "persistence_result_contract"
        )
        assert calls == []
        assert (
            case["state_path"].read_bytes(),  # type: ignore[union-attr]
            case["events_path"].read_bytes(),  # type: ignore[union-attr]
        ) == before


@pytest.mark.parametrize(
    ("index", "none_positions", "provider_overrides"),
    [
        (6, (5,), {}),
        (7, (5,), {5: "openai"}),
        (7, (5, 6), {5: "openai", 6: "openai"}),
    ],
)
def test_bounded_legacy_request_id_none_is_accepted_at_current_limits(
    tmp_path: Path,
    index: int,
    none_positions: tuple[int, ...],
    provider_overrides: dict[int, object],
) -> None:
    case = running_case(
        tmp_path,
        index=index,
        none_request_positions=none_positions,
        provider_overrides=provider_overrides,
    )
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    case["transport"] = success_transport(calls)

    result = route(case)

    assert type(result) is StepRuntimeExecutionSuccess
    assert len(calls) == 1


def test_bounded_legacy_request_id_none_below_limit_is_rejected(
    tmp_path: Path,
) -> None:
    case = running_case(
        tmp_path,
        index=6,
        none_request_positions=(4,),
        provider_overrides={4: "openai"},
    )
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    case["transport"] = success_transport(calls)
    before = case["before"]

    assert_classification(lambda: route(case), "persistence_result_contract")
    assert calls == []
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def test_empty_predecessor_outputs_remain_accepted(
    tmp_path: Path,
) -> None:
    case = running_case(tmp_path, empty_positions=(1, 2, 3, 4, 5))
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    case["transport"] = success_transport(calls, text="")

    result = route(case)

    assert type(result) is StepRuntimeExecutionSuccess
    assert result.invocation_result.text == ""
    assert len(calls) == 1


@pytest.mark.parametrize(
    "transport_factory",
    [
        lambda calls: (_ for _ in ()).throw(RuntimeError("secret transport detail")),
        lambda calls: (_ for _ in ()).throw(ValueError("secret transport detail")),
    ],
)
def test_transport_exception_is_sanitized_without_retry_or_mutation(
    tmp_path: Path, transport_factory: object
) -> None:
    case = running_case(tmp_path)
    calls = 0

    def transport(_: OpenAIResponsesAuthenticatedHttpRequest) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret transport detail")

    case["transport"] = transport
    before = case["before"]

    assert_classification(lambda: route(case), "dependency_error")
    assert calls == 1
    assert "secret transport detail" not in str(case)
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_unexpected_owner_mutation_is_compensated_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    case = running_case(tmp_path)
    state = case["state_path"]
    events = case["events_path"]
    before = case["before"]
    calls = 0

    def mutating_owner(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"mutated state")  # type: ignore[union-attr]
        if mutation in {"events", "both"}:
            events.write_bytes(b"mutated events")  # type: ignore[union-attr]
        return runtime_success()

    monkeypatch.setattr(
        phase155_module, "execute_persisted_start_openai_step", mutating_owner
    )
    assert_classification(lambda: route(case), "runtime_contract")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


def test_malformed_owner_return_is_rejected_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = running_case(tmp_path)
    calls = 0

    def malformed_owner(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    monkeypatch.setattr(
        phase155_module, "execute_persisted_start_openai_step", malformed_owner
    )
    assert_classification(lambda: route(case), "runtime_contract")
    assert calls == 1


def test_owner_exception_with_mutation_is_compensated_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = running_case(tmp_path)
    state = case["state_path"]
    events = case["events_path"]
    before = case["before"]
    calls = 0

    def raising_owner(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"mutated state")  # type: ignore[union-attr]
        events.write_bytes(b"mutated events")  # type: ignore[union-attr]
        raise RuntimeError("secret owner detail")

    monkeypatch.setattr(
        phase155_module, "execute_persisted_start_openai_step", raising_owner
    )
    assert_classification(lambda: route(case), "dependency_error")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


def test_restoration_failure_is_safe_and_does_not_retry_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = running_case(tmp_path)
    calls = 0
    restore_calls = 0

    def raising_owner(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("owner failure")

    def failed_restore(*_: object, **__: object) -> None:
        nonlocal restore_calls
        restore_calls += 1
        raise _ERROR("dependency_rollback")

    monkeypatch.setattr(
        phase155_module, "execute_persisted_start_openai_step", raising_owner
    )
    monkeypatch.setattr(phase155_module, "_restore_if_changed", failed_restore)
    assert_classification(lambda: route(case), "dependency_rollback")
    assert calls == 1
    assert restore_calls == 1


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_stop_routes_are_identity_preserving_read_only_and_zero_transport_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    case, result = stop_case(
        tmp_path,
        status=status,
        terminal_provider="other",
        empty_positions=(2, 4),
    )
    before = case["before"]

    def unexpected_owner(*_: object, **__: object) -> object:
        raise AssertionError("stop route must not execute")

    monkeypatch.setattr(
        phase155_module, "execute_persisted_start_openai_step", unexpected_owner
    )
    assert route(case) is result
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def test_stop_route_rejects_non_none_execution_context_without_side_effects(
    tmp_path: Path,
) -> None:
    case, _result = stop_case(tmp_path, status="succeeded")
    case["transport"] = lambda _: None
    before = case["before"]

    assert_classification(lambda: route(case), "execution_inputs")
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def test_invalid_result_is_rejected_before_any_provider_attempt(tmp_path: Path) -> None:
    case = running_case(tmp_path)
    case["result"] = object()
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    case["transport"] = success_transport(calls)

    assert_classification(lambda: route(case), "result_type")
    assert calls == []


def test_persisted_state_and_history_loaders_still_report_exact_evidence(
    tmp_path: Path,
) -> None:
    case = running_case(tmp_path)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    case["transport"] = success_transport(calls)
    result = route(case)
    assert type(result) is StepRuntimeExecutionSuccess
    assert len(calls) == 1
    state = load_workflow_execution_state(case["state_path"])  # type: ignore[arg-type]
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(case["state_path"], case["events_path"])  # type: ignore[arg-type]
    )
    assert state == case["start"].running_state  # type: ignore[union-attr]
    assert history.state == state
    assert len(history.events) == state.current_step_index - 1


def runtime_success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "workflow",
        "step-6",
        6,
        "employee",
        ModelInvocationSuccess(
            "openai", "response", "request", "completed", ("ok",), "ok"
        ),
    )


def test_runtime_failure_model_remains_a_safe_linked_result() -> None:
    result = StepRuntimeExecutionFailure(
        "workflow",
        "step-6",
        6,
        "employee",
        ModelInvocationFailure(
            "openai", "transport_error", "safe", None, None, None, None
        ),
    )
    assert result.invocation_result.category == "transport_error"
    assert result.step_index == 6
