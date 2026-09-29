# AI Office Engine Target Architecture

> **Status:** This is the normative target architecture for future engine design and implementation. It is not a claim that every capability described here is implemented today.

## Documentation authority

The documentation authority hierarchy is:

1. [`docs/product-vision.md`](product-vision.md) — **Why / immutable product direction and principles**
2. [`docs/engine-target-architecture.md`](engine-target-architecture.md) — **What the engine must ultimately guarantee; normative target design and invariants**
3. [`docs/architecture.md`](architecture.md) — **Current implementation architecture plus historical design record**
4. GitHub Issues — **Scoped deltas from current architecture toward the target**

This document is authoritative for future design decisions and invariants. `docs/architecture.md` remains authoritative for what is actually implemented today. A target capability must not be described as already implemented merely because it appears here. If a future Issue intentionally deviates from this target architecture, that deviation must be explicit and this document, or an ADR where appropriate, must be updated rather than silently diverged.


## 1. Purpose

AI Office engine is not merely a mechanism for calling AI models in sequence.

Its purpose is:

> Execute human-defined business workflows through assigned AI employees under explicit inputs, approvals, and state transitions, while preserving enough durable evidence to explain what was executed, why it was allowed, what definitions and inputs were used, what the provider returned, what artifacts were produced, and how execution can safely stop or resume.

Preserve the existing strengths:

- workflow order is human-defined;
- an AI employee processes only its assigned step;
- state transitions are explicit;
- provider execution requires explicit authorization;
- retry is never implicit;
- execution can be reconstructed from persisted state/history after restart;
- unknown or inconsistent state fails closed;
- durable state ownership and external side effects remain separated;
- simpler structures are preferred when they provide the same guarantees.

## 2. Central concept: Workflow Run

A `WorkflowDefinition` is a reusable **type of work**.

A **Workflow Run** is one concrete execution of that workflow for one concrete business request.

```text
Employee Definition
        |
Workflow Definition
        |
        v
   Workflow Run
   |- Run Identity
   |- Run Input
   |- Definition Snapshot
   |- Execution State
   |- Approvals
   |- Execution Evidence
   |- Step Outputs
   `- Artifacts
```

Executing the same workflow ten times creates ten independent Runs.

State, input, evidence, approvals, attempts, and artifacts from different Runs must never be mixed.

## 3. Definition and Run are separate

Definitions are reusable templates:

- `WorkflowDefinition`
- `EmployeeDefinition`
- tool contracts

Run-specific business data must not be embedded into the reusable workflow solely to execute one case.

Conceptually:

```text
Workflow:
  "Research a company"

Run Input:
  "Research Company ABC's generative-AI strategy"
```

## 4. Run Input

Run Input is the business input supplied by a human or an upstream system for one Run.

It is distinct from:

- Employee Instructions — how the AI employee behaves
- Step Instructions — what the workflow step does
- Upstream Step Output — result of a completed predecessor
- Runtime Facts — typed system/persisted facts

`RuntimeFactsSnapshot` must not become a substitute for Run Input.

Run Input must become durable before the first provider side effect and must not silently change afterward.

Every applicable step must be able to receive the same Run Input identity.

## 5. Run Identity

Every Run has one immutable `run_id` or equivalent stable identity.

All state, events, evidence, approvals, attempts, and artifacts belong to exactly one Run.

The concrete identifier format is an implementation detail.

Cross-Run mixing must fail closed.

## 6. Definition Snapshot

At Run creation, freeze the execution meaning of the Run.

At minimum, the engine must preserve durable identity/snapshot evidence for:

- Workflow Definition
- referenced Employee Definitions
- tool contracts required by the Run

Invariant:

> Editing source YAML after Run creation must not silently change the meaning of an existing Run.

Normal `continue` must use the Run's pinned definition meaning, not reinterpret arbitrary live YAML as the authoritative Run definition.

Definition migration is not ordinary continuation and is out of scope for the initial target.

## 7. Step Input Bundle

Keep the semantic input classes distinct inside the engine:

```text
Step Input
|- Step Instructions
|- Run Input
|- Upstream Step Output
`- Runtime Facts
```

Employee Instructions remain a separate system-side instruction source.

A provider adapter may serialize these values into its wire format, but the engine must not erase their semantic distinction.

## 8. Workflow Approval Policy

The existing explicit approval mechanisms remain.

In addition, a Workflow Definition must be able to declare whether a business approval is required before a step/progression point.

Concrete YAML field names and internal class layout are implementation details.

The safe default must not silently weaken existing approval behavior.

### Approval types are semantically distinct

Do not conflate:

- **Business Approval** — permission to proceed with a business step
- **Execution Approval** — permission to perform a provider/external execution
- **Publication Approval** — permission to publish/export externally
- **Recovery Approval** — permission to retry/recover after failure or ambiguity

Approval for one purpose must not authorize another purpose.

## 9. External side-effect invariant

A workflow business policy that says "no business approval required" must not implicitly authorize paid provider execution, external publication, or retry.

The current preview / expected-identity / explicit-execution-approval model remains a valid safety property.

## 10. Run Evidence

Durable evidence must make it possible to reconstruct important execution facts, including:

- Run created
- definitions pinned
- Run Input accepted
- business approval granted, when applicable
- execution approval granted
- invocation prepared
- provider attempt claimed/started
- provider response received or left ambiguous
- step succeeded or failed
- artifact produced
- recovery approved/attempted

These facts do not require one class/file each. The observable guarantee matters more than internal structure.

## 11. Provider Invocation Evidence

For a provider execution, it must be possible to trace at least:

- Run identity
- step identity
- employee identity
- execution target
- exact invocation identity/fingerprint
- system-instruction identity
- task-side input identity
- resolved-tool identity
- approval identity
- attempt identity
- provider request/response identifiers where available
- normalized result
- raw-provider-response evidence
- final step execution result

Credentials themselves must never be persisted as execution evidence.

## 12. Raw response evidence

The product vision requirement that raw responses are traceable is normative.

Raw-response evidence must not mean blindly surfacing raw bodies in user-facing errors.

The engine must preserve safe traceability while ensuring:

- secrets are not embedded in safe errors;
- raw responses are not unconditionally printed by normal CLI output;
- integrity can be checked, such as by digest;
- credential values are not persisted as evidence.

Concrete storage/encryption layout is implementation detail.

## 13. Provider attempt claim

Before an external provider request is sent, durable evidence must establish that the Run/Step is attempting a specific invocation.

The goal is not magical exactly-once delivery.

The goal is safe ambiguity handling.

Example:

```text
running
+
attempt-started evidence
+
no response evidence
```

After a crash in this state, the engine must not silently re-execute the provider request.

It must stop for recovery/investigation.

## 14. Artifact

Artifact is a first-class business result distinct from raw/normalized model output.

An Artifact must be traceable to at least:

- Run identity
- producing step
- immutable content identity
- content type
- content digest
- size
- provenance

An existing artifact must not be silently overwritten. A changed result is a new artifact/version identity.

Examples include Markdown, JSON, CSV, HTML, code, or other business outputs.

Concrete filesystem/database layout is implementation detail.

## 15. Model Output vs Artifact

```text
Model Output
    provider result

Artifact
    business result intentionally preserved by the workflow/system
```

They may be identical, but need not be.

Publication/export should eventually operate on explicit artifacts/projections rather than assuming every provider output is a publishable artifact.

## 16. State and Evidence have different authority

Do not create two competing current-state authorities.

- **Execution State** answers: "Where is the Run now?"
- **Evidence** answers: "Why/how did it reach that state?"

If state and required evidence are inconsistent, fail closed.

Do not silently repair current state by guessing from partial evidence.

## 17. Continue semantics

Normal continuation must progress from the Run's durable identity, pinned definition meaning, durable state, and evidence.

It must not mean "reload arbitrary live YAML and reinterpret the existing Run."

## 18. Failure

Normal step failure remains:

```text
Step failure
    |
durable failure
    |
   STOP
```

Failure detection alone must not cause:

- retry
- provider switch
- model switch
- input mutation
- step skip
- definition mutation

## 19. Explicit Recovery / Retry

Recovery is a separate, explicit operation.

Conceptually:

```text
failed or ambiguous attempt
        |
human/system review
        |
explicit recovery decision
        |
Recovery Approval
        |
new attempt
```

Previous failed/ambiguous attempts must remain durable and auditable.

Normal recovery must not rewrite:

- Workflow Definition snapshot
- Employee Definition snapshot
- Run Input
- completed-step history

If the business meaning must change, prefer a new Run rather than mutating the old Run.

## 20. Retry Policy

A workflow may eventually state that retry is permitted or prohibited.

"Retry permitted" never means "automatic retry."

Actual retry requires explicit Recovery Approval.

## 21. Relationship to the current engine

Current canonical owners should remain the starting point rather than being wrapped in a new deep chain merely to add Run concepts.

Current important ownership includes:

- Phase 145 — approved preparation boundary
- Phase 146 — prepared-step start boundary
- Phase 147 — prepared running-state persistence
- Phase 155 — persisted-running execution boundary
- Phase 161 — runtime result terminal durable persistence
- Phase 172 — post-commit composition / committed snapshot safety
- Phase 38 — canonical classification/progression composition
- Phase 37 — persisted terminal classification
- Phase 31 — persisted-success progression

Future Run capabilities should reuse or extend existing ownership where semantically appropriate.

Do not rebuild a wrapper/bridge ladder around these owners.

## 22. Run Manifest

Before execution, a Run has an immutable durable manifest or equivalent authoritative record containing at least the identities of:

- Run
- Workflow
- Workflow Definition snapshot
- referenced Employee Definition snapshots
- required tool contracts
- Run Input
- schema/version identity

Credentials are never part of the manifest.

Execution approvals are later evidence, not immutable Run-definition fields.

## 23. Run creation gate

Before the first external provider side effect:

1. validate Workflow Definition;
2. validate referenced employees;
3. validate required tool contracts;
4. validate Run Input;
5. pin definition meaning;
6. durably commit Run Manifest and Run Input identity.

If this durable commit fails, provider execution must not occur.

## 24. Target execution flow

```text
Human / upstream system
        |
        |- Workflow Definition
        |- Employee Definitions
        `- Run Input
                |
                v
           Create Run
                |
                |- validate
                |- pin definitions
                |- persist input
                `- persist manifest
                |
                v
           Preview Step
                |
                v
         Business Approval
          (when required)
                |
                v
         Execution Approval
                |
                v
       Persist attempt evidence
                |
                v
       Persist running state
                |
                v
        Execute provider once
                |
                v
 Persist raw/normalized response evidence
                |
                v
    Persist terminal transition
                |
                v
       Produce artifact(s)
                |
                v
 Classification / Progression
                |
             next / stop
```

Exact internal function order may differ if the same externally required guarantees are preserved.

## 25. Core invariants

### Identity
All state/events/evidence/artifacts belong to exactly one Run.

### Definition immutability
Source-definition changes after Run creation do not silently change an existing Run.

### Input immutability
Run Input does not silently change after Run creation.

### Ordered execution
The engine does not invent steps or execute steps outside the human-defined workflow.

### Approval
Operations requiring approval do not proceed without the correct approval purpose.

### Side effects
Provider execution, publication, and recovery do not occur without their explicit authorization.

### Ambiguity safety
Provider-side ambiguity never causes automatic replay.

### No automatic retry
Failure, timeout, transport uncertainty, or restart never automatically retries.

### Immutable history
Past attempts, approvals, events, and artifacts are not silently overwritten.

### Fail closed
Identity/evidence/definition inconsistencies stop execution.

### No speculative recovery
Restart does not guess missing facts.

## 26. Initial non-goals

Do not make these requirements for core engine completion:

- conditional workflow branching
- parallel execution
- workflow loops
- autonomous agent planning
- AI-authored workflow changes
- GUI
- approval UI
- distributed execution
- multi-writer concurrency
- automatic retry
- automatic approval
- arbitrary general-purpose tool-execution loop
- definition migration
- dynamic workflow generation

They may be designed later without weakening the core invariants.

## 27. Delivery milestones

Do not treat new Phase numbers as milestones by themselves.

### Milestone 1 — Run Foundation
- Run identity
- immutable Run Input
- Run Manifest
- workflow/employee/tool definition pinning
- start/continue operate against Run identity

### Milestone 2 — Approval Policy & Evidence
- workflow-defined business approval policy
- Run-bound approval evidence
- business/execution/recovery/publication approval separation

### Milestone 3 — Execution Evidence
- invocation evidence
- execution-target evidence
- attempt identity
- pre-transport attempt claim
- raw-response evidence
- normalized-response linkage

### Milestone 4 — Artifact
- immutable artifact identity
- digest/provenance
- Run/step linkage
- artifact read/export

### Milestone 5 — Explicit Recovery
- recovery decision
- Recovery Approval
- new attempt lineage
- previous attempt preservation
- no automatic retry

## 28. Engine completion acceptance criteria

The engine core can be considered functionally complete when all of the following observable guarantees exist:

1. Multiple independent Runs can be created from one Workflow Definition.
2. Every Run has explicit business input.
3. The exact workflow/employee/tool definition meaning used by a Run is traceable.
4. Editing source YAML does not silently change the meaning of an existing Run.
5. Step Instructions, Run Input, Upstream Output, and Runtime Facts remain semantically distinguishable.
6. Workflow Definition can declare human business-approval points.
7. A required business approval cannot be bypassed.
8. Provider execution cannot occur without explicit execution approval.
9. Durable attempt evidence exists before an external provider side effect becomes ambiguous.
10. Exact invocation identity is auditable.
11. Raw provider response evidence is traceable without exposing secrets through normal errors/output.
12. Successful work can produce first-class durable artifacts.
13. State, events, approvals, attempts, invocation evidence, and artifacts can all be associated with one Run identity.
14. After crash/restart, the engine can safely stop or continue based only on authoritative durable information.
15. Failure never causes automatic retry.
16. Retry requires explicit Recovery Approval.
17. Failed/ambiguous previous attempts remain preserved.
18. The engine does not invent undefined steps, branches, or workflow completions.
19. Unknown/inconsistent persisted information fails closed.
20. These guarantees are not implemented by rebuilding unnecessary wrapper/bridge chains.

## 29. Final responsibility model

```text
What work exists?        -> Workflow Definition
Who performs it?         -> Employee Definition
What is this case?       -> Run Input
Which execution is this? -> Workflow Run
Where is it now?         -> Execution State
Why may it proceed?      -> Approval + Evidence
What was sent?           -> Invocation Evidence
What came back?          -> Provider Evidence
What was produced?       -> Artifact
What happens after fail? -> Explicit Recovery
```

The engine's job is not to let AI act freely.

Its job is to make AI-performed business work **explicit, controlled, reproducible, restart-safe, and auditable**.

---
