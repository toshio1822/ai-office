"""Provider-independent inputs for a future model adapter."""

import json
from dataclasses import dataclass

from ai_office.invocation.runtime_facts import (
    EMPTY_RUNTIME_FACTS,
    RuntimeFactsSnapshot,
    runtime_facts_snapshot_digest,
    serialize_runtime_facts_snapshot_canonical,
)
from ai_office.planning.step_execution_request import StepExecutionRequest


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

    Run identity and Run Input are carried directly by this request but kept out
    of the historical dataclass field set.  This preserves the provider-facing
    request shape for old unbound callers while making every Run-owned request
    explicit and independently checkable.
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
    ) -> None:
        _validate_optional_run_binding(run_id, manifest_digest, run_input)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "system_instructions", system_instructions)
        object.__setattr__(self, "task_instructions", task_instructions)
        object.__setattr__(self, "allowed_tools", allowed_tools)
        object.__setattr__(self, "upstream_inputs", upstream_inputs)
        object.__setattr__(self, "runtime_facts", runtime_facts)
        if run_id is not None:
            object.__setattr__(self, "_run_id", run_id)
            object.__setattr__(self, "_manifest_digest", manifest_digest)
            object.__setattr__(self, "_run_input", run_input)
        _validate_upstream_inputs(self.upstream_inputs)
        _validate_runtime_facts(self.runtime_facts)

    @property
    def run_id(self) -> str | None:
        return getattr(self, "_run_id", None)

    @property
    def manifest_digest(self) -> str | None:
        return getattr(self, "_manifest_digest", None)

    @property
    def run_input(self) -> str | None:
        return getattr(self, "_run_input", None)

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
            self.run_id,
            self.manifest_digest,
            self.run_input,
        ) == (
            other.model,
            other.system_instructions,
            other.task_instructions,
            other.allowed_tools,
            other.upstream_inputs,
            other.runtime_facts,
            other.run_id,
            other.manifest_digest,
            other.run_input,
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
                self.run_id,
                self.manifest_digest,
                self.run_input,
            )
        )


def build_model_invocation_request(
    step_request: StepExecutionRequest,
    *,
    upstream_inputs: tuple[UpstreamStepOutput, ...] = (),
    runtime_facts: RuntimeFactsSnapshot = EMPTY_RUNTIME_FACTS,
) -> ModelInvocationRequest:
    """Copy a step request into the provider-independent invocation boundary."""
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
        run_input=step_request.run_input,
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


def _validate_optional_run_binding(
    run_id: object,
    manifest_digest: object,
    run_input: object,
) -> None:
    if run_id is None and manifest_digest is None:
        if run_input is not None:
            raise ValueError("workflow Run input is unbound")
        return
    if run_id is None or manifest_digest is None:
        raise ValueError("workflow Run binding is incomplete")
    if type(run_id) is not str or not run_id:
        raise ValueError("workflow Run identity is invalid")
    if (
        type(manifest_digest) is not str
        or len(manifest_digest) != 64
        or any(character not in "0123456789abcdef" for character in manifest_digest)
    ):
        raise ValueError("workflow Run Manifest identity is invalid")
    if run_input is not None and type(run_input) is not str:
        raise TypeError("workflow Run input must be a string")


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
