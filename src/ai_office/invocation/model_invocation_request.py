"""Provider-independent inputs for a future model adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ai_office.invocation.runtime_facts import (
    EMPTY_RUNTIME_FACTS,
    RuntimeFactsSnapshot,
    runtime_facts_snapshot_digest,
    serialize_runtime_facts_snapshot_canonical,
)
from ai_office.planning.step_execution_request import StepExecutionRequest

if TYPE_CHECKING:
    from ai_office.runtime.run_binding import WorkflowRunBinding


@dataclass(frozen=True)
class UpstreamStepOutput:
    """One successful predecessor output forwarded as task-side data."""

    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    output_text: str


@dataclass(frozen=True, init=False)
class ModelInvocationRequest:
    """Immutable values required to invoke a model for one workflow step.

    The Run binding (Run identity + Manifest digest) and the business Run Input
    are fixed here at construction and never mutated.  Run Input is semantic
    task data whose only durable authority is the Run Manifest; it is not part
    of the Run binding.  The historical dataclass field set omits both so that
    provider-facing unbound callers keep their shape, while every Run-owned
    request stays explicit and independently checkable.
    """

    model: str
    system_instructions: str
    task_instructions: str
    allowed_tools: tuple[str, ...]
    upstream_inputs: tuple[UpstreamStepOutput, ...] = ()
    runtime_facts: RuntimeFactsSnapshot = EMPTY_RUNTIME_FACTS

    def __init__(
        self,
        model: str,
        system_instructions: str,
        task_instructions: str,
        allowed_tools: tuple[str, ...],
        upstream_inputs: tuple[UpstreamStepOutput, ...] = (),
        runtime_facts: RuntimeFactsSnapshot = EMPTY_RUNTIME_FACTS,
        *,
        run_id: str | None = None,
        manifest_digest: str | None = None,
        run_input: str | None = None,
        binding: WorkflowRunBinding | None = None,
    ) -> None:
        if run_input is not None and type(run_input) is not str:
            raise TypeError("workflow Run input must be a string")
        # Imported here so that this provider-independent leaf module does not
        # create an import cycle back into the runtime package.
        from ai_office.runtime.run_binding import select_run_binding

        selected = select_run_binding(binding, run_id, manifest_digest)
        if run_input is not None and selected is None:
            raise ValueError("workflow Run input is unbound")
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "system_instructions", system_instructions)
        object.__setattr__(self, "task_instructions", task_instructions)
        object.__setattr__(self, "allowed_tools", allowed_tools)
        object.__setattr__(self, "upstream_inputs", upstream_inputs)
        object.__setattr__(self, "runtime_facts", runtime_facts)
        object.__setattr__(self, "_run_binding", selected)
        object.__setattr__(self, "_run_input", run_input)
        _validate_upstream_inputs(self.upstream_inputs)
        _validate_runtime_facts(self.runtime_facts)

    @property
    def run_id(self) -> str | None:
        binding = self._run_binding
        return None if binding is None else binding.run_id

    @property
    def manifest_digest(self) -> str | None:
        binding = self._run_binding
        return None if binding is None else binding.manifest_digest

    @property
    def run_input(self) -> str | None:
        return self._run_input

    def __eq__(self, other: object) -> bool:
        """Compare the complete request, including its direct Run binding."""
        if type(other) is not ModelInvocationRequest:
            return NotImplemented
        assert isinstance(other, ModelInvocationRequest)
        return (
            self.model,
            self.system_instructions,
            self.task_instructions,
            self.allowed_tools,
            self.upstream_inputs,
            self.runtime_facts,
            self._run_binding,
            self._run_input,
        ) == (
            other.model,
            other.system_instructions,
            other.task_instructions,
            other.allowed_tools,
            other.upstream_inputs,
            other.runtime_facts,
            other._run_binding,
            other._run_input,
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.model,
                self.system_instructions,
                self.task_instructions,
                self.allowed_tools,
                self.upstream_inputs,
                self.runtime_facts,
                self._run_binding,
                self._run_input,
            )
        )


def build_model_invocation_request(
    step_request: StepExecutionRequest,
    *,
    upstream_inputs: tuple[UpstreamStepOutput, ...] = (),
    runtime_facts: RuntimeFactsSnapshot = EMPTY_RUNTIME_FACTS,
    run_input: str | None = None,
) -> ModelInvocationRequest:
    """Copy a step request into the provider-independent invocation boundary.

    ``run_input`` is the Manifest-authoritative business input for the Run.  It
    is passed as a distinct semantic value, separate from step/employee
    instructions, upstream outputs, and runtime facts.
    """
    _validate_upstream_inputs(upstream_inputs)
    _validate_runtime_facts(runtime_facts)
    return ModelInvocationRequest(
        model=step_request.model,
        system_instructions=step_request.employee_instructions,
        task_instructions=step_request.step_instructions,
        allowed_tools=tuple(step_request.allowed_tools),
        upstream_inputs=upstream_inputs,
        runtime_facts=runtime_facts,
        run_id=step_request.run_id,
        manifest_digest=step_request.manifest_digest,
        run_input=run_input,
    )


def build_model_invocation_task_input(request: ModelInvocationRequest) -> str:
    """Render distinct Run Input, step, predecessor, and fact values."""
    has_runtime_facts = request.runtime_facts != EMPTY_RUNTIME_FACTS
    has_run_input = request.run_input is not None
    if request.upstream_inputs == () and not has_runtime_facts and not has_run_input:
        return request.task_instructions

    value: dict[str, object] = {
        "task_instructions": request.task_instructions,
    }
    if has_run_input:
        value["run_input"] = request.run_input
    if has_runtime_facts:
        runtime_facts = json.loads(
            serialize_runtime_facts_snapshot_canonical(request.runtime_facts)
        )
        runtime_facts["snapshot_sha256"] = runtime_facts_snapshot_digest(
            request.runtime_facts
        )
        value["runtime_facts"] = runtime_facts
    if request.upstream_inputs != ():
        value["upstream_inputs"] = [
            {
                "employee_id": upstream.employee_id,
                "output_text": upstream.output_text,
                "step_id": upstream.step_id,
                "step_index": upstream.step_index,
                "workflow_id": upstream.workflow_id,
            }
            for upstream in request.upstream_inputs
        ]
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _validate_upstream_inputs(value: object) -> None:
    """Reject unordered or structurally ambiguous upstream input containers."""
    if type(value) is not tuple or any(
        type(item) is not UpstreamStepOutput for item in value
    ):
        raise TypeError("upstream_inputs must be a tuple of UpstreamStepOutput")


def _validate_runtime_facts(value: object) -> None:
    """Reject substitutes so the runtime-facts snapshot remains the sole validator."""
    if type(value) is not RuntimeFactsSnapshot:
        raise TypeError("runtime_facts must be a RuntimeFactsSnapshot")
