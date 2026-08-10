"""Focused tests for the shared terminal-history contract."""

# ruff: noqa: E501

from dataclasses import replace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    validate_strict_terminal_history,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState


class StringChild(str):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "history-workflow",
            "name": "History workflow",
            "description": "shared contract",
            "steps": [
                {"id": "one", "name": "One", "employee": "one", "instructions": "one"},
                {"id": "two", "name": "Two", "employee": "two", "instructions": "two"},
                {"id": "three", "name": "Three", "employee": "three", "instructions": "three"},
                {"id": "four", "name": "Four", "employee": "four", "instructions": "four"},
                {"id": "five", "name": "Five", "employee": "five", "instructions": "five"},
            ],
        }
    )


def succeeded_state(
    definition: WorkflowDefinition,
    index: int,
) -> WorkflowExecutionState:
    current = definition.steps[index - 1]
    return WorkflowExecutionState(
        definition.id,
        "succeeded",
        current.id,
        index,
        current.employee,
        tuple(step.id for step in definition.steps[:index]),
        None,
    )


def succeeded_event(
    definition: WorkflowDefinition,
    index: int,
    *,
    output_text: object = "output",
    response_id: object = "response",
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
        "openai",
        None,
        response_id,
        "request",
        output_text,
        None,
    )


def succeeded_history(
    definition: WorkflowDefinition,
    index: int,
    *,
    outputs: tuple[object, ...] | None = None,
) -> tuple[RuntimeStepEvent, ...]:
    values = outputs or tuple("output" for _ in range(index))
    return tuple(
        succeeded_event(definition, position, output_text=values[position - 1])
        for position in range(1, index + 1)
    )


def failed_history(
    definition: WorkflowDefinition,
    index: int,
) -> tuple[RuntimeStepEvent, ...]:
    events = list(succeeded_history(definition, index - 1))
    step = definition.steps[index - 1]
    events.append(
        RuntimeStepEvent(
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
            "request",
            None,
            "failure",
        )
    )
    return tuple(events)


def assert_rejected(
    definition: WorkflowDefinition,
    state: WorkflowExecutionState,
    events: tuple[RuntimeStepEvent, ...],
) -> None:
    with pytest.raises(TerminalHistoryContractError):
        validate_strict_terminal_history(definition, state, events)


def test_non_final_succeeded_terminal_empty_output_is_accepted() -> None:
    definition = workflow()
    state = succeeded_state(definition, 4)
    events = succeeded_history(definition, 4, outputs=("output", "output", "output", ""))

    validate_strict_terminal_history(definition, state, events)


def test_non_final_succeeded_terminal_nonempty_output_remains_accepted() -> None:
    definition = workflow()
    state = succeeded_state(definition, 4)
    events = succeeded_history(definition, 4)

    validate_strict_terminal_history(definition, state, events)


def test_non_final_succeeded_history_accepts_earlier_empty_output_after_later_success() -> None:
    definition = workflow()
    state = succeeded_state(definition, 4)
    events = succeeded_history(
        definition,
        4,
        outputs=("", "later-output", "later-output", "terminal-output"),
    )

    validate_strict_terminal_history(definition, state, events)


@pytest.mark.parametrize("value", [4, None, True, StringChild("output")])
def test_non_final_success_output_remains_exact_builtin_string(value: object) -> None:
    definition = workflow()
    state = succeeded_state(definition, 4)
    events = succeeded_history(
        definition,
        4,
        outputs=("output", "output", "output", value),
    )

    assert_rejected(definition, state, events)


@pytest.mark.parametrize("value", ["", 4, None, StringChild("response")])
def test_non_final_response_id_remains_exact_nonempty_builtin_string(value: object) -> None:
    definition = workflow()
    state = succeeded_state(definition, 4)
    events = list(succeeded_history(definition, 4))
    events[-1] = replace(events[-1], response_id=value)

    assert_rejected(definition, state, tuple(events))


def test_non_final_earlier_response_id_remains_strict() -> None:
    definition = workflow()
    state = succeeded_state(definition, 4)
    events = list(succeeded_history(definition, 4))
    events[0] = replace(events[0], response_id="")

    assert_rejected(definition, state, tuple(events))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_id", "wrong-workflow"),
        ("current_step_id", "wrong-step"),
        ("current_step_index", 3),
        ("current_employee_id", "wrong-employee"),
        ("status", "running"),
        ("completed_step_ids", ("one", "wrong", "three", "four")),
    ],
)
def test_non_final_state_linkage_and_prefix_remain_strict(
    field: str, value: object
) -> None:
    definition = workflow()
    state = replace(succeeded_state(definition, 4), **{field: value})
    events = succeeded_history(definition, 4)

    assert_rejected(definition, state, events)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_id", "wrong-workflow"),
        ("step_id", "wrong-step"),
        ("step_index", 99),
        ("employee_id", "wrong-employee"),
        ("event_type", "step_failed"),
        ("previous_status", "ready"),
        ("next_status", "failed"),
        ("message", "unexpected"),
    ],
)
def test_non_final_event_linkage_and_status_remain_strict(
    field: str, value: object
) -> None:
    definition = workflow()
    state = succeeded_state(definition, 4)
    events = list(succeeded_history(definition, 4))
    events[-1] = replace(events[-1], **{field: value})

    assert_rejected(definition, state, tuple(events))


def test_non_final_history_order_remains_strict() -> None:
    definition = workflow()
    state = succeeded_state(definition, 4)
    events = succeeded_history(definition, 4)

    assert_rejected(definition, state, tuple(reversed(events)))


def test_final_succeeded_empty_terminal_output_remains_rejected() -> None:
    definition = workflow()
    state = succeeded_state(definition, len(definition.steps))
    events = succeeded_history(
        definition,
        len(definition.steps),
        outputs=("output", "output", "output", "output", ""),
    )

    assert_rejected(definition, state, events)


def test_final_history_earlier_empty_output_remains_strict() -> None:
    definition = workflow()
    state = succeeded_state(definition, len(definition.steps))
    events = succeeded_history(
        definition,
        len(definition.steps),
        outputs=("", "output", "output", "output", "terminal"),
    )

    assert_rejected(definition, state, events)


def test_failed_history_behavior_remains_unchanged() -> None:
    definition = workflow()
    state = replace(
        succeeded_state(definition, 4),
        status="failed",
        completed_step_ids=("one", "two", "three"),
        last_failure_category="api_error",
    )
    events = failed_history(definition, 4)

    validate_strict_terminal_history(definition, state, events)

    invalid = list(events)
    invalid[-1] = replace(invalid[-1], response_id="response")
    assert_rejected(definition, state, tuple(invalid))


@pytest.mark.parametrize(
    "events",
    [
        (),
        (object(),),
    ],
)
def test_malformed_history_still_raises_contract_error(events: tuple[object, ...]) -> None:
    definition = workflow()
    state = succeeded_state(definition, 4)

    with pytest.raises(TerminalHistoryContractError):
        validate_strict_terminal_history(definition, state, events)  # type: ignore[arg-type]
