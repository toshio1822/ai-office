"""Exhaustive Phase 49 routing tests using injected Phase 42 fakes only."""
# ruff: noqa: E501

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedRunningExecutionBridgeCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_persisted_running_execution_bridge_reentry,
)
from ai_office.engine.persisted_running_execution_routing_reentry import (
    PersistedRunningExecutionRoutingCompatibilityError,
)
from ai_office.invocation import (
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


class _ResultSubclass(RunningStatePersistenceResult):
    pass


class _WorkflowSubclass(WorkflowDefinition):
    pass


class _EmployeeSubclass(EmployeeDefinition):
    pass


class _StartSubclass(PreparedStepExecutionStart):
    pass


class _RequestSubclass(ModelInvocationRequest):
    pass


class _StateSubclass(WorkflowExecutionState):
    pass


class _ToolSubclass(ToolDefinition):
    pass


class _SuccessSubclass(StepRuntimeExecutionSuccess):
    pass


class _FailureSubclass(StepRuntimeExecutionFailure):
    pass


def test_running_result_delegates_exact_objects_once(tmp_path: Path) -> None:
    workflow = WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "task"}
            ],
        }
    )
    employee = EmployeeDefinition.model_validate(
        {
            "id": "e",
            "name": "E",
            "role": "R",
            "instructions": "system",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )
    request = ModelInvocationRequest("model", "system", "task", ("tool",))
    start = PreparedStepExecutionStart(
        request, WorkflowExecutionState("w", "running", "one", 1, "e", (), None)
    )
    tools = (ToolDefinition("tool", "Tool", ()),)
    approval = approve_model_invocation_execution(
        request, tools, provider="openai", approved_by="test", approval_id="id"
    )
    key = OpenAIApiKey(value=SecretStr("synthetic"))
    state, events = tmp_path / "state", tmp_path / "events"
    state.write_text(serialize_workflow_execution_state_json(start.running_state))
    events.write_bytes(b"")
    persisted = RunningStatePersistenceResult(len(state.read_bytes()))
    expected = StepRuntimeExecutionSuccess(
        "w",
        "one",
        1,
        "e",
        ModelInvocationSuccess("openai", "response", None, "completed", ("ok",), "ok"),
    )
    calls = 0

    def phase42(*args: object) -> StepRuntimeExecutionSuccess:
        nonlocal calls
        calls += 1
        assert args == (
            persisted,
            start,
            workflow,
            employee,
            state,
            events,
            tools,
            key,
            approval,
            transport,
        )
        return expected

    def transport(_: object) -> object:
        raise AssertionError("transport must not run")

    assert (
        route_persisted_running_execution_bridge_reentry(
            persisted,
            start,
            workflow,
            employee,
            state,
            events,
            tools,
            key,
            approval,
            transport,
            execution_routing_function=phase42,
        )
        is expected
    )
    assert calls == 1


def test_running_history_rejects_extra_event_before_phase42(tmp_path: Path) -> None:
    workflow = WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "task"}
            ],
        }
    )
    employee = EmployeeDefinition.model_validate(
        {
            "id": "e",
            "name": "E",
            "role": "R",
            "instructions": "system",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )
    request = ModelInvocationRequest("model", "system", "task", ("tool",))
    start = PreparedStepExecutionStart(
        request, WorkflowExecutionState("w", "running", "one", 1, "e", (), None)
    )
    tools = (ToolDefinition("tool", "Tool", ()),)
    approval = approve_model_invocation_execution(
        request, tools, provider="openai", approved_by="test", approval_id="id"
    )
    state, events = tmp_path / "state", tmp_path / "events"
    state.write_text(serialize_workflow_execution_state_json(start.running_state))
    events.write_bytes(b"extra")
    with pytest.raises(PersistedRunningExecutionBridgeCompatibilityError) as caught:
        route_persisted_running_execution_bridge_reentry(
            RunningStatePersistenceResult(len(state.read_bytes())),
            start,
            workflow,
            employee,
            state,
            events,
            tools,
            OpenAIApiKey(value=SecretStr("synthetic")),
            approval,
            lambda _: None,
            execution_routing_function=lambda *_: pytest.fail("called"),
        )
    assert caught.value.detail.classification == "persistence_contract"


def _workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "a"},
                {"id": "two", "name": "Two", "employee": "e", "instructions": "b"},
            ],
        }
    )


def _employee() -> EmployeeDefinition:
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


def _event(status: str, index: int) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "w",
        "one" if index == 1 else "two",
        index,
        "e",
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None,
        "request",
        "output" if status == "succeeded" else None,
        None if status == "succeeded" else "safe failure",
    )  # type: ignore[arg-type]


def _write_targets(tmp_path: Path, status: str = "running") -> tuple[Path, Path]:
    index = 2 if status == "running" else 2
    completed = ("one",) if status in {"running", "failed"} else ("one", "two")
    state = WorkflowExecutionState(
        "w",
        status,
        "two",
        index,
        "e",
        completed,
        None if status != "failed" else "api_error",
    )  # type: ignore[arg-type]
    events = [_event("succeeded", 1)]
    if status != "running":
        events.append(_event(status, 2))
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(item) for item in events)
    )
    return state_path, events_path


def _running_inputs(tmp_path: Path) -> dict[str, object]:
    state, events = _write_targets(tmp_path)
    workflow, employee = _workflow(), _employee()
    request = ModelInvocationRequest("model", "system", "b", ("tool",))
    start = PreparedStepExecutionStart(
        request, WorkflowExecutionState("w", "running", "two", 2, "e", ("one",), None)
    )
    tools = (ToolDefinition("tool", "Tool", ()),)
    approval = approve_model_invocation_execution(
        request, tools, provider="openai", approved_by="test", approval_id="id"
    )
    return {
        "result": RunningStatePersistenceResult(len(state.read_bytes())),
        "start": start,
        "workflow": workflow,
        "employee": employee,
        "state_path": state,
        "events_path": events,
        "resolved_tools": tools,
        "api_key": OpenAIApiKey(value=SecretStr("synthetic")),
        "approval": approval,
        "transport": lambda _: None,
    }


def _success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w",
        "two",
        2,
        "e",
        ModelInvocationSuccess("openai", "response", None, "completed", ("ok",), "ok"),
    )


def _failure() -> StepRuntimeExecutionFailure:
    from ai_office.invocation import ModelInvocationFailure

    return StepRuntimeExecutionFailure(
        "w",
        "two",
        2,
        "e",
        ModelInvocationFailure("openai", "api_error", "safe", None, None, None, None),
    )


def _call(values: dict[str, object], dependency: object) -> object:
    return route_persisted_running_execution_bridge_reentry(
        **values,
        execution_routing_function=dependency,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("expected", [_success(), _failure()])
def test_running_route_returns_exact_phase42_result_and_preserves_targets(
    tmp_path: Path, expected: object
) -> None:
    values, calls = _running_inputs(tmp_path), []
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]

    def phase42(*args: object) -> object:
        calls.append(args)
        return expected

    assert _call(values, phase42) is expected
    assert len(calls) == 1
    assert all(
        actual is values[name]
        for actual, name in zip(
            calls[0],
            (
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
            ),
            strict=True,
        )
    )
    assert (
        values["state_path"].read_bytes(),
        values["events_path"].read_bytes(),
    ) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", ["complete", "failure"])
@pytest.mark.parametrize(
    "input_name",
    ["start", "employee", "resolved_tools", "api_key", "approval", "transport"],
)
def test_terminal_routes_require_every_execution_input_to_be_none(
    tmp_path: Path, kind: str, input_name: str
) -> None:
    values = _running_inputs(tmp_path)
    state, events = _write_targets(
        tmp_path, "succeeded" if kind == "complete" else "failed"
    )
    values.update(
        {
            "state_path": state,
            "events_path": events,
            "result": (
                WorkflowProgressionDecision(
                    "workflow_complete",
                    "w",
                    "two",
                    2,
                    "e",
                    None,
                    None,
                    None,
                    "last_step_succeeded",
                )
                if kind == "complete"
                else PersistedExecutionOutcome(
                    "persisted_failure", "w", "two", 2, "e", "api_error"
                )
            ),
        }
    )
    supplied = values["result"]
    before, calls = (state.read_bytes(), events.read_bytes()), 0
    values[input_name] = object()

    def phase42(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedRunningExecutionBridgeCompatibilityError) as caught:
        _call(values, phase42)
    assert caught.value.detail.classification == "execution_inputs" and calls == 0
    assert (state.read_bytes(), events.read_bytes()) == before and supplied is values[
        "result"
    ]


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_terminal_routes_are_exact_read_only_stops(tmp_path: Path, kind: str) -> None:
    state, events = _write_targets(
        tmp_path, "succeeded" if kind == "complete" else "failed"
    )
    result: object = (
        WorkflowProgressionDecision(
            "workflow_complete",
            "w",
            "two",
            2,
            "e",
            None,
            None,
            None,
            "last_step_succeeded",
        )
        if kind == "complete"
        else PersistedExecutionOutcome(
            "persisted_failure", "w", "two", 2, "e", "api_error"
        )
    )
    values = dict(
        result=result,
        start=None,
        workflow=_workflow(),
        employee=None,
        state_path=state,
        events_path=events,
        resolved_tools=None,
        api_key=None,
        approval=None,
        transport=None,
    )
    before = state.read_bytes(), events.read_bytes()
    assert _call(values, lambda *_: pytest.fail("Phase 42 must not run")) is result
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "field, value, classification",
    [
        ("result", object(), "result_type"),
        ("workflow", SimpleNamespace(), "workflow_definition"),
        ("state_path", "state", "state_target"),
        ("events_path", "events", "event_target"),
        ("resolved_tools", [], "execution_inputs"),
        ("api_key", object(), "execution_inputs"),
        ("approval", object(), "execution_inputs"),
        ("transport", object(), "execution_inputs"),
        ("execution_routing_function", object(), "execution_contract"),
    ],
)
def test_prevalidation_rejections_do_not_call_write_or_disclose(
    tmp_path: Path, field: str, value: object, classification: str
) -> None:
    values, calls = _running_inputs(tmp_path), 0
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]

    def fail_if_called(*_: object) -> object:
        raise AssertionError

    if field == "execution_routing_function":
        dependency = value
    else:
        values[field] = value
        dependency = fail_if_called
    with pytest.raises(PersistedRunningExecutionBridgeCompatibilityError) as caught:
        _call(values, dependency)
    assert caught.value.detail.classification == classification
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]
    assert (
        "synthetic" not in str(caught.value)
        and "system" not in str(caught.value)
        and calls == 0
    )


@pytest.mark.parametrize(
    "field", ["workflow_id", "step_id", "step_index", "employee_id"]
)
@pytest.mark.parametrize("expected", [_success(), _failure()])
def test_phase42_result_identity_contract_rejection(
    tmp_path: Path, field: str, expected: object
) -> None:
    values = _running_inputs(tmp_path)
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    bad = replace(expected, **{field: 0 if field == "step_index" else "wrong"})
    calls = 0

    def phase42(*_: object) -> object:
        nonlocal calls
        calls += 1
        return bad

    with pytest.raises(PersistedRunningExecutionBridgeCompatibilityError) as caught:
        _call(values, phase42)
    assert caught.value.detail.classification == "execution_contract" and calls == 1
    assert (
        values["state_path"].read_bytes(),
        values["events_path"].read_bytes(),
    ) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
def test_phase42_target_mutations_are_compensated(
    tmp_path: Path, target: str, operation: str
) -> None:
    values, calls = _running_inputs(tmp_path), 0
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]

    def mutate(path: Path) -> None:
        if operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        elif operation == "append":
            path.write_bytes(path.read_bytes() + b"changed")
        else:
            path.write_bytes(b"changed")

    def phase42(*_: object) -> object:
        nonlocal calls
        calls += 1
        if target in {"state", "both"}:
            mutate(state)  # type: ignore[arg-type]
        if target in {"events", "both"}:
            mutate(events)  # type: ignore[arg-type]
        return _success()

    with pytest.raises(PersistedRunningExecutionBridgeCompatibilityError) as caught:
        _call(values, phase42)
    assert caught.value.detail.classification == "execution_contract" and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("error_kind", ["safe", "unexpected"])
@pytest.mark.parametrize("target", ["none", "state", "events", "both"])
def test_phase42_errors_are_sanitized_or_preserved_and_compensated(
    tmp_path: Path, error_kind: str, target: str
) -> None:
    values, calls = _running_inputs(tmp_path), 0
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    safe = PersistedRunningExecutionRoutingCompatibilityError("persistence_contract")

    def phase42(*_: object) -> object:
        nonlocal calls
        calls += 1
        if target in {"state", "both"}:
            state.write_bytes(b"api-key synthetic output /secret")  # type: ignore[union-attr]
        if target in {"events", "both"}:
            events.write_bytes(b"approval request response")  # type: ignore[union-attr]
        raise (
            safe
            if error_kind == "safe"
            else RuntimeError("/secret synthetic transport output")
        )

    expected_type = (
        PersistedRunningExecutionRoutingCompatibilityError
        if error_kind == "safe"
        else PersistedRunningExecutionBridgeCompatibilityError
    )
    with pytest.raises(expected_type) as caught:
        _call(values, phase42)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]
    if error_kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification == "dependency_error"
        assert all(
            secret not in str(caught.value)
            for secret in ("/secret", "synthetic", "output", "transport")
        )


@pytest.mark.parametrize("outcome", ["return", "safe", "unexpected"])
@pytest.mark.parametrize("failures", [("state",), ("events",), ("state", "events")])
def test_rollback_failure_attempts_both_targets_and_overrides_dependency_outcome(
    tmp_path: Path, outcome: str, failures: tuple[str, ...]
) -> None:
    values = _running_inputs(tmp_path)
    state, events = values["state_path"], values["events_path"]
    original_write, attempts = Path.write_bytes, []
    safe = PersistedRunningExecutionRoutingCompatibilityError("persistence_contract")

    def phase42(*_: object) -> object:
        original_write(state, b"changed")  # type: ignore[arg-type]
        original_write(events, b"changed")  # type: ignore[arg-type]
        if outcome == "safe":
            raise safe
        if outcome == "unexpected":
            raise RuntimeError("secret /path output")
        return _success()

    def fail_restore(path: Path, data: bytes) -> int:
        attempts.append(path)
        if (path is state and "state" in failures) or (
            path is events and "events" in failures
        ):
            raise OSError("secret path")
        return original_write(path, data)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", fail_restore)
        with pytest.raises(PersistedRunningExecutionBridgeCompatibilityError) as caught:
            _call(values, phase42)
    assert caught.value.detail.classification == "dependency_rollback"
    assert state in attempts and events in attempts
    assert all(
        secret not in str(caught.value) for secret in ("secret", "path", "output")
    )


@pytest.mark.parametrize(
    "name, make",
    [
        (
            "result subclass",
            lambda values: _ResultSubclass(values["result"].state_bytes_written),
        ),
        ("result substitute", lambda _: SimpleNamespace(state_bytes_written=1)),
        (
            "workflow subclass",
            lambda values: _WorkflowSubclass(**values["workflow"].__dict__),
        ),
        ("workflow substitute", lambda _: SimpleNamespace(id="w", steps=())),
        (
            "employee subclass",
            lambda values: _EmployeeSubclass(**values["employee"].__dict__),
        ),
        ("employee substitute", lambda _: SimpleNamespace(id="e")),
        ("start subclass", lambda values: _StartSubclass(**values["start"].__dict__)),
        (
            "start substitute",
            lambda _: SimpleNamespace(request=None, running_state=None),
        ),
        (
            "request subclass",
            lambda values: _RequestSubclass(**values["start"].request.__dict__),
        ),
        (
            "state subclass",
            lambda values: _StateSubclass(**values["start"].running_state.__dict__),
        ),
        (
            "tool subclass",
            lambda values: (_ToolSubclass(**values["resolved_tools"][0].__dict__),),
        ),
        ("tool substitute", lambda _: (SimpleNamespace(name="tool"),)),
    ],
)
def test_exact_input_types_reject_subclasses_and_structural_substitutes(
    tmp_path: Path, name: str, make: object
) -> None:
    values = _running_inputs(tmp_path)
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    value = make(values)  # type: ignore[operator]
    if name.startswith("result"):
        values["result"] = value
        expected = "result_type"
    elif name.startswith("workflow"):
        values["workflow"] = value
        expected = "workflow_definition"
    elif name.startswith("employee"):
        values["employee"] = value
        expected = "execution_inputs"
    elif name.startswith("start"):
        values["start"] = value
        expected = "execution_inputs"
    elif name.startswith("request"):
        values["start"] = PreparedStepExecutionStart(
            value, values["start"].running_state
        )  # type: ignore[arg-type,union-attr]
        expected = "execution_inputs"
    elif name.startswith("state"):
        values["start"] = PreparedStepExecutionStart(values["start"].request, value)  # type: ignore[arg-type,union-attr]
        expected = "execution_inputs"
    else:
        values["resolved_tools"] = value
        expected = "execution_inputs"
    with pytest.raises(PersistedRunningExecutionBridgeCompatibilityError) as caught:
        _call(values, lambda *_: pytest.fail("Phase 42 must not run"))
    assert caught.value.detail.classification == expected
    assert (
        values["state_path"].read_bytes(),
        values["events_path"].read_bytes(),
    ) == before  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "result",
    [
        object(),
        _SuccessSubclass(**_success().__dict__),
        _FailureSubclass(**_failure().__dict__),
        SimpleNamespace(workflow_id="w"),
        replace(_success(), invocation_result=object()),
        replace(_failure(), invocation_result=object()),
    ],
)
def test_phase42_rejects_exact_type_and_malformed_return_contracts(
    tmp_path: Path, result: object
) -> None:
    values, calls = _running_inputs(tmp_path), 0
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]

    def phase42(*_: object) -> object:
        nonlocal calls
        calls += 1
        return result

    with pytest.raises(PersistedRunningExecutionBridgeCompatibilityError) as caught:
        _call(values, phase42)
    assert caught.value.detail.classification == "execution_contract" and calls == 1
    assert (
        values["state_path"].read_bytes(),
        values["events_path"].read_bytes(),
    ) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("field", ["response_id", "output_text"])
@pytest.mark.parametrize("route", ["running", "terminal"])
def test_event_success_response_and_output_contract(
    tmp_path: Path, field: str, route: str
) -> None:
    """Empty response_id stays rejected; empty output_text is accepted on the
    running route (empty-success compatibility) but stays rejected on the
    terminal route (shared terminal-history contract)."""
    status = "running" if route == "running" else "succeeded"
    state, events = _write_targets(tmp_path, status)
    events.write_text(
        "".join(
            serialize_runtime_step_event_jsonl(
                replace(_event("succeeded", index), **{field: ""})
                if index == 1
                else _event("succeeded", index)
            )
            for index in (1,)
            if route == "running"
        )
        if route == "running"
        else "".join(
            serialize_runtime_step_event_jsonl(
                replace(_event("succeeded", index), **{field: ""})
                if index == 1
                else _event("succeeded", index)
            )
            for index in (1, 2)
        )
    )
    if route == "running":
        inputs_path = tmp_path / "inputs"
        inputs_path.mkdir()
        values = _running_inputs(inputs_path)
        values.update(state_path=state, events_path=events)
        before = state.read_bytes(), events.read_bytes()
        if field == "output_text":
            calls: list[tuple[object, ...]] = []
            expected = _success()

            def phase42(*args: object) -> object:
                calls.append(args)
                return expected

            returned = _call(values, phase42)
            assert returned is expected and len(calls) == 1
            assert (state.read_bytes(), events.read_bytes()) == before
        else:
            with pytest.raises(
                PersistedRunningExecutionBridgeCompatibilityError
            ) as caught:
                _call(values, lambda *_: pytest.fail("Phase 42 must not run"))
            assert caught.value.detail.classification == "persistence_contract"
    else:
        result = WorkflowProgressionDecision(
            "workflow_complete",
            "w",
            "two",
            2,
            "e",
            None,
            None,
            None,
            "last_step_succeeded",
        )
        values = dict(
            result=result,
            start=None,
            workflow=_workflow(),
            employee=None,
            state_path=state,
            events_path=events,
            resolved_tools=None,
            api_key=None,
            approval=None,
            transport=None,
        )
        with pytest.raises(
            PersistedRunningExecutionBridgeCompatibilityError
        ) as caught:
            _call(values, lambda *_: pytest.fail("Phase 42 must not run"))
        assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize("output", ["", None, 123, 1.5])
def test_succeeded_predecessor_empty_output_is_accepted_and_non_string_rejected(
    tmp_path: Path, output: object
) -> None:
    supplied = _running_inputs(tmp_path)
    events = supplied["events_path"]
    events.write_text(
        serialize_runtime_step_event_jsonl(
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
                "response",
                "request",
                output,  # type: ignore[arg-type]
                None,
            )
        )
    )
    calls = 0
    expected = _success()

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    if output == "":
        returned = _call(supplied, dependency)
        assert returned is expected and calls == 1
    else:
        with pytest.raises(PersistedRunningExecutionBridgeCompatibilityError) as caught:
            _call(supplied, dependency)
        assert caught.value.detail.classification == "persistence_contract"
        assert calls == 0


def _continuation(tmp_path: Path) -> dict[str, object]:
    """Running at step three with succeeded steps one and two (two predecessors)."""
    wf = WorkflowDefinition.model_validate(
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
    state = WorkflowExecutionState(
        "w", "running", "three", 3, "e", ("one", "two"), None
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(
        serialize_runtime_step_event_jsonl(
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
                "response",
                "request",
                "o",
                None,
            )
        )
        + serialize_runtime_step_event_jsonl(
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
                "response2",
                "request2",
                "o2",
                None,
            )
        )
    )
    request = ModelInvocationRequest("model", "system", "c", ("tool",))
    tools = (ToolDefinition("tool", "Tool", ()),)
    return {
        "result": RunningStatePersistenceResult(len(state_path.read_bytes())),
        "start": PreparedStepExecutionStart(request, state),
        "workflow": wf,
        "employee": _employee(),
        "state_path": state_path,
        "events_path": events_path,
        "resolved_tools": tools,
        "api_key": OpenAIApiKey(value=SecretStr("synthetic")),
        "approval": approve_model_invocation_execution(
            request,
            tools,
            provider="openai",
            approved_by="test",
            approval_id="id",
        ),
        "transport": lambda _: None,
    }


def _runtime_three() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w",
        "three",
        3,
        "e",
        ModelInvocationSuccess("openai", "response", None, "completed", ("ok",), "ok"),
    )


def _assert_canonical_identity_order(
    calls: list[tuple[object, ...]], supplied: dict[str, object]
) -> None:
    assert len(calls) == 1
    assert all(
        left is right
        for left, right in zip(
            calls[0],
            (
                supplied["result"],
                supplied["start"],
                supplied["workflow"],
                supplied["employee"],
                supplied["state_path"],
                supplied["events_path"],
                supplied["resolved_tools"],
                supplied["api_key"],
                supplied["approval"],
                supplied["transport"],
            ),
            strict=True,
        )
    )


def test_earlier_succeeded_predecessor_empty_output_delegates_exactly_once_in_canonical_identity_order(
    tmp_path: Path,
) -> None:
    supplied = _continuation(tmp_path)
    events = supplied["events_path"]
    first = serialize_runtime_step_event_jsonl(
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
            "response",
            "request",
            "",
            None,
        )
    )
    second = serialize_runtime_step_event_jsonl(
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
            "response2",
            "request2",
            "o2",
            None,
        )
    )
    events.write_text(first + second)
    state_before = supplied["state_path"].read_bytes()
    events_before = events.read_bytes()
    transport_calls = 0

    def transport(_: object) -> object:
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("transport must not be called")

    supplied["transport"] = transport
    calls: list[tuple[object, ...]] = []
    expected = _runtime_three()

    def dependency(*args: object) -> object:
        calls.append(args)
        return expected

    returned = _call(supplied, dependency)
    assert returned is expected
    _assert_canonical_identity_order(calls, supplied)
    assert supplied["state_path"].read_bytes() == state_before
    assert events.read_bytes() == events_before
    assert transport_calls == 0


def test_immediate_succeeded_predecessor_empty_output_delegates_exactly_once_in_canonical_identity_order(
    tmp_path: Path,
) -> None:
    supplied = _continuation(tmp_path)
    events = supplied["events_path"]
    first = serialize_runtime_step_event_jsonl(
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
            "response",
            "request",
            "o",
            None,
        )
    )
    second = serialize_runtime_step_event_jsonl(
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
            "response2",
            "request2",
            "",
            None,
        )
    )
    events.write_text(first + second)
    state_before = supplied["state_path"].read_bytes()
    events_before = events.read_bytes()
    transport_calls = 0

    def transport(_: object) -> object:
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("transport must not be called")

    supplied["transport"] = transport
    calls: list[tuple[object, ...]] = []
    expected = _runtime_three()

    def dependency(*args: object) -> object:
        calls.append(args)
        return expected

    returned = _call(supplied, dependency)
    assert returned is expected
    _assert_canonical_identity_order(calls, supplied)
    assert supplied["state_path"].read_bytes() == state_before
    assert events.read_bytes() == events_before
    assert transport_calls == 0
