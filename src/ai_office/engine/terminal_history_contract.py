"""Shared terminal-history invariants for read-only routing boundaries."""

from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
)
from ai_office.storage.workflow_execution_history import WorkflowExecutionLoadError

_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PROVENANCE_PROVIDERS = frozenset({"openai", "omniroute"})
_UNSET = object()


class TerminalHistoryContractError(ValueError):
    """Raised when terminal persisted history violates the shared contract."""


def _load_terminal_history(
    state_path: Path,
    events_path: Path,
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    """Load one exact persisted state/event snapshot without route policy."""
    try:
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except (OSError, WorkflowExecutionLoadError) as error:
        raise TerminalHistoryContractError from error
    if (
        type(history.state) is not WorkflowExecutionState
        or type(history.events) is not tuple
    ):
        raise TerminalHistoryContractError from None
    return history.state, history.events


def load_strict_terminal_history(
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    """Load and validate one terminal workflow state and event history."""
    state, events = _load_terminal_history(state_path, events_path)
    allow_empty_success_output = (
        type(state) is WorkflowExecutionState
        and state.status == "succeeded"
        and type(state.current_step_index) is int
        and state.current_step_index < len(workflow.steps)
    )
    _validate_terminal_history(
        workflow,
        state,
        events,
        allow_empty_success_output=allow_empty_success_output,
        allow_empty_predecessor_output=allow_empty_success_output,
        provider_policy="ignore",
        predecessor_request_id_policy="ignore",
        terminal_request_id_policy="ignore",
        failure_message_policy="string",
    )
    return state, events


def validate_strict_terminal_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    events: tuple[RuntimeStepEvent, ...],
) -> None:
    """Validate the legacy shared terminal-history contract."""
    allow_empty_success_output = (
        type(state) is WorkflowExecutionState
        and state.status == "succeeded"
        and type(state.current_step_index) is int
        and state.current_step_index < len(workflow.steps)
    )
    _validate_terminal_history(
        workflow,
        state,
        events,
        allow_empty_success_output=allow_empty_success_output,
        allow_empty_predecessor_output=allow_empty_success_output,
        provider_policy="ignore",
        predecessor_request_id_policy="ignore",
        terminal_request_id_policy="ignore",
        failure_message_policy="string",
    )


def is_valid_workflow_definition(workflow: object) -> bool:
    """Return whether a workflow is an exact, usable terminal-history schema."""
    if not (
        type(workflow) is WorkflowDefinition
        and _nonempty_string(workflow.id)
        and _nonempty_string(workflow.name)
        and _nonempty_string(workflow.description)
        and type(workflow.steps) is list
        and bool(workflow.steps)
    ):
        return False
    if any(
        type(step) is not WorkflowStepDefinition
        or not _nonempty_string(step.id)
        or not _nonempty_string(step.name)
        or not _nonempty_string(step.employee)
        or not _nonempty_string(step.instructions)
        for step in workflow.steps
    ):
        return False
    step_ids = tuple(step.id for step in workflow.steps)
    return len(step_ids) == len(set(step_ids))


def _validate_terminal_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    events: tuple[RuntimeStepEvent, ...],
    *,
    expected_status: Literal["succeeded", "failed"] | None = None,
    result: object | None = None,
    expected_failure: object = _UNSET,
    minimum_index: int = 1,
    require_immediate_openai: bool = False,
    allow_empty_success_output: bool = False,
    allow_empty_predecessor_output: bool = False,
    provider_policy: Literal["ignore", "nonempty"] = "nonempty",
    predecessor_request_id_policy: Literal[
        "ignore", "required", "optional"
    ] = "required",
    terminal_request_id_policy: Literal["ignore", "optional", "required"] = "optional",
    failure_message_policy: Literal["string", "nonempty"] = "nonempty",
    allow_immediate_none_request_id: bool = False,
    allow_accumulated_none_request_id: bool = False,
    immediate_none_minimum_position: int = 1,
) -> None:
    """Validate shared evidence while preserving route-specific compatibility.

    Workflow/state identity, completed-step prefix, event ordering, event
    identity, terminal status, and failure-category consistency are implemented
    once here. Provider, request-id, empty-output, and failure-message rules
    remain explicit policy inputs so Phase 143 and Phase 144 retain their
    current observable boundaries.
    """
    if (
        not is_valid_workflow_definition(workflow)
        or type(state) is not WorkflowExecutionState
        or type(events) is not tuple
        or not events
        or type(state.status) is not str
        or state.status not in {"succeeded", "failed"}
        or expected_status not in {None, "succeeded", "failed"}
        or (expected_status is not None and state.status != expected_status)
        or type(minimum_index) is not int
        or minimum_index < 1
        or type(immediate_none_minimum_position) is not int
        or immediate_none_minimum_position < 1
        or provider_policy not in {"ignore", "nonempty"}
        or predecessor_request_id_policy not in {"ignore", "required", "optional"}
        or terminal_request_id_policy not in {"ignore", "optional", "required"}
        or failure_message_policy not in {"string", "nonempty"}
    ):
        _invalid()

    index = state.current_step_index
    if not _valid_state(state, workflow) or not (
        minimum_index <= index <= len(workflow.steps)
    ):
        _invalid()
    expected_failure_value = (
        state.last_failure_category if expected_failure is _UNSET else expected_failure
    )
    if state.last_failure_category != expected_failure_value:
        _invalid()
    if result is not None and not _valid_result_identity(result, state):
        _invalid()

    expected_completed = (
        tuple(step.id for step in workflow.steps[:index])
        if state.status == "succeeded"
        else tuple(step.id for step in workflow.steps[: index - 1])
    )
    if state.completed_step_ids != expected_completed:
        _invalid()

    prior_steps = workflow.steps[: index - 1]
    if len(events) != len(prior_steps) + 1 or any(
        type(event) is not RuntimeStepEvent for event in events
    ):
        _invalid()

    last_position = len(prior_steps)
    for position, (event, step) in enumerate(
        zip(events[:-1], prior_steps, strict=True), 1
    ):
        allow_none = (
            allow_immediate_none_request_id
            and position == last_position
            and position >= immediate_none_minimum_position
        ) or (
            allow_accumulated_none_request_id and last_position >= 6 and position >= 5
        )
        if not _valid_predecessor(
            event,
            step,
            position,
            state,
            require_immediate_openai=require_immediate_openai
            and position == last_position,
            allow_empty_output=allow_empty_predecessor_output,
            provider_policy=provider_policy,
            request_id_policy=predecessor_request_id_policy,
            allow_none_request_id=allow_none,
        ):
            _invalid()
        if (
            allow_none
            and event.request_id is None
            and event.provider not in _PROVENANCE_PROVIDERS
        ):
            _invalid()

    if not _valid_terminal_event(
        events[-1],
        state,
        expected_failure_value,
        require_immediate_openai=require_immediate_openai,
        allow_empty_success_output=allow_empty_success_output,
        provider_policy=provider_policy,
        request_id_policy=terminal_request_id_policy,
        failure_message_policy=failure_message_policy,
    ):
        _invalid()


def _valid_state(state: WorkflowExecutionState, workflow: WorkflowDefinition) -> bool:
    index = state.current_step_index
    if type(index) is not int or not 1 <= index <= len(workflow.steps):
        return False
    current = workflow.steps[index - 1]
    return (
        _nonempty_string(state.workflow_id)
        and state.workflow_id == workflow.id
        and type(state.status) is str
        and state.status in {"succeeded", "failed"}
        and _nonempty_string(state.current_step_id)
        and state.current_step_id == current.id
        and _nonempty_string(state.current_employee_id)
        and state.current_employee_id == current.employee
        and type(state.completed_step_ids) is tuple
        and all(_nonempty_string(item) for item in state.completed_step_ids)
        and (
            state.last_failure_category is None
            or (
                type(state.last_failure_category) is str
                and state.last_failure_category in _FAILURE_CATEGORIES
            )
        )
    )


def _valid_result_identity(result: object, state: WorkflowExecutionState) -> bool:
    try:
        return (
            _exact_string(result.workflow_id, state.workflow_id)  # type: ignore[attr-defined]
            and _exact_string(result.current_step_id, state.current_step_id)  # type: ignore[attr-defined]
            and type(result.current_step_index) is int  # type: ignore[attr-defined]
            and result.current_step_index == state.current_step_index  # type: ignore[attr-defined]
            and _exact_string(result.current_employee_id, state.current_employee_id)  # type: ignore[attr-defined]
        )
    except AttributeError:
        return False


def _valid_predecessor(
    event: RuntimeStepEvent,
    step: WorkflowStepDefinition,
    position: int,
    state: WorkflowExecutionState,
    *,
    require_immediate_openai: bool,
    allow_empty_output: bool,
    provider_policy: Literal["ignore", "nonempty"],
    request_id_policy: Literal["ignore", "required", "optional"],
    allow_none_request_id: bool,
) -> bool:
    provider_valid = provider_policy == "ignore" or _nonempty_string(event.provider)
    if require_immediate_openai:
        provider_valid = _nonempty_string(event.provider) and (
            event.provider in _PROVENANCE_PROVIDERS
        )
    request_valid = _request_id_valid(
        event.request_id,
        request_id_policy,
        allow_none_request_id=allow_none_request_id,
    )
    return (
        type(event) is RuntimeStepEvent
        and _exact_string(event.event_type, "step_succeeded")
        and _exact_string(event.workflow_id, state.workflow_id)
        and _exact_string(event.step_id, step.id)
        and type(event.step_index) is int
        and event.step_index == position
        and _exact_string(event.employee_id, step.employee)
        and _exact_string(event.previous_status, "running")
        and _exact_string(event.next_status, "succeeded")
        and provider_valid
        and event.failure_category is None
        and _nonempty_string(event.response_id)
        and request_valid
        and type(event.output_text) is str
        and (allow_empty_output or bool(event.output_text))
        and event.message is None
    )


def _valid_terminal_event(
    event: RuntimeStepEvent,
    state: WorkflowExecutionState,
    expected_failure: object,
    *,
    require_immediate_openai: bool,
    allow_empty_success_output: bool,
    provider_policy: Literal["ignore", "nonempty"],
    request_id_policy: Literal["ignore", "optional", "required"],
    failure_message_policy: Literal["string", "nonempty"],
) -> bool:
    provider_valid = provider_policy == "ignore" or _nonempty_string(event.provider)
    if require_immediate_openai:
        provider_valid = _nonempty_string(event.provider) and (
            event.provider in _PROVENANCE_PROVIDERS
        )
    request_valid = _request_id_valid(event.request_id, request_id_policy)
    base = (
        type(event) is RuntimeStepEvent
        and _exact_string(event.workflow_id, state.workflow_id)
        and _exact_string(event.step_id, state.current_step_id)
        and type(event.step_index) is int
        and event.step_index == state.current_step_index
        and _exact_string(event.employee_id, state.current_employee_id)
        and _exact_string(event.previous_status, "running")
        and provider_valid
        and request_valid
    )
    if state.status == "succeeded":
        return (
            base
            and _exact_string(event.event_type, "step_succeeded")
            and _exact_string(event.next_status, "succeeded")
            and expected_failure is None
            and event.failure_category is None
            and _nonempty_string(event.response_id)
            and type(event.output_text) is str
            and (allow_empty_success_output or bool(event.output_text))
            and event.message is None
        )
    message_valid = (
        isinstance(event.message, str)
        if failure_message_policy == "string"
        else _nonempty_string(event.message)
    )
    return (
        base
        and _exact_string(event.event_type, "step_failed")
        and _exact_string(event.next_status, "failed")
        and event.failure_category == expected_failure
        and type(expected_failure) is str
        and expected_failure in _FAILURE_CATEGORIES
        and event.response_id is None
        and event.output_text is None
        and message_valid
    )


def _request_id_valid(
    value: object,
    policy: Literal["ignore", "optional", "required"],
    *,
    allow_none_request_id: bool = False,
) -> bool:
    if policy == "ignore":
        return True
    if policy == "optional":
        return value is None or _nonempty_string(value)
    return (allow_none_request_id and value is None) or _nonempty_string(value)


def _nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value)


def _exact_string(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _invalid() -> None:
    raise TerminalHistoryContractError from None
