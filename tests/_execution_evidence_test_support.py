"""Small helpers for provider tests that need an authoritative Run context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.workflow_approval_evidence import (
    build_execution_approval_evidence_for_tools,
    persist_execution_approval_evidence,
)
from ai_office.execution_evidence import (
    ExecutionEvidenceContext,
    build_execution_evidence_context,
)
from ai_office.execution_target import ModelExecutionTarget
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationRequest,
    approve_model_invocation_execution,
)
from ai_office.tools import ToolDefinition
from tests._run_test_support import TestRun, create_test_run


@dataclass(frozen=True)
class TestExecutionEvidence:
    run: TestRun
    request: ModelInvocationRequest
    approval: ModelInvocationExecutionApproval
    context: ExecutionEvidenceContext


def create_test_execution_evidence(
    root: Path,
    *,
    run_id: str,
    workflow: WorkflowDefinition,
    employees: tuple[EmployeeDefinition, ...],
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    step_id: str,
    target: ModelExecutionTarget,
    approved_by: str = "test-execution-reviewer",
    approval_id: str = "test-execution-approval",
) -> TestExecutionEvidence:
    """Bind a provider test request to one Manifest and durable approval."""
    run = create_test_run(
        root,
        run_id,
        workflow,
        employees,
        with_business_approvals=False,
    )
    step_index, step = next(
        (index, step)
        for index, step in enumerate(workflow.steps, start=1)
        if step.id == step_id
    )
    if request.run_id is not None or request.manifest_digest is not None:
        raise ValueError("source test request must be unbound")
    bound_request = ModelInvocationRequest(
        model=request.model,
        system_instructions=request.system_instructions,
        task_instructions=request.task_instructions,
        allowed_tools=request.allowed_tools,
        upstream_inputs=request.upstream_inputs,
        runtime_facts=request.runtime_facts,
        binding=run.binding,
        run_input=f"input-{run_id}",
    )
    approval = approve_model_invocation_execution(
        bound_request,
        resolved_tools,
        provider=target.provider,
        approved_by=approved_by,
        approval_id=approval_id,
        execution_target=target,
    )
    approval_evidence = build_execution_approval_evidence_for_tools(
        bound_request,
        resolved_tools,
        approval,
        workflow_id=workflow.id,
        step_id=step_id,
        step_index=step_index,
        employee_id=step.employee,
        target=target,
    )
    persist_execution_approval_evidence(run.store, approval_evidence)
    context = build_execution_evidence_context(
        store_root=run.store.root,
        binding=run.binding,
        workflow_id=workflow.id,
        step_id=step_id,
        step_index=step_index,
        employee_id=step.employee,
        request=bound_request,
        resolved_tools=resolved_tools,
        approval=approval,
        target=target,
    )
    return TestExecutionEvidence(run, bound_request, approval, context)
