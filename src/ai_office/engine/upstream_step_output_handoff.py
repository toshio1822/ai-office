"""Restart-safe reconstruction of the immediately preceding step output."""

from dataclasses import dataclass
from typing import Literal

from ai_office.invocation import UpstreamStepOutput
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import LoadedWorkflowExecutionHistory

UpstreamStepOutputHandoffClassification = Literal[
    "workflow_identity",
    "next_step_index",
    "history_type",
    "history_identity",
]
_ERROR_MESSAGE = "upstream step output handoff inputs are incompatible"


@dataclass(frozen=True)
class UpstreamStepOutputHandoffFailureDetail:
    """Detail-safe classification for an incompatible handoff request."""

    classification: UpstreamStepOutputHandoffClassification


class UpstreamStepOutputHandoffError(ValueError):
    """Raised when persisted terminal history cannot provide one predecessor."""


class UpstreamStepOutputHandoffCompatibilityError(UpstreamStepOutputHandoffError):
    """Raised for a safe, detail-only handoff incompatibility."""

    def __init__(self, classification: UpstreamStepOutputHandoffClassification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = UpstreamStepOutputHandoffFailureDetail(classification)


def build_immediate_predecessor_upstream_inputs(
    workflow_id: object,
    next_step_index: object,
    history: object,
) -> tuple[UpstreamStepOutput, ...]:
    """Copy exactly one successful predecessor output from authoritative history.

    The function intentionally accepts a loaded history rather than paths.  The
    strict storage loader therefore remains the owner of decoding and
    state/event consistency, while this pure seam owns only the continuation
    handoff contract.
    """
    if type(workflow_id) is not str or not workflow_id:
        _raise("workflow_identity")
    if type(next_step_index) is not int or isinstance(next_step_index, bool):
        _raise("next_step_index")
    if next_step_index < 2:
        _raise("next_step_index")
    if type(history) is not LoadedWorkflowExecutionHistory:
        _raise("history_type")
    state = history.state
    if type(state) is not WorkflowExecutionState or type(history.events) is not tuple:
        _raise("history_type")
    if (
        type(state.status) is not str
        or state.status != "succeeded"
        or type(state.workflow_id) is not str
        or state.workflow_id != workflow_id
    ):
        _raise("workflow_identity")
    predecessor_index = next_step_index - 1
    if (
        type(state.current_step_index) is not int
        or isinstance(state.current_step_index, bool)
        or state.current_step_index != predecessor_index
    ):
        _raise("next_step_index")
    if not history.events or type(history.events[-1]) is not RuntimeStepEvent:
        _raise("history_identity")
    event = history.events[-1]
    if not (
        type(event.event_type) is str
        and event.event_type == "step_succeeded"
        and type(event.workflow_id) is str
        and event.workflow_id == state.workflow_id
        and type(event.step_id) is str
        and type(state.current_step_id) is str
        and event.step_id == state.current_step_id
        and type(event.step_index) is int
        and not isinstance(event.step_index, bool)
        and event.step_index == state.current_step_index
        and type(event.employee_id) is str
        and type(state.current_employee_id) is str
        and event.employee_id == state.current_employee_id
        and type(event.previous_status) is str
        and event.previous_status == "running"
        and type(event.next_status) is str
        and event.next_status == "succeeded"
        and event.failure_category is None
        and event.message is None
        and type(event.output_text) is str
    ):
        _raise("history_identity")
    return (
        UpstreamStepOutput(
            workflow_id=event.workflow_id,
            step_id=event.step_id,
            step_index=event.step_index,
            employee_id=event.employee_id,
            output_text=event.output_text,
        ),
    )


def _raise(classification: UpstreamStepOutputHandoffClassification) -> None:
    raise UpstreamStepOutputHandoffCompatibilityError(classification) from None


__all__ = [
    "UpstreamStepOutputHandoffClassification",
    "UpstreamStepOutputHandoffCompatibilityError",
    "UpstreamStepOutputHandoffError",
    "UpstreamStepOutputHandoffFailureDetail",
    "build_immediate_predecessor_upstream_inputs",
]
