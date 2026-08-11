# アーキテクチャ

## Phase 59: classified persisted outcome routing phase bridge reentry

Phase 59 accepts exactly one Phase 58 result. Exact `persisted_success` and
`persisted_failure` outcomes are verified against strict terminal state/history
and delegated exactly once to Phase 52 with the caller-supplied workflow,
state/event `Path` objects, and result object unchanged. It returns the exact
Phase 52 decision or supplied failure object. Exact `workflow_complete` is a
read-only, unchanged stop route with zero Phase 52 calls. Dependency mutations,
malformed returns, and unexpected errors are compensated safely. Phase 59 does
not duplicate Phase 52, Phase 45, or Phase 38 logic; prepare or execute a next
step, persist state, invoke providers/tools, retry, continue automatically,
finalize terminal outcomes, schedule, loop, run in parallel, or add paid
CLI/GUI behavior.

```text
Phase 58
persisted_success | persisted_failure | workflow_complete
    ↓
Phase 59
persisted_success/failure → Phase 52 exactly once
    → prepare_next_step | workflow_complete | same persisted_failure
workflow_complete → unchanged stop
    ↓
future explicit boundary
```

## Phase 60: approved next-step preparation phase bridge reentry

Phase 60 accepts exactly one Phase 59 result. Exact `prepare_next_step` requires
one exact approval and the exact next employee, delegates exactly once to Phase
53 with all supplied objects unchanged, and returns the exact
`PreparedWorkflowStep` dependency result. Exact `workflow_complete` and
`persisted_failure` require no approval or employee, verify strict terminal
state/history and unchanged targets, and return their supplied objects without
calling Phase 53. Dependency mutations, malformed returns, safe errors, and
unexpected errors are handled with safe compensation and no retry. Phase 60
does not create approvals, select or load employees, start steps, persist state,
execute providers/tools, continue automatically, finalize, schedule, loop, run
in parallel, or add paid CLI/GUI behavior.

```text
Phase 59
prepare_next_step | workflow_complete | persisted_failure
    ↓
Phase 60
prepare_next_step + approval + employee → Phase 53 exactly once → PreparedWorkflowStep
workflow_complete / persisted_failure → no approval/employee → unchanged stop
    ↓
Phase 54
```

## Phase 61: prepared next-step start routing phase bridge reentry

Phase 61 accepts exactly one Phase 60 result. An exact
`PreparedWorkflowStep` with the exact employee is validated against the
workflow and the immediately preceding succeeded terminal state/history, then
delegated exactly once to Phase 54 with all supplied objects unchanged. The
exact `PreparedStepExecutionStart` dependency result is validated and returned.
Exact `workflow_complete` and `persisted_failure` require no employee, verify
strict terminal state/history and unchanged targets, and return their supplied
objects without calling Phase 54. Dependency mutations, malformed returns,
safe errors, and unexpected errors are compensated safely without retry. Phase
61 does not select/load employees, persist running state, execute
providers/tools, continue automatically, finalize, schedule, loop, run in
parallel, or add paid CLI/GUI behavior.

```text
Phase 60
PreparedWorkflowStep | workflow_complete | persisted_failure
    ↓
Phase 61
PreparedWorkflowStep + employee → Phase 54 exactly once → PreparedStepExecutionStart
workflow_complete / persisted_failure → no employee → unchanged stop
    ↓
Phase 55
```

## Phase 57: executed-result transition persistence phase bridge

Phase 57 accepts exactly one Phase 56 result. Exact runtime success/failure is delegated exactly once to the existing Phase 50 bridge and returns its identical `WorkflowExecutionPersistenceResult`. Exact `workflow_complete` and `persisted_failure` are strictly verified terminal, read-only stop routes that return their supplied object unchanged without calling Phase 50. Only the exact Phase 50 transition persistence side effects are allowed; malformed, partial, or unexpected dependency writes are compensated. Phase 57 does not duplicate Phase 50, Phase 43, or Phase 36 logic and does not classify outcomes, decide progression, prepare or execute another step, retry, continue automatically, finalize, schedule, loop, or run in parallel.

## Phase 50: executed-result transition persistence bridge

Phase 50 accepts exactly one Phase 49 result. Exact runtime success/failure is delegated once to Phase 43 and returns the dependency's identical persistence-result object. Exact `workflow_complete` and `persisted_failure` are terminal, read-only routes that return their supplied object unchanged. The bridge validates persistence bytes and compensates malformed or partial Phase 43 writes; it does not call Phase 36 directly or add progression, execution, retry, continuation, finalization, scheduling, loops, or parallelism.

## Phase 51: persisted terminal outcome classification bridge

Phase 51 accepts exactly one Phase 50 result. An exact `WorkflowExecutionPersistenceResult` is validated against strict terminal state/history and delegated once to Phase 44, returning the dependency's identical `PersistedExecutionOutcome` object. Exact `workflow_complete` and existing `persisted_failure` are terminal, read-only routes which return their supplied object unchanged without calling Phase 44. The bridge compensates dependency mutations and does not call Phase 37 directly, route classified outcomes onward, decide progression, prepare or execute steps, retry, continue automatically, finalize terminal states, schedule, loop, or run in parallel.

## Phase 58: persisted terminal outcome classification phase bridge

Phase 58 accepts exactly one exact Phase 57 result. Only an exact `WorkflowExecutionPersistenceResult` is delegated exactly once to Phase 51 and the same exact `PersistedExecutionOutcome` is returned. Exact `workflow_complete` and `persisted_failure` are strict, read-only stop routes that return their supplied objects unchanged without calling Phase 51. Dependency mutations, malformed returns, and unexpected errors are compensated safely. Phase 58 does not duplicate Phase 51, Phase 44, or Phase 37 logic; route classified outcomes onward, decide progression, prepare or execute another step, retry, continue automatically, finalize, schedule, loop, or run in parallel; or add paid CLI/GUI behavior.

## Phase 52: classified persisted outcome routing bridge

Phase 52 accepts exactly one Phase 51 result. Exact persisted success or failure is delegated once to Phase 45 and returns its identical routing result. Exact `workflow_complete` is a read-only stop route. The bridge compensates dependency mutations and does not call Phase 38 directly, prepare or execute steps, persist running state, invoke providers, retry, continue automatically, finalize terminal states, schedule, loop, or run in parallel.

## Phase 53: approved next-step preparation bridge

Phase 53 accepts exactly one Phase 52 result. Exact `prepare_next_step` requires one explicit approval and exact next employee, delegates once to Phase 32, and returns the identical prepared-step result. Completion and persisted failure are read-only stop routes. It does not create approval, select employees, persist state, execute providers or tools, retry, continue automatically, or finalize terminal states.

## Phase 54: prepared-step start phase bridge

Phase 54 accepts exactly one Phase 53 result. Exact `PreparedWorkflowStep` requires the exact next employee, delegates once to Phase 47, and returns the dependency's identical `PreparedStepExecutionStart` object. Exact workflow completion and persisted failure require no employee, verify strict terminal state/history, and return their supplied object unchanged without calling Phase 47. The bridge is read-only, compensates dependency mutations, does not duplicate Phase 40 or Phase 34, and does not persist running state, invoke providers or tools, retry, continue automatically, finalize terminal states, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 55: prepared-start persistence phase bridge

Phase 55 accepts exactly one Phase 54 result. Exact `PreparedStepExecutionStart` requires the exact employee and delegates once to Phase 48, returning that dependency's identical `RunningStatePersistenceResult`; only the exact proposed running state may replace the state target and the event target remains unchanged. Exact workflow completion and persisted failure require no employee, verify strict terminal state/history, and return their supplied object unchanged without calling Phase 48. It compensates invalid dependency mutations and errors, does not duplicate Phase 41 or Phase 35, append runtime events, invoke providers or tools, retry, continue automatically, execute the prepared request, transition results, finalize terminal states, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 56: persisted-running execution phase bridge

Phase 56 accepts exactly one Phase 55 result. Exact `RunningStatePersistenceResult` requires the original prepared start, matching employee, resolved tools, API credential, and paid-execution approval, then delegates exactly once to Phase 49 and returns its identical step runtime execution result. Exact workflow completion and persisted failure require all execution-only inputs to be absent, verify strict terminal state/history, and return their supplied object unchanged without calling Phase 49. The bridge is read-only for state/event targets and compensates dependency mutations and errors only when targets changed; unchanged dependency errors perform zero writes. It does not duplicate Phase 42, 36, 29, or 21, invoke providers or tools directly, transition or persist execution results, retry, continue automatically, finalize terminal states, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 62: prepared-start persistence routing phase bridge reentry

Phase 62 accepts exactly one Phase 61 result. Exact `PreparedStepExecutionStart` and employee are delegated exactly once to Phase 55 with identical workflow, employee, and state/event targets, returning its identical `RunningStatePersistenceResult`. Only the exact proposed running-state persistence transition may change the state target; the event target remains byte-for-byte unchanged. Exact workflow completion and persisted failure require no employee, verify strict terminal state/history, and return their supplied object unchanged without calling Phase 55. The bridge compensates malformed, partial, unrelated, or unexpected dependency changes and errors. It does not duplicate Phase 48 or Phase 55, select or load employees, execute providers or tools, classify results, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 63: persisted-running execution routing phase bridge reentry

Phase 63 accepts exactly one Phase 62 result. Exact `RunningStatePersistenceResult`, original `PreparedStepExecutionStart`, matching employee, resolved tools, `OpenAIApiKey`, valid execution approval, and transport are validated against the persisted running state and predecessor history, then delegated exactly once to Phase 56 with identical argument identity. The state and event targets must remain byte-for-byte unchanged; malformed results, dependency mutations, and unexpected errors are compensated safely, while exact safe Phase 56 errors are preserved. Exact workflow completion and persisted failure require all execution-only inputs to be absent, verify strict terminal state/history, and return their supplied objects unchanged without calling Phase 56. It does not duplicate Phase 49 or Phase 56, select employees, resolve tools, create credentials or approvals, invoke providers or tools directly, persist execution results, classify outcomes, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 64: executed-result transition persistence routing phase bridge reentry

Phase 64 accepts exactly one Phase 63 runtime result. Exact `StepRuntimeExecutionSuccess` or `StepRuntimeExecutionFailure` is validated against the persisted running state and predecessor history, then delegated exactly once to Phase 57 with identical workflow and state/event targets, returning its identical `WorkflowExecutionPersistenceResult`. Success permits only the exact succeeded state and one `step_succeeded` event; failure permits only the exact failed state and one `step_failed` event, with byte counts and history validated. Malformed, partial, unrelated, or reordered dependency changes are compensated to the original bytes, safe Phase 57 errors preserve identity, and unexpected errors are sanitized. Exact completion and persisted failure are unchanged stop routes without calling Phase 57. It does not duplicate Phase 50 or Phase 57, execute providers or tools, create runtime results, classify outcomes, decide progression, prepare steps, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 65: persisted terminal outcome classification routing phase bridge reentry

Phase 65 accepts exactly one Phase 64 `WorkflowExecutionPersistenceResult`. It validates the exact persisted terminal state/history and delegates once to Phase 58 with the identical result, workflow, and state/event targets, returning the exact `PersistedExecutionOutcome`. Exact `workflow_complete` and `persisted_failure` are strict read-only stop routes that do not call Phase 58. The boundary verifies byte counts and outcome identity, preserves safe dependency errors, sanitizes unexpected errors, compensates all detected mutations, and never retries. It does not duplicate Phase 51 or Phase 58 classification logic, execute providers or tools, persist transitions, route classified outcomes, decide progression, prepare steps, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 66: classified persisted outcome routing phase bridge continuation

Phase 66 accepts exactly one Phase 65 `PersistedExecutionOutcome` or `workflow_complete` decision. Exact persisted success and failure outcomes are validated against terminal state/history and delegated exactly once to Phase 59 with identical argument identity. Success accepts only the exact next-step or completion decision produced by Phase 59; failure accepts only the same supplied failure object. Exact workflow completion is a zero-call read-only stop. Dependency mutations and errors are compensated safely, unexpected details are sanitized, and retry is not performed. Phase 66 does not duplicate Phase 52 or Phase 59, execute providers or tools, persist transitions, prepare or execute steps, create approvals, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 67: approved next-step preparation phase bridge continuation

Phase 67 accepts exactly one Phase 66 `prepare_next_step`, `workflow_complete`, or `persisted_failure` result. The prepare route requires the exact approval and next employee, then delegates exactly once to the existing Phase 60 preparation boundary with identical object identity and accepts only its exact `PreparedWorkflowStep`. Terminal routes require no approval or employee, validate strict terminal state/history, and return the supplied object unchanged without delegation. Targets are inspected and captured independently; dependency errors, malformed returns, mutations, and rollback failures are classified safely, compensated where possible, and never retried. Phase 67 does not create approvals, select employees, start steps, persist transitions, execute providers or tools, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 68: prepared next-step start routing phase bridge continuation

Phase 68 accepts exactly one Phase 67 `PreparedWorkflowStep`, `workflow_complete`, or `persisted_failure` result. The prepared route requires the exact matching `EmployeeDefinition`, validates the predecessor terminal history, then delegates exactly once to the existing Phase 61 start-routing boundary with identical object identity and accepts only its exact `PreparedStepExecutionStart`. Terminal routes require no employee, validate strict terminal state/history, and return the supplied object unchanged without delegation. Targets are inspected and captured independently; dependency errors, malformed returns, mutations, and rollback failures are classified safely, compensated where possible, and never retried. Phase 68 does not select employees, persist running state, execute providers or tools, classify results, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 69: prepared start persistence routing phase bridge continuation

Phase 69 accepts exactly one Phase 68 `PreparedStepExecutionStart`, `workflow_complete`, or `persisted_failure` result. The start route requires the exact matching `EmployeeDefinition`, validates the predecessor terminal history, then delegates exactly once to the existing Phase 62 persistence-routing boundary. It permits only the exact running-state persistence effect, validates the exact persisted state bytes and byte count, and returns the exact `RunningStatePersistenceResult`. Terminal routes require no employee, validate strict terminal state/history, and return the supplied object unchanged without delegation. Unrelated changes, malformed results, dependency errors, and rollback failures are classified safely and compensated where possible; retry is never performed. Phase 69 does not select employees, execute providers or tools, classify results, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 70: persisted running execution routing phase bridge continuation

Phase 70 accepts exactly one Phase 69 `RunningStatePersistenceResult`, `workflow_complete`, or `persisted_failure` result. The execution route revalidates the exact running state, predecessor history, execution inputs, and persistence byte count, then delegates exactly once to the existing Phase 63 routing boundary with identical argument identity. Only a matching exact runtime success/failure with unchanged state and events is accepted. Terminal routes require all execution-only inputs to be absent, validate strict terminal history, and stop without delegation. State and events are checked independently; malformed returns, dependency errors, mutations, and rollback failures are classified safely and compensated where possible, without retry. Phase 70 does not select employees, execute providers or tools, classify outcomes, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 71: executed-result transition persistence routing phase bridge continuation

Phase 71 accepts exactly one Phase 70 runtime result, `workflow_complete`, or `persisted_failure`. Exact runtime success/failure is validated against the persisted running state and predecessor history, then delegated exactly once to Phase 64 with identical result, workflow, and state/event target identity. Only the exact terminal transition persistence result is accepted: success appends one valid `step_succeeded` event and writes the exact succeeded state; failure appends one valid `step_failed` event and writes the exact failed state. Paths, byte counts, history order, and transition fields are validated. Completion and persisted failure are strict zero-call read-only stop routes. Malformed returns, partial or unrelated mutations, safe/unexpected errors, and rollback failures are classified safely and compensated where possible; retry is never performed. Phase 71 does not execute providers or tools, create runtime results, classify outcomes, decide progression, prepare steps, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 72: persisted outcome classification routing phase bridge continuation

Phase 72 accepts exactly one Phase 71 `WorkflowExecutionPersistenceResult`, `workflow_complete`, or `persisted_failure`. The persistence route requires identical state/event Path objects, exact terminal state/history, byte counts, and the single appended terminal event, then delegates exactly once to Phase 65 with identical argument identity. Succeeded persistence accepts only `persisted_success`; failed persistence accepts only a matching valid `persisted_failure`. Completion and persisted failure are strict zero-call read-only stop routes. Malformed returns, dependency mutations, safe/unexpected errors, and rollback failures are classified safely and compensated where possible; retry is never performed. Phase 72 does not execute providers or tools, persist transitions, duplicate classification logic, route outcomes, decide progression, prepare steps, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 78: executed-result transition persistence routing phase bridge cycle continuation

Phase 78 accepts exactly one Phase 77 runtime success/failure, `workflow_complete`, or `persisted_failure`. It prevalidates inputs, snapshots both targets, calls the injected Phase 71 function directly exactly once with identical argument identity, and validates the exact persistence result and persisted transition itself. Safe Phase 71 errors preserve identity after unchanged targets or successful compensation; malformed effects are compensated with both restoration attempts. Completion and persisted failure are strict zero-call read-only stop routes. Phase 78 does not execute providers or tools, create runtime results, classify outcomes, decide progression, prepare steps, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 79: persisted outcome classification routing phase bridge cycle continuation

Phase 79 accepts exactly one Phase 78 `WorkflowExecutionPersistenceResult`, `workflow_complete`, or `persisted_failure`. A valid persistence result is checked against the exact terminal state/history and byte counts, then delegated exactly once to Phase 72 with identical argument identity. Only the exact matching `PersistedExecutionOutcome` is returned unchanged. Completion and persisted failure are strict zero-call read-only stop routes. Dependency errors, malformed returns, mutations, and rollback failures are classified safely and compensated with both restoration attempts where possible; safe error identity is preserved and retry is never performed. Phase 79 does not select employees, resolve tools, create credentials or approvals, invoke providers or tools, create runtime results, persist transitions, duplicate classification logic, route classified outcomes, decide progression, prepare next steps, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 80: classified outcome routing phase bridge cycle continuation

Phase 80 accepts exactly one Phase 79 `PersistedExecutionOutcome`, `workflow_complete`, or `persisted_failure`. A persisted success is checked against the exact succeeded terminal state/history, then delegated exactly once to Phase 73 with identical argument identity; only the exact matching `prepare_next_step` or `workflow_complete` decision is returned unchanged. Persisted failure and workflow completion are strict zero-call read-only stop routes. Dependency errors, malformed decisions, mutations, and rollback failures are classified safely and compensated with both restoration attempts where possible; safe error identity is preserved and retry is never performed. Phase 80 does not select employees, resolve tools, create credentials or approvals, invoke providers or tools, create runtime results, persist transitions, classify persisted outcomes, prepare or execute the next step, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 81: approved next-step preparation phase bridge cycle reentry continuation

Phase 81 accepts exactly one Phase 80 `prepare_next_step`, `workflow_complete`, or `persisted_failure`. A prepare decision requires the exact approval and next employee, validates the succeeded terminal state/history, and delegates exactly once to Phase 74 with identical argument identity. Only the exact matching `PreparedWorkflowStep` is returned. Completion and persisted failure require absent approval and employee and are strict zero-call read-only stop routes. Dependency errors, malformed prepared steps, mutations, and rollback failures are classified safely and compensated with both restoration attempts where possible; safe error identity is preserved and retry is never performed. Phase 81 does not create approvals, select/load employees, start steps, persist state, execute providers/tools, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 82: prepared next-step start routing phase bridge cycle reentry continuation

Phase 82 accepts exactly one Phase 81 `PreparedWorkflowStep`, `workflow_complete`, or `persisted_failure`. A prepared step requires the exact employee and the immediately preceding succeeded terminal state/history, then delegates exactly once to Phase 75 with identical argument identity in canonical order `(result, workflow, employee, state_path, events_path)`. Only the exact matching `PreparedStepExecutionStart` is returned. Completion and persisted failure require an absent employee and are strict zero-call read-only stop routes. Dependency errors, malformed starts, mutations, and rollback failures are classified safely and compensated with both restoration attempts where possible; safe error identity is preserved and retry is never performed. Phase 82 does not select employees, persist running state, execute providers/tools, classify results, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 89: prepared next-step start dispatch phase bridge cycle reentry continuation

Phase 90 accepts exactly one Phase 89 `PreparedStepExecutionStart`,
`workflow_complete`, or `persisted_failure`. The prepared-start route requires
the exact employee and predecessor succeeded history, then directly delegates
once to Phase 83 in canonical five-argument order. Only the exact Phase 83
running-state persistence transition is accepted; state bytes must match the
running state, events must remain unchanged, and the exact persistence result
is returned. Completion and persisted failure require no employee and return
their supplied objects unchanged without calling Phase 83. Errors and invalid
dependency mutations are detail-safe and compensated on both targets without
retry. Phase 90 does not select employees, execute providers/tools, classify
outcomes, call Phase 76, continue automatically, finalize, schedule, loop,
parallelize, or add paid CLI/GUI behavior.

Phase 89 accepts exactly one Phase 88 `PreparedWorkflowStep`, `workflow_complete`, or `persisted_failure`. A prepared step requires the exact employee and immediately preceding succeeded terminal state/history, then dispatches exactly once to Phase 82 with canonical identity and order `(result, workflow, employee, state_path, events_path)`, returning the exact `PreparedStepExecutionStart`. Completion and persisted failure require an absent employee and are strict zero-call unchanged stop routes. Dependency errors, malformed starts, mutations, and rollback failures are classified safely and compensated with both restoration attempts where possible; safe error identity is preserved and retry is never performed. Phase 89 does not select or load employees, persist running state, execute providers/tools, classify results, call Phase 75 directly, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 96: prepared next-step start dispatch continuation boundary

Phase 96 accepts exactly one Phase 95 `PreparedWorkflowStep`, `workflow_complete`, or `persisted_failure`. A prepared step requires exact workflow/employee models, the immediately preceding succeeded terminal state/history, and regular distinct targets, then directly delegates once to Phase 89 in canonical five-argument order and returns its exact `PreparedStepExecutionStart`. Completion and persisted failure require an absent employee and are strict zero-call unchanged stop routes. Dependency errors, malformed starts, target mutations, and rollback failures are detail-safe, compensated on both targets where possible, and never retried. Phase 96 does not select employees, persist running state, execute providers/tools, classify results, call Phase 82 directly, continue automatically, finalize, schedule, loop, parallelize, or add paid CLI/GUI behavior.

## Phase 97: prepared start persistence dispatch continuation boundary

Phase 97 accepts exactly one Phase 96 `PreparedStepExecutionStart`, `workflow_complete`, or `persisted_failure`. A prepared start requires exact request/running-state, workflow, employee, predecessor succeeded history, and regular distinct targets, then directly delegates once to Phase 90 in canonical five-argument order. Only the exact permitted running-state persistence transition and matching `RunningStatePersistenceResult` are accepted; state bytes and byte count must match the proposed running state and events must remain unchanged. Completion and persisted failure require an absent employee and are strict zero-call unchanged stop routes. Dependency errors, malformed persistence results, target mutations, and rollback failures are detail-safe, compensated on both targets where possible, and never retried. Phase 97 does not select employees, execute providers/tools, classify outcomes, call Phase 83 directly, continue automatically, finalize, schedule, loop, parallelize, or add paid CLI/GUI behavior.

## Phase 84: persisted-running execution routing phase bridge cycle reentry continuation

Phase 84 accepts exactly one Phase 83 `RunningStatePersistenceResult`, `workflow_complete`, or `persisted_failure`. A running persistence result requires all exact execution inputs and the strict running state/history, then delegates exactly once to Phase 77 with the canonical ten-argument identity and returns its exact runtime success/failure. Completion and persisted failure require every execution-only input to be absent and are strict zero-call read-only stop routes. Dependency errors, malformed returns, mutations, and rollback failures are classified safely and compensated with both restoration attempts where possible; safe error identity is preserved and retry is never performed. Phase 84 does not select employees, resolve tools, create credentials or approvals, invoke providers/tools, persist transitions, classify outcomes, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 91: persisted-running execution dispatch phase bridge cycle reentry continuation

Phase 91 accepts exactly one Phase 90 `RunningStatePersistenceResult`,
`workflow_complete`, or `persisted_failure`. A running persistence result
requires every exact execution input, persisted running state/history, and
target contract, then delegates once to Phase 84 in canonical ten-argument
order and returns its exact runtime success/failure. Completion and persisted
failure require every execution-only input to be absent and are unchanged,
zero-call stop routes. Dependency errors, malformed runtime results, target
mutations, and rollback failures are detail-safe and compensated with both
restoration attempts where possible; safe errors preserve identity and Phase
84 is never retried. Phase 91 does not resolve tools, create credentials or
approvals, invoke providers/tools, persist transitions, classify outcomes,
call Phase 77, continue automatically, finalize, schedule, loop, parallelize,
or add paid CLI/GUI behavior.

## Phase 85: executed-result transition persistence routing phase bridge cycle reentry continuation

Phase 85 accepts exactly one Phase 84 runtime success/failure, `workflow_complete`, or `persisted_failure`. A runtime result requires exact persisted running state/history and target validation, then delegates exactly once to the injected Phase 78 function with canonical identity and order `(result, workflow, state_path, events_path)`, returning the exact `WorkflowExecutionPersistenceResult`. Completion and persisted failure are strict zero-call read-only stop routes. Dependency errors, malformed results, mutations, and rollback failures are classified safely and compensated with both restoration attempts where possible; safe error identity is preserved and retry is never performed. Phase 85 does not execute providers/tools, create runtime results, call Phase 71 directly, classify outcomes, decide progression, prepare steps, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 92: executed-result transition persistence dispatch phase bridge cycle reentry continuation

Phase 92 accepts exactly one Phase 91 runtime success/failure, `workflow_complete`, or `persisted_failure`. Runtime results require exact persisted running state/history and target validation, then delegate exactly once to the injected Phase 85 function with canonical identity and order `(result, workflow, state_path, events_path)`, returning its exact `WorkflowExecutionPersistenceResult`. The runtime route permits only the exact existing Phase 85/78/71 terminal transition persistence; malformed or partial persistence, unrelated state/history writes, invalid results, and incorrect paths or byte counts are rejected. Completion and persisted failure are strict zero-call unchanged stop routes. Dependency errors, mutations, and rollback failures are detail-safe and compensated with both restoration attempts where possible; safe error identity is preserved and retry is never performed. Phase 92 does not call Phase 78 directly, execute providers/tools, create runtime results, classify outcomes, decide progression, prepare steps, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 86: persisted outcome classification routing phase bridge cycle reentry continuation

Phase 86 accepts exactly one Phase 85 `WorkflowExecutionPersistenceResult`, `workflow_complete`, or `persisted_failure`. A persistence result requires exact target identity, terminal state/history, byte counts, and final event validation, then delegates exactly once to the injected Phase 79 function with canonical identity and order `(result, workflow, state_path, events_path)`, returning the exact `PersistedExecutionOutcome`. Completion and persisted failure are strict zero-call read-only stop routes. Dependency errors, malformed outcomes, mutations, and rollback failures are classified safely and compensated with both restoration attempts where possible; safe error identity is preserved and retry is never performed. Phase 86 does not execute providers/tools, create runtime results, persist transitions, call Phase 72 directly, duplicate classification logic, route classified outcomes, decide progression, prepare steps, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 87: classified outcome routing phase bridge cycle reentry continuation

Phase 87 accepts exactly one Phase 86 `PersistedExecutionOutcome` or `workflow_complete`. Only `persisted_success` delegates exactly once to the injected Phase 80 function with identical canonical arguments and returns its exact matching progression decision. `persisted_failure` and completion are strict zero-call read-only stop routes. Dependency errors, malformed decisions, mutations, and rollback failures are classified safely and compensated with both restoration attempts where possible; safe error identity is preserved and retry is never performed. Phase 87 does not classify outcomes, call Phase 73 directly, prepare or execute steps, persist transitions, invoke providers/tools, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 88: approved next-step preparation routing phase bridge cycle reentry continuation

Phase 88 accepts exactly one Phase 87 `WorkflowProgressionDecision` or `PersistedExecutionOutcome`. A `prepare_next_step` decision requires the exact approval and matching next employee, validates the succeeded terminal state/history, and delegates exactly once to the existing Phase 81 boundary with identical six-argument identity and order, returning its exact `PreparedWorkflowStep`. Completion and persisted failure require absent approval and employee and are strict zero-call read-only stop routes. Dependency errors, malformed prepared steps, mutations, and rollback failures are classified safely and compensated with both restoration attempts where possible; safe error identity is preserved and retry is never performed. Phase 88 does not call Phase 74 directly, create approvals, select employees, start steps, persist state, execute providers/tools, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 93: persisted outcome classification dispatch phase bridge cycle reentry continuation

Phase 93 accepts exactly one Phase 92 `WorkflowExecutionPersistenceResult`,
`workflow_complete`, or `persisted_failure`. A persistence result validates the
strict terminal state/history, byte counts, and terminal event, then directly
delegates once to Phase 86 in canonical `(result, workflow, state_path,
events_path)` order and returns its exact `PersistedExecutionOutcome`.
Completion and persisted failure are unchanged zero-call stop routes. Dependency
errors, malformed outcomes, target mutations, and rollback failures are
detail-safe, compensated on both targets where possible, and never retried.
Phase 93 does not execute providers/tools, persist transitions, call Phase 79
directly, duplicate classification, route outcomes, decide progression, prepare
steps, retry, continue automatically, finalize, schedule, loop, parallelize, or
add paid CLI/GUI behavior.

## Phase 94: classified outcome dispatch phase bridge cycle reentry continuation

Phase 94 accepts exactly one Phase 93 `PersistedExecutionOutcome` or
`workflow_complete`. A `persisted_success` validates the succeeded terminal
state/history and directly delegates once to Phase 87 in canonical `(result,
workflow, state_path, events_path)` order, returning its exact progression
decision. `persisted_failure` and workflow completion are unchanged zero-call
stop routes. Dependency errors, malformed decisions, target mutations, and
rollback failures are detail-safe, compensated on both targets where possible,
and never retried. Phase 94 does not select employees, resolve tools, create
credentials or approvals, invoke providers/tools, create runtime results,
persist transitions, classify outcomes, call Phase 80 directly, prepare or
execute steps, retry, continue automatically, finalize, schedule, loop,
parallelize, or add paid CLI/GUI behavior.

## Phase 83: prepared start persistence routing phase bridge cycle reentry continuation

Phase 83 accepts exactly one Phase 82 `PreparedStepExecutionStart`, `workflow_complete`, or `persisted_failure`. A prepared start requires the exact employee and the immediately preceding succeeded terminal state/history, then delegates exactly once to Phase 76 with identical argument identity in canonical order `(result, workflow, employee, state_path, events_path)`, returning its exact `RunningStatePersistenceResult`. Completion and persisted failure require an absent employee and are strict zero-call unchanged stop routes. Only the exact Phase 76/69/62 running-state persistence transition is permitted; malformed returns, missing or partial writes, unrelated writes, event mutation, and invalid history are rejected. Dependency errors, mutations, and rollback failures are detail-safe and compensated with both restoration attempts where possible; no retry is performed. Phase 83 does not select employees, execute providers/tools, classify results, retry, continue automatically, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## 構成

```text
definitions (YAML / Markdown)
        |
        v
validation
        |
        v
planning: ExecutionPlan -> StepExecutionRequest
        |
        v
invocation: ModelInvocationRequest
        ↓
OpenAIResponsesRequest
        │
        ├─ allowed_tool_names
        │       ↓
        │   tools: Tool Catalog
        │       ↓
        │   ToolDefinition
        │       ↓
        │   OpenAI Responses Tool Adapter
        │       ↓
        │   OpenAIResponsesFunctionTool
        │
        └──────────────┐
                       ↓
          OpenAI Responses Payload Adapter
                       ↓
             OpenAIResponsesPayload
                       ↓
    OpenAI Responses Dictionary Payload Adapter
                       ↓
       JSON-compatible Python dictionary
                       ↓
       OpenAI Responses JSON Serializer
                       ↓
                 JSON string
                       ↓
 OpenAI Responses HTTP Request Template Builder
                       ↓
      OpenAI Responses Authentication Boundary
                       ↓
    OpenAI Responses HTTPS Transport Boundary
                       ↓
    OpenAIResponsesRawHttpResponse
                       ↓
 OpenAI Responses HTTP Response Boundary
                       ↓
 success response | API error response | invalid response error
                       ↓
 OpenAI Responses Output Text Boundary
                       ↓
       OpenAIResponsesOutputText
                       ↓
 Model Invocation Result Boundary
                       ↓
 ModelInvocationSuccess | ModelInvocationFailure
                       ↓
 Explicit Paid-Execution Approval Boundary
                       ↓
 Guarded OpenAI Provider Execution Boundary
                       ↓
 Single-Step Runtime Execution Result Boundary
                       ↓
 Pure Workflow State Transition and Runtime Event Boundary
                       ↓
    Compensatable State and Event Persistence Boundary
                       ↓
 Strict State and Event Loading Boundary
                       ↓
      future controlled progression and replay validation

explicit environment mapping or current process environment
                       ↓
 OpenAI API Key Environment Acquisition Boundary
                       ↓
       OpenAIApiKey ───────────────→ Authentication Boundary
```

| 層 | 責務 |
| --- | --- |
| `definitions` | 社員・ワークフローのテキスト定義をモデル化する。 |
| `planning` | 検証済み定義を、順序・担当employee・step instructionsを明示した不変の実行計画へ変換し、1 step分の構造化実行要求を生成する。AI実行、状態、保存は扱わない。 |
| `invocation` | `StepExecutionRequest` を、モデル・system instructions・task instructions・allowed toolsだけからなるprovider非依存の不変なモデル呼び出し要求へ変換し、provider固有の安全なoutcomeをfuture runtime向けの不変なsuccess/failure結果へ正規化する。明示的paid-execution approvalは、provider、caller metadata、入力に束縛した決定的fingerprintだけを持ち、credential、HTTP、response、保存、時刻、policyを扱わない。planning上のworkflow、step、employee文脈は持ち込まず、prompt結合、provider固有message形式、AI実行、retry policy、state mutationは扱わない。 |
| `providers.openai` | `ModelInvocationRequest` をOpenAI Responses API用の不変な実行前情報 `OpenAIResponsesRequest` へ純粋に変換する。`system_instructions` は `instructions`、`task_instructions` は `input` に対応し、文字列を結合・加工しない。`allowed_tool_names` は定義順の未解決tool名である。OpenAI Responses Tool Adapterは解決済み`ToolDefinition`を静的な`OpenAIResponsesFunctionTool`へ変換し、OpenAI Responses Payload Adapterは基本request情報と解決済みtool schemaを`OpenAIResponsesPayload`へ統合する。Dictionary Payload AdapterはそれをJSON互換Python辞書へ、JSON Serializerはその辞書を決定的なJSON文字列へ、HTTP Request Template Builderは非秘密headerを持つ未認証templateへ変換する。Authentication Boundaryは明示入力のAPI keyをBearer headerへ付加し、Environment Acquisition Boundaryだけが`OPENAI_API_KEY`を明示mappingまたはprocess environmentから取得する。HTTPS Transport Boundaryは認証済みrequestを1回だけ送信し、Response Boundaryはraw responseを不変successまたはAPI-error dataへ分類し、Output Text Boundaryはsuccess responseから対応するmessage output textだけを不変text結果へ抽出する。credential persistenceやruntime処理は扱わない。 |
| `engine` | 定義済みの状態遷移、検証、再試行を決定的に管理する。 |
| `runtime` | すでに準備済みかつ承認済みの単一step inputを、既存guarded OpenAI executionへ一度だけ委譲し、workflow/step/employee identityとprovider-independent resultを不変dataとして返す。completed resultを明示`running` stateと検証して、不変next stateと1つのsafe runtime eventへ純粋に変換する。next-step選択、event/artifact保存、retry、tool実行は扱わない。 |
| `storage` | completed transitionのnext stateを決定的JSONへ、runtime eventを決定的JSONLへ保存する。両targetの事前bytesを捕捉し、handled partial failure時には両targetを補償復元する。load/replay、locks、crash recovery、workflow進行は扱わない。 |
| `tools` | provider非依存の静的なTool Catalogを保持し、未解決tool名を完全一致で`ToolDefinition`へ決定的に解決する。`ToolDefinition`はHTTP payloadでも実行可能オブジェクトでもなく、provider schema変換、executor、Runtimeは後続Phaseで扱う。 |

## 境界と不変条件

- 実行時は、開始時点の定義を保存してから処理する。
- エンジンは定義にない遷移を作らない。
- 検証失敗は AI 実行前に報告する。
- 実行計画のstep順はworkflow YAMLの`steps`配列順だけで決まり、計画生成は定義を補正・並び替え・暗黙補完しない。
- 実行計画は元の定義モデルやファイル配置場所への参照を持たない。provenance、定義スナップショット、監査情報の保存は後続Phaseで扱う。
- 実行要求は実行アダプタへの不変の入力であり、runtime stateではない。元定義やファイル配置場所への参照を持たず、prompt組立、AI実行、tool解決、保存を扱わない。
- モデル呼び出し要求はprovider adapterへの不変の入力であり、model、分離されたsystem instructionsとtask instructions、定義順のallowed toolsだけを持つ。
- OpenAI Responses Adapterは純粋な変換層であり、`OpenAIResponsesRequest` はHTTP payloadでもwire formatでもない。model、instructions、input、未解決のallowed tool namesだけを保持し、SDK、認証、通信、tool schema解決、AI実行を扱わない。
- Tool Catalogはprovider非依存であり、`ModelInvocationRequest.allowed_tools`や`OpenAIResponsesRequest.allowed_tool_names`を置き換えない。未解決名を順序・重複そのままで`ToolDefinition`へ解決するだけで、provider schema、tool executor、Runtimeを扱わない。
- OpenAI Responses Tool Adapterは、`ToolDefinition`を`OpenAIResponsesFunctionTool`へ変換する純粋なprovider固有層である。tool typeは`function`、parameters typeは`object`、`additional_properties`と`strict`は`False`に固定し、propertiesとrequired名は順序・重複を保持したtupleで保持する。dict、JSON文字列、HTTP request bodyを生成しない。後続のrequest payload adapterだけがHTTP送信用のdictまたはJSON payloadを担当し、さらに後続のRuntimeがAPI呼び出しと結果処理を担当する。
- OpenAI Responses Payload Adapterは、`OpenAIResponsesRequest`と解決済み`OpenAIResponsesFunctionTool`を`OpenAIResponsesPayload`へ統合する純粋なprovider固有層である。payloadはmodel、instructions、input、toolsだけを保持し、未解決の`allowed_tool_names`、Catalog、HTTP情報、Runtime情報を保持しない。
- OpenAI Responses Dictionary Payload Adapterは、`OpenAIResponsesPayload`をJSON互換Python辞書へ決定的に変換する。toolsとrequiredはlistへ変換し、`additional_properties`は`additionalProperties`へ写像する。property名の重複は後の値で上書きするが、Python辞書の最初のkey位置を保持する。JSON文字列化、HTTP request body送信、API呼び出し、Runtimeは扱わず、future JSON serializerが文字列化を、future runtimeがHTTP通信、API呼び出し、response処理を担当する。
- OpenAI Responses JSON Serializerは、JSON互換Python辞書を変更せずに決定的なJSON文字列へ変換する。compact形式は不要な空白なし、pretty形式は2 space indentとし、入力dictの挿入順序を維持して`ensure_ascii=False`でUnicodeを保持する。`None`はJSONの`null`として表現する。後続のHTTP Request Template Builderがmethod、endpoint、非秘密headers、bodyを配置し、future runtimeがHTTP通信、API呼び出し、response処理を担当する。
- OpenAI Responses HTTP Request Template Builderは、Phase 11のJSON文字列を変更せず、`POST`、Responses endpoint、順序付きの非秘密`Content-Type` header、bodyからなる不変templateへ配置する。API key、Authorization、認証、HTTP通信、timeout、response処理は扱わない。後続のAuthentication Boundaryが認証情報を付加し、future HTTP Transportが通信・timeout・通信エラーを担当し、future Response Boundaryがresponseを受信・検証・解析する。
- OpenAI Responses Authentication Boundaryは、明示入力の不変`OpenAIApiKey`を使い、未認証templateの既存header順序を保持して最後にBearer Authorization headerを付加する。keyは通常の表現でマスクし、空文字列とCR/LFを拒否する。環境・設定ファイル・keyring・CLIからcredentialを取得せず、HTTP通信も行わない。future HTTP Transportが通信・timeout・通信エラーを、future Response Boundaryがresponseを受信・検証・解析する。
- OpenAI API Key Environment Acquisition Boundaryは、provider固有の`OPENAI_API_KEY`だけを読み、既存の`OpenAIApiKey`を返す。caller-supplied mappingがある場合はそれだけを参照し、`None`の場合に限りこのモジュール内でprocess environmentを参照する。値を変形せず、`.env`、設定ファイル、keyring、prompt、CLI、credential persistence、requestの自動認証、HTTP通信は扱わない。
- OpenAI Responses HTTPS Transport Boundaryは、認証済みrequestをPython標準ライブラリで同期的に1回送信し、status、reason、順序・重複を保持したheaders、未解析body bytesからなる不変raw responseを返す。completed HTTP statusは解釈せず返し、transport failureは秘密を含まないprovider固有エラーにする。connectionは成功・失敗のどちらでもcloseし、retry、redirect、timeout設定、response JSON parsing、tool実行、Runtimeは扱わない。
- OpenAI Responses HTTP Response Boundaryは、raw response bodyをUTF-8で1回decodeしJSONを1回parseして、2xxを最小検証済みsuccess responseへ、非2xxを最小検証済みAPI-error responseへ分類する。payloadは完全に保持し再帰的に不変化し、最初の`x-request-id`を取得する。completed非2xxを例外化せず、無効なbodyや契約は安全なinvalid-response errorにする。usage、tool call、retry、persistence、Runtimeは扱わない。
- OpenAI Responses Output Text Boundaryは、`OpenAIResponsesSuccessResponse.output`を順に走査して、`message`の`content`にある`output_text`の文字列だけを加工せず不変の`OpenAIResponsesOutputText`へ抽出する。対応しないitemは無視し、対応すると主張するmessage/content構造が不正な場合はpayloadやtextを露出しない安全なinvalid-output errorにする。raw response、JSON decode・parse、API error、credentials、HTTP通信、usage、tool call、persistence、Runtimeは扱わない。
- Model Invocation Result Boundaryは、provider固有の安全なsuccess output、API error、transport error、invalid-response error、invalid-output error、invalid execution input、approval failureを、provider-independent かつ不変の`ModelInvocationSuccess`または`ModelInvocationFailure`へ正規化する。failure categoryは`api_error`、`transport_error`、`invalid_response`、`invalid_output`、`invalid_request`、`approval_required`だけであり、retryabilityやtransient/permanentを推測しない。raw payload、request body、headers、credential、exception internals、usage、tool、persistence、runtime stateは保持・解釈しない。
- Explicit Paid-Execution Approval Boundaryは、caller suppliedな不変`ModelInvocationExecutionApproval`を、provider、non-empty caller metadata、現在の`ModelInvocationRequest`とresolved `ToolDefinition` tupleから再計算するSHA-256 fingerprintへ束縛して検証する。fingerprintはmodel、system/task instructions、ordered allowed tool names、ordered tool definitionsとparameter定義を含み、API key、authorization、HTTP body、environment、response、時刻、random値を含まない。false、provider mismatch、stale fingerprint、空metadataは例外詳細を露出しない`approval_required`として正規化される。自動approval、approval persistence、expiration、role/policy、CLI paid executionは扱わない。
- Guarded OpenAI Provider Execution Boundaryは、明示的な`ModelInvocationRequest`、位置順まで一致する解決済み`ToolDefinition` tuple、明示的な`OpenAIApiKey`、明示的なapprovalを入力にして、tool一致、approval検証、既存のrequest、tool schema、payload、JSON、HTTP template、authentication、HTTPS transport、response、output、result正規化の境界を順に一度だけ合成する。tool一致失敗はapproval前に安全な`invalid_request`結果へ、approval失敗は認証・通信前に安全な`approval_required`結果へ正規化し、expected provider-specific safe errorだけを正規化する。環境credential取得、retry、tool実行、usage、persistence、runtime state、CLI API実行は扱わない。
- Single-Step Runtime Execution Result Boundaryは、明示的な`StepExecutionRequest`、既存の`ModelInvocationRequest`、resolved tool tuple、approvalからなる不変inputを受け取る。model、employee instructions、step instructions、allowed toolsが既存のrequest生成契約どおり完全一致することを最初に検証し、不一致は詳細を露出しない`invalid_request` runtime failureへ正規化する。整合する入力だけを既存guarded OpenAI executionへ一度だけ委譲し、workflow ID、step ID、既存の1始まりstep index、employee IDと既存`ModelInvocationSuccess`または`ModelInvocationFailure`を不変wrapperとして保持する。API keyはinput/resultに保存しない。state transition、next-step selection、event/artifact保存、retry、tool実行、agent loop、CLI paid executionは扱わない。future state-transition boundaryがこのresultを消費する。
- Pure Workflow State Transition and Runtime Event Boundaryは、completed `StepRuntimeExecutionResult`と明示的`running` `WorkflowExecutionState`のworkflow ID、step ID、既存の1始まりstep index、employee IDを完全一致で検証する純粋boundaryである。不一致は詳細を露出しないsafe input errorになる。successは`running -> succeeded`としてcurrent step IDを一度だけappendし、failureは`running -> failed`としてcompleted IDsを保持してfailure categoryを記録する。どちらもidentity、safe provider metadata、empty output textを含むevent dataを不変に返す。ここでの`succeeded`は単一step execution stateの完了であり、multi-step workflow final completionではない。timestamp、event ID、persistence、next-step selection、provider呼出、retry、tool execution、agent loop、CLI paid executionは扱わず、future persistence boundaryがstateとeventをatomicまたはcompensatableに保存する。
- Compensatable State and Event Persistence Boundaryは、明示`WorkflowExecutionTransition`のnext stateを決定的UTF-8 JSONへ、runtime eventを1件の決定的UTF-8 JSONL recordへ保存する。workflow/step/employee identity、status、event type、distinct file targetを検証し、変更前に両targetの存在と正確なbytesを捕捉する。state replacement後のevent appendを行い、handled filesystem failureではevents、stateの決定順で両targetを元のbytesへ復元し、元は存在しなかったtargetを削除する。rollbackの一方が失敗しても両方を試み、safe rollback errorへ分類する。これはin-process compensationでありcrash-safe cross-file transaction、fsync保証、locks、concurrency、load/replay、next-step orchestration、provider呼出、retry、tool execution、CLI paid executionは扱わない。
- Strict State and Event Loading Boundaryは、caller suppliedなPhase 23 target pathsからstate JSONとevent JSONLをread-onlyで読み、UTF-8、duplicate key、完全なfield集合、型、有限値、event意味制約を厳格に検証してPhase 22の不変`WorkflowExecutionState`と順序付き`RuntimeStepEvent` tupleへ再構築する。JSONLのblank record、未知field、欠損field、partial final recordは拒否する。regular fileを指すsymbolic linkは許可し、directory targetは拒否する。終端stateは最後のevent、identity、status、failure category、completed stepと照合し、安全なdata/load/inconsistency errorだけを返す。空event fileは明示的に`ready`または`running` stateに限り許可する。repair、migration、書込み、resume、next-step selection、retry、tool/provider実行、CLI paid execution、GUIは扱わない。Phase 21 execution、Phase 22 transition、Phase 23 persistence、Phase 24 loadingはすべて別の明示呼出であり、後続boundaryだけがhuman-approvedなcontrolled next-step preparationを判断できる。
- Pure Workflow Progression Decision Boundaryは、検証済み`WorkflowDefinition`とPhase 24の`LoadedWorkflowExecutionHistory`だけを入力に、persisted stateのworkflow ID、current step index/ID/employee、completed step IDと定義順序を現在のworkflow definitionへ照合する純粋boundaryである。staleまたは互換性のない履歴は、IDやinstructionなどを露出しない安全なcompatibility errorとして拒否する。`ready`と`running`は`not_progressable`、`failed`は`stopped_failed`、成功した終端stepは`workflow_complete`、成功した非終端stepは定義順の直後の一stepだけを示す`prepare_next_step`を返す。不変decisionはstateを変更せず、request構築、approval作成、provider/tool実行、persistence、automatic resume、retry、scheduler、CLI paid execution、GUIを行わない。Phase 21 execution、Phase 22 transition、Phase 23 persistence、Phase 24 loading、Phase 25 decisionは別の明示呼出であり、後続boundaryだけが明示承認済みdecisionを次step preparation requestへ変換できる。
- Approved Next-Step Preparation Boundaryは、Phase 25の`prepare_next_step` decision、明示的かつcurrent/next step identityへ完全に束縛されたapproval、正確なvalidated employee definitionを入力に、workflow/history/decision/approval/employeeの互換性を再検証する純粋boundaryである。返す不変`PreparedWorkflowStep`はworkflow/step/employee identity、1始まりindex、employee instructions、step instructions、model、順序を保持したallowed tool namesだけを含む。stale decision、別stepのapproval、employee mismatchは安全なerrorとして拒否する。provider request/payload、credential lookup、tool resolution/execution、state mutation、runtime event、persistence、retry、automatic continuation、CLI paid execution、GUIは扱わない。Phase 21 execution、Phase 22 transition、Phase 23 persistence、Phase 24 loading、Phase 25 decision、Phase 26 preparationは別の明示呼出であり、後続boundaryだけがprepared dataをcontrolled execution requestとrunning-state transitionへ変換できる。
- Pure Prepared-Step Execution Start Boundaryは、Phase 26の`PreparedWorkflowStep`とloaded historyを入力に、success state、workflow ID、1始まりの直後step index、必要なrequest dataを再検証して、provider-independent execution requestとproposed `running` stateを不変に返す。completed step IDsは正確に保持しfailure categoryはclearする。provider execution、credential、tool resolution、event、persistence、retry、automatic continuation、CLI paid execution、GUIは扱わず、後続boundaryがrunning stateを明示保存してからPhase 21を明示実行する。
- Explicit Running-State Persistence Boundaryは、Phase 27のproposed `running` stateだけをcaller suppliedなstate targetへ既存の決定的state JSON contractで安全に置換保存する。保存完了後にのみcallerがPhase 21を明示実行できる。start eventは作成せずruntime event fileを変更しない。provider execution、Phase 22 completed-result transition、completion/failure persistence、retry、automatic continuationは別の明示boundaryに保つ。
- Persisted-Start Single-Step Execution Boundaryは、Phase 27の`PreparedStepExecutionStart`、明示state target、検証済み`WorkflowDefinition`と正確な`EmployeeDefinition`、resolved tools、credential、approvalを入力にする。in-memory contractを検証してから既存の厳格state parserでtargetをread-onlyに読み、`running` status、failure categoryなし、Phase 27 stateとの完全一致を確認する。さらにworkflow ID、1-based current step index、current step ID、current employee IDをworkflow definitionへ照合する。確認後だけworkflow/step表示名をdefinitionから取得して既存の必須`StepExecutionRequest`を構築し、同一`ModelInvocationRequest`を既存Phase 21 `execute_openai_runtime_step()`へ一度だけ渡す。結果state/eventを保存せず、retry、自動継続、paid CLI、GUIも行わない。順序はPhase 25 decision → Phase 26 preparation → Phase 27 proposed start → Phase 28 running-state persistence → Phase 29 persisted-state verification + one Phase 21 call → later Phase 22 transition + Phase 23 completion/failure persistenceである。
- Executed-Step Transition Persistence Boundaryは、既存Phase 21の`StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`、明示state target、明示runtime-event targetを入力にする。strict state-only loaderでcurrent stateをread-onlyに再読込し、`running` status、failure categoryなし、workflow/step/index/employee identityを確認してから、既存Phase 22 `transition_workflow_execution_from_step_result()`を一度だけ呼ぶ。returned `WorkflowExecutionTransition`を再構築せず互換性確認し、既存Phase 23 `persist_workflow_execution_transition()`へ一度だけ渡して最終stateと一つのeventを補償付きで保存する。provider、credential、approval、tool解決、retry、progression、次step準備・実行、自動継続、paid CLI、GUIは扱わない。順序はPhase 25 decision → Phase 26 preparation → Phase 27 proposed start → Phase 28 running-state persistence → Phase 29 one Phase 21 call → Phase 30 reload running state + one Phase 22 transition + one Phase 23 persistence → later explicit progression decisionである。
- Persisted-Success Progression Decision Boundaryは、検証済み`WorkflowDefinition`と明示state/event targetを入力にするread-only boundaryである。既存Phase 24 strict history loaderでpersisted succeeded state、failure categoryなし、最新`step_succeeded` event、workflow identityを確認してから、既存Phase 25 `decide_workflow_progression()`へ一度だけ委譲し、既存immutable decisionをそのまま返す。承認、次step準備、running state作成、provider実行、persistence、retry、自動継続、paid CLI、GUIは扱わない。順序はPhase 25 → Phase 26 → Phase 27 → Phase 28 → Phase 29 → Phase 30 → Phase 31 persisted success reload + one Phase 25 decision → later explicit human approval/preparation or completion handlingである。
- Approved Next-Step Reentry Boundaryは、Phase 31の正確な`prepare_next_step` decision、新規に明示された同一next stepへのPhase 26 approval、検証済みworkflow/employee、caller supplied state/event targetsを入力にするread-only boundaryである。既存Phase 24 loaderでpersisted successと最新success eventを再読込してworkflow順序を検証し、decisionのcurrent/next identityとapprovalの完全一致を確認してから、既存Phase 26 `prepare_approved_next_workflow_step()`を一度だけ呼ぶ。返却された既存`PreparedWorkflowStep`はdecisionと照合し、同一objectを返す。provider request、running state、persistence、execution、retry、自動継続、paid CLI、GUIを作成せず、Phase 25/31も再呼出ししない。順序はPhase 25 → Phase 26 → Phase 27 → Phase 28 → Phase 29 → Phase 30 → Phase 31 persisted success reload + one Phase 25 decision → Phase 32 exact prepare_next_step decision + fresh approval + one Phase 26 preparation → later explicit Phase 27 start preparationである。
- Prepared-Step Start Reentry Boundaryは、Phase 32の正確な`PreparedWorkflowStep`、対応employee definition、workflow、caller supplied state/event targetsを入力にするread-only boundaryである。既存Phase 24 loaderでpersisted successと最新success eventを再読込してworkflow順序を検証し、prepared stepのidentity、instructions、model、allowed toolsを完全照合してから、既存Phase 27 `prepare_prepared_step_execution_start()`を一度だけ呼ぶ。返却された既存request/proposed running-state resultも照合し、同一objectを返す。running stateの保存、provider実行、credential/tool resolution、retry、自動継続、paid CLI、GUIを行わず、Phase 25、26、31、32を再呼出ししない。順序はPhase 31 persisted success + one Phase 25 decision → Phase 32 exact prepare_next_step + fresh approval + one Phase 26 preparation → Phase 33 exact PreparedWorkflowStep + one Phase 27 start preparation → later explicit Phase 28 running-state persistenceである。
- Prepared Running-State Persistence Reentry Boundaryは、Phase 33の正確なstart result、workflow/employee、caller supplied state/event targetsを入力にする。Phase 24 loaderでpersisted successを再読込し、start request/proposed running stateを完全照合して既存Phase 28 `persist_prepared_running_state()`を一度だけ呼ぶ。呼出し後はstrict state reload、result byte count、event targetのbyte-for-byte不変を検証して同一result objectを返す。provider、credential/tool resolution、execution、retry、自動継続、paid CLI、GUIを行わず、Phase 25–27、31–33を再呼出ししない。順序はPhase 31 persisted success + Phase 25 → Phase 32 fresh approval + Phase 26 → Phase 33 PreparedWorkflowStep + Phase 27 → Phase 34 exact start + Phase 28 persistence → later explicit Phase 29である。
- Persisted-Running Execution Reentry Boundaryは、Phase 33 start、persisted running state、workflow/employee、explicit Phase 29 inputsを受ける。strict state loaderでrunning stateとstartを照合して既存Phase 29を一度だけ呼び、returned Phase 21 result identityとstate bytes 不変を検証する。結果保存、transition、event append、retry、自動継続は行わない。
- Persisted-Running Execution Routing Reentry Boundaryは、正確なPhase 41 persistence resultとPhase 33 start、workflow/employee、明示実行入力を照合し、既存Phase 36を一度だけ呼ぶ。Phase 36を通じて最大一回の明示承認済みOpenAI executionだけを行える。strict state loaderでpersisted `running` stateとstartおよびbyte countを検証し、実行後のstate/event bytesが不変であることを確認する。注入依存がtargetを改変または削除した場合は変更されたtargetだけを呼出し前bytesへ補償復元してsafe errorにする。正確な`workflow_complete`は実行入力なしで同じdecisionを返し、Phase 36を呼ばない。credential取得、approval作成、結果保存、transition、event append、retry、自動継続、completion finalization、paid CLI/GUIは扱わない。
- Executed-Result Transition Persistence Reentry Boundaryは、正確な既存Phase 21/35 runtime result、検証済みworkflow、caller suppliedなstate/event targetを入力にする。Phase 24 strict state loaderで`running` stateを再読込し、workflow ID、current step/index/employee、completed-step prefixとresult identityを検証してから、既存Phase 30 `persist_executed_step_transition()`へ一度だけ委譲する。returned既存`WorkflowExecutionPersistenceResult`、strictly reloaded final state/history、一つの追加event、byte countを照合し、注入依存のpartial/wrong writeやevent replacementは両targetを呼出し前bytesへ補償復元してsafe errorにする。provider execution、credential/tool resolution、retry、progression、自動継続、paid CLI、GUIは扱わない。順序はPhase 31 persisted success + Phase 25 → Phase 32 fresh approval + Phase 26 → Phase 33 PreparedWorkflowStep + Phase 27 → Phase 34 start + Phase 28 persistence → Phase 35 strict running verification + Phase 29 once → Phase 36 exact result + Phase 30 once → later explicit persisted-history progression/failure handlingである。
- Executed-Result Transition Routing Reentry Boundaryは、正確なPhase 42 runtime success/failureを`persist_executed_result_transition_reentry()`へ正確に一度だけ渡し、同一のPhase 30 persistence resultを返す。terminal stateと一つのruntime eventの構築・保存は既存boundary内部のPhase 30 persistenceで行い、Phase 43自身は構築しない。strict running state/history、runtime identity、final terminal state、一つの追加event、byte countを検証し、dependencyのpartial/invalid writeまたはunexpected errorでは両targetを補償復元する。正確な`workflow_complete`はtargetをread-onlyで確認して同じdecisionを返し、retry、自動継続、progression、workflow completion finalization、paid CLI/GUIを行わない。順序はPhase 42 result → Phase 43 routing → runtime resultなら`persist_executed_result_transition_reentry()` + Phase 30 terminal transition/event、completionならunchanged stop → future explicit outcome routingである。
- Persisted Terminal Outcome Classification Routing Reentry Boundaryは、正確なPhase 43 persistence resultをtarget bytesとstrict terminal historyへ照合してから、`classify_persisted_execution_outcome_reentry()`を正確に一度だけ呼ぶread-only boundaryである。同じPhase 37 `PersistedExecutionOutcome` objectを返し、`workflow_complete`は分類せず同じdecisionを返す。Phase 38 routing、Phase 31 progression、next-step preparation、retry、自動継続、workflow completion finalization、paid CLI/GUIは行わない。
- Classified Persisted Outcome Routing Bridgeは、正確なPhase 44 `PersistedExecutionOutcome`をstrict terminal historyに照合してから、`route_persisted_execution_outcome_reentry()`（Phase 38）へ正確に一度だけ渡すread-only bridgeである。`workflow_complete`はPhase 38を呼ばず同じdecisionを返す。Phase 37 classificationやPhase 31 progressionを直接呼ばず、next-step preparation/execution、retry、自動継続、workflow completion/failure finalization、paid CLI/GUIは行わない。
- 依存方向は `provider-independent invocation model -> provider-specific adapter -> provider-specific request model -> future runtime` とする。Runtimeからdefinitionsやplanningのモデルへ逆依存させない。
- provider共通抽象は、複数providerの実装から実際の共通点が確認されるまで作らない。Codex CLIは承認・sandbox・tool実行・agent loopを伴う実行基盤であるため、将来は別のAdapterとRuntime経路として検討する。
- 人間承認が必要な遷移は、承認済みの明示的な入力なしに進めない。
- 成果物とイベントは実行 ID に紐付け、後から検証できるようにする。

## 初期ディレクトリ

```text
src/ai_office/
  cli.py
  definitions/
  planning/
  invocation/
  providers/
    openai/
  engine/
  runtime/
  storage/
  tools/
employees/
workflows/
schemas/
```

`employees/` と `workflows/` はテキスト定義の配置場所であり、定義の読込・検証とCLIによる確認を提供する。`planning/` は検証済み定義から実行計画と、1 step分の構造化実行要求を生成する。`invocation/` は実行要求からprovider非依存のモデル呼び出し要求を生成する。`tools/` は未解決名を静的な`ToolDefinition`へ解決する。`providers/openai/` はモデル呼び出し要求をOpenAI固有の実行前モデルへ、解決済みtool定義を静的function tool schemaモデルへ、payloadをJSON互換Python辞書と決定的なJSON文字列へ、未認証HTTP request templateと認証済みtemplateへ変換し、`OPENAI_API_KEY`を限定された環境取得境界でだけ取得して、認証済みrequestを1回だけHTTPS送信してraw responseを返す。Response Boundaryはcompleted responseを不変dataへ分類し、Output Text Boundaryはsuccess responseから対応するtextだけを抽出する。OpenAI Runtimeは今後のPhaseで扱う。

Persisted Execution Outcome Classification Reentry Boundary（Phase 37）は、検証済み`WorkflowDefinition`とcaller supplied state/event targetを入力にするread-only boundaryである。既存Phase 24 loaderを一度だけ呼び、Phase 36の終端`step_succeeded`または`step_failed`、workflow/current-step/employee/completed-step/event sequenceを厳格に照合する。成功は`persisted_success`、失敗は既存安全failure categoryを持つ`persisted_failure`として最小の不変resultに分類するだけである。Phase 25とPhase 31、progression判断、次step準備、retry、completion/finalization、provider実行、persistenceを呼ばない。loader注入がtargetを改変した場合は呼出し前bytesへ復元し、安全なerrorとして拒否する。

Phase 74 Approved Next-Step Preparation Phase Bridge Cycle Continuation Boundaryは、Phase 73の正確な`WorkflowProgressionDecision`または`PersistedExecutionOutcome`を受けるread-only boundaryである。`prepare_next_step`だけを明示approvalと一致するnext employee、および同じworkflow・state/event targetsとともにPhase 67へ正確に一度だけ委譲し、同じ`PreparedWorkflowStep`を返す。`workflow_complete`と`persisted_failure`はapproval/employeeなしでstrict terminal state/historyを検証し、Phase 67を呼ばず同じobjectで停止する。依存のtarget改変、不正返却、safe/unexpected errorは両targetをbyte-for-byte補償復元し、安全なdetail classificationに変換する。Phase 53/60/67/73 logic、approval作成、employee選択、persistence、provider/tool実行、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIを複製・追加しない。

Phase 75 Prepared Next-Step Start Routing Phase Bridge Cycle Continuation Boundaryは、Phase 74の正確な`PreparedWorkflowStep`、`workflow_complete`、または`persisted_failure`を受けるread-only boundaryである。prepared stepだけをmatchingする正確な`EmployeeDefinition`、同じworkflow、state/event targetsとともにPhase 68へ正確に一度だけ委譲し、正確な`PreparedStepExecutionStart`を返す。completion/failure stop routeはemployeeなしでstrict terminal state/historyを検証し、Phase 68を呼ばず同じobjectで停止する。依存のtarget改変、不正返却、safe/unexpected error、rollback failureは両targetをbyte-for-byte補償復元し、安全なdetail classificationに変換する。Phase 54/61/68/74 logic、employee選択、running-state persistence、provider/tool実行、outcome分類、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIを複製・追加しない。

Phase 76 Prepared Start Persistence Routing Phase Bridge Cycle Continuation Boundaryは、Phase 75の正確な`PreparedStepExecutionStart`、`workflow_complete`、または`persisted_failure`を受けるboundaryである。prepared startだけをmatchingする正確な`EmployeeDefinition`、同じworkflow、state/event targetsとともに既存Phase 69へ正確に一度だけ委譲し、正確な`RunningStatePersistenceResult`を返す。completion/failure stop routeはemployeeなしでstrict terminal state/historyを検証し、Phase 69を呼ばず同じobjectで停止する。依存のtarget改変、不正返却、safe/unexpected error、rollback failureは両targetをbyte-for-byte補償復元し、安全なdetail classificationに変換する。Phase 69の実装を複製した自動継続やprovider/tool実行、retry、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しない。

Phase 77 Persisted Running Execution Routing Phase Bridge Cycle Continuation Boundaryは、Phase 76の正確な`RunningStatePersistenceResult`、`workflow_complete`、または`persisted_failure`を受けるread-only boundaryである。execution routeでは元の正確な`PreparedStepExecutionStart`、workflow、employee、resolved tools、OpenAI API key、approval、transportを検証し、state/event targetsと先行step historyを再検証した後、同じ10引数のobject identityで既存Phase 70へ正確に一度だけ委譲し、正確なruntime success/failureを返す。completion/failure stop routeはexecution-only inputsをすべてNoneとしてstrict terminal state/historyを検証し、Phase 70を呼ばず同じobjectで停止する。依存のtarget改変、不正返却、safe/unexpected error、rollback failureは両targetをbyte-for-byte補償復元し、安全なdetail classificationに変換する。Phase 70/63/56 logic、employee/tool/credential/approval選択、provider/tool実行、transition persistence、outcome分類、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは複製・追加しない。

Persisted Execution Outcome Routing Reentry Boundary（Phase 38）は、caller suppliedな正確なPhase 37 outcomeを同じ明示targetに対して再分類し、全fieldを照合するread-only boundaryである。成功だけをPhase 31へ一度委譲して同じdecision objectを返し、失敗はPhase 31を呼ばず同じoutcome objectを返す。各依存呼出し後にtarget bytesの不変性を確認し、改変時のみ補償復元する。next-step preparation、completion persistence/finalization、retry/recovery、provider execution、data persistenceを行わない。

Persisted Success Preparation Routing Reentry Boundary（Phase 39）は、caller suppliedな正確なPhase 31 decisionを明示targetに対して再判定し、全fieldを照合するread-only boundaryである。`prepare_next_step`だけをcaller supplied approval/employeeとともにPhase 32へ一度委譲して同じprepared-step objectを返し、`workflow_complete`はPhase 32を呼ばず同じdecision objectを返す。approval作成、prepared-step execution、running-state persistence、completion persistence/finalization、retry、provider execution、data persistenceを行わない。

Progression Preparation Routing Bridge（Phase 46）は、正確なPhase 45 result、`NextStepPreparationApproval`、`EmployeeDefinition`を明示入力として受けるread-only boundaryである。`prepare_next_step`だけを同じworkflow、state/event target、approval、employee objectsとともに`route_persisted_success_progression_reentry()`（Phase 39）へ正確に一度委譲し、同じprepared-step result objectを返す。`workflow_complete`と`persisted_failure`はapproval/employeeを使用せずPhase 39を呼ばず同じ supplied objectを返す。依存によるtarget改変は元bytesへ補償復元する。Phase 31/32やPhase 39 logicを複製せず、approvalの作成・変更、next-step start/execution、running-state persistence、retry、自動継続、completion/failure finalization、paid CLI/GUIを行わない。

Prepared-Step Start Routing Bridge（Phase 47）は、正確な`PreparedWorkflowStep`をcaller suppliedの`WorkflowDefinition`、`EmployeeDefinition`、state/event target objectsとともに`route_prepared_step_start_reentry()`（Phase 40）へ正確に一度だけ委譲するread-only bridgeである。同じ`PreparedStepExecutionStart` objectを返す。`workflow_complete`と`persisted_failure`はstrict terminal state/historyを検証してPhase 40を呼ばず、同じ supplied objectのまま停止する。依存のtarget改変は元bytesへ補償復元する。Phase 34を直接呼ばず、running-state persistence、runtime event append、provider/tool execution、retry、自動継続、workflow completion/failure finalizationを行わない。

Prepared-Start Persistence Routing Bridge（Phase 48）は、正確なPhase 47 resultを受ける明示bridgeである。正確な`PreparedStepExecutionStart`だけをcaller suppliedの`WorkflowDefinition`、`EmployeeDefinition`、state/event target objectsとともに`route_prepared_start_persistence_reentry()`（Phase 41）へ正確に一度だけ委譲し、同じ`RunningStatePersistenceResult` objectを返す。許可する副作用は提案済みの正確な`running` stateをstate targetへ永続化することだけであり、event targetはbyte-for-byte不変でなければならない。`workflow_complete`と`persisted_failure`はstrict terminal state/historyを検証してPhase 41を呼ばず、同じ supplied objectで停止する。Phase 35やPhase 41 logicを複製せず、runtime event append、provider/tool execution、retry、自動継続、execution-result transition、workflow completion/failure finalization、paid CLI/GUIを行わない。flowは `Phase 47 PreparedStepExecutionStart → Phase 48 → Phase 41 exactly once → same RunningStatePersistenceResult`、または `workflow_complete | persisted_failure → unchanged stop` とし、後続は将来の明示boundaryに委ねる。

Persisted-Running Execution Routing Bridge（Phase 49）は、正確なPhase 48 resultを既存`route_persisted_running_execution_reentry()`（Phase 42）へ明示的にroutingするbridgeである。正確な`RunningStatePersistenceResult`だけをcaller suppliedのstart、workflow、employee、targets、tools、API key、approval、transportとともに一度委譲し、同じruntime execution result objectを返す。`workflow_complete`と`persisted_failure`は実行入力をすべて`None`としてstrict terminal state/historyを検証し、Phase 42を呼ばず同じobjectで停止する。Phase 36やPhase 42 logicを複製せず、transition persistence、runtime-event append、retry、自動継続、completion/failure finalization、scheduler、loop、parallel execution、paid CLI/GUIを行わない。

Prepared-Step Start Routing Reentry Boundary（Phase 40）は、caller suppliedな正確なPhase 39 resultを明示targetに対して検証するread-only boundaryである。正確な`PreparedWorkflowStep`だけを正確なmatching employeeとともにPhase 34へ一度委譲し、requestとproposed `running` stateの契約を照合した同じ`PreparedStepExecutionStart` objectを返す。正確な`workflow_complete` decisionはPhase 34を呼ばず同じdecision objectを返して停止し、completion persistence/finalizationを行わない。state/event target bytesは依存呼出し前後に検証し、改変時のみ補償復元する。running-state/event persistence、provider実行、tool/credential resolution、retry、自動継続、paid CLI/GUI、data writeを行わない。flowは `Phase 39 PreparedWorkflowStep → explicit employee → Phase 34 → PreparedStepExecutionStart`、または `Phase 39 workflow_complete → stop` とし、その後のrunning-state persistenceとcompletion persistence/finalizationは将来の明示boundaryに委ねる。

Prepared-Start Persistence Routing Reentry Boundary（Phase 41）は、正確なPhase 40 resultをPhase 35へ明示的にroutingするboundaryである。正確な`PreparedStepExecutionStart`だけを正確なmatching employeeとともに一度委譲し、same `RunningStatePersistenceResult`を返す。許可されるside effectは提案済み`running` stateのstate targetへの永続化だけであり、event targetはbyte-for-byte不変でなければならない。`workflow_complete`はPhase 35を呼ばず同じdecision objectを返して停止する。provider実行、credentials/tools/approval、runtime event append、transition、retry、自動継続、completion persistence/finalizationを行わない。

## Phase 73: classified outcome routing phase bridge continuation

Phase 73 accepts one exact Phase 72 `PersistedExecutionOutcome` or the existing
final `WorkflowProgressionDecision`. A persisted success is validated against
the succeeded terminal state/history and delegated exactly once to the existing
Phase 59 classified-outcome routing boundary, returning its exact progression
decision. A persisted failure and workflow completion are strict, unchanged,
zero-call stop routes. Dependency errors and malformed returns are detail-safe;
all dependency mutations are compensated byte-for-byte, without retry. This
boundary does not select employees, invoke providers or tools, persist
transitions, classify outcomes, prepare or execute steps, retry, auto-continue,
finalize, schedule, loop, parallelize, or add paid CLI/GUI behavior.
### Phase 95

The approved-next-step preparation dispatch boundary accepts the exact Phase 94 decision or persisted failure. A prepare decision, matching approval, and next employee are passed directly to Phase 88 once; workflow completion and persisted failure are strict terminal, zero-call stop routes. The boundary is read-only and sanitizes unexpected dependency failures while compensating both persistence targets without retry.

### Phase 98

The persisted-running execution dispatch continuation boundary accepts the exact Phase 97 persistence result and delegates the canonical ten inputs to Phase 91 exactly once. Terminal completion and persisted failure remain unchanged zero-call routes; the boundary is read-only and compensates mutations without retry.

### Phase 99

The executed-result transition persistence dispatch continuation boundary accepts an exact Phase 98 runtime result and delegates the canonical four inputs to Phase 92 exactly once. Workflow completion and persisted failure remain strict unchanged zero-call routes; malformed dependency returns, mutations, and errors are compensated without retry.
### Phase 100

The persisted-outcome classification dispatch continuation boundary accepts an exact Phase 99 persistence result and delegates the canonical four inputs to Phase 93 exactly once. Workflow completion and persisted failure remain strict unchanged zero-call stop routes. Terminal persistence, outcome consistency, target identity, dependency mutation, and rollback behavior are validated without retry.

### Phase 101

The classified-outcome cycle-closure continuation boundary accepts exactly one Phase 100 `PersistedExecutionOutcome` or `workflow_complete` decision. A persisted success validates succeeded terminal state/history and directly delegates once to Phase 94 in canonical `(result, workflow, state_path, events_path)` order, returning the exact matching `prepare_next_step` or `workflow_complete` decision. Persisted failure and workflow completion are strict zero-call unchanged stop routes. Dependency safe errors preserve identity when targets are restored, unexpected errors are sanitized, malformed returns and target mutations are compensated on both targets where possible, and retry is never performed.

```text
Phase 100
PersistedExecutionOutcome
| workflow_complete
    ↓
Phase 101 cycle-closure continuation boundary
persisted_success
    → Phase 94 exactly once
    → exact prepare_next_step | workflow_complete
persisted_failure | workflow_complete
    → unchanged zero-call stop
    ↓
Phase 95 (future explicit caller action)
```

Phase 101 closes only the outcome-classification-to-progression edge. It does not call Phase 95, load/select employees, resolve tools, create credentials or approvals, prepare or execute a step, persist state, invoke providers/tools, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

### Phase 102

The approved-next-step cycle continuation boundary accepts exactly one Phase 101 `prepare_next_step` or `workflow_complete` decision, or one exact persisted failure. A prepare decision requires the exact matching approval and next employee, validates succeeded terminal state/history, and delegates directly to Phase 95 exactly once in canonical `(result, workflow, approval, employee, state_path, events_path)` order, returning the exact matching `PreparedWorkflowStep`. Workflow completion and persisted failure require approval and employee to be absent and are strict zero-call unchanged stop routes. Dependency safe errors preserve identity after successful compensation, unexpected errors are sanitized, target mutation and malformed returns are rejected, both targets are restored where possible, and retry is never performed.

```text
Phase 101
prepare_next_step | workflow_complete | persisted_failure
    ↓
Phase 102 approved next-step cycle continuation boundary
prepare_next_step + approval + next employee
    → Phase 95 exactly once
    → exact PreparedWorkflowStep
workflow_complete | persisted_failure
    → approval/employee absent
    → unchanged zero-call stop
    ↓
Phase 96 (future explicit caller action)
```

Phase 102 advances only progression-to-preparation. It does not call Phase 96, create approvals, select or load employees, start steps, persist state, execute providers/tools, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

### Phase 103

The prepared-step start cycle continuation boundary accepts exactly one Phase 102 `PreparedWorkflowStep`, `workflow_complete` decision, or persisted failure. A prepared step requires the exact matching employee, validates the succeeded predecessor state/history, and delegates directly to the public Phase 96 boundary exactly once in canonical `(result, workflow, employee, state_path, events_path)` order, returning the exact matching `PreparedStepExecutionStart`. Workflow completion and persisted failure require employee to be absent and are strict zero-call unchanged stop routes. Dependency safe errors preserve identity after successful compensation, unexpected errors are sanitized, target mutation and malformed returns are rejected, both targets are restored where possible, and retry is never performed.

```text
Phase 102
PreparedWorkflowStep | workflow_complete | persisted_failure
    ↓
Phase 103 prepared-step start cycle continuation boundary
PreparedWorkflowStep + exact employee
    → Phase 96 exactly once
    → exact PreparedStepExecutionStart
workflow_complete | persisted_failure
    → employee absent
    → unchanged zero-call stop
    ↓
Phase 97 (future explicit caller action)
```

Phase 103 advances only preparation-to-start construction. It does not call Phase 89 directly, persist running state, execute providers/tools, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

### Phase 104

The prepared-start persistence cycle continuation boundary accepts exactly one Phase 103 `PreparedStepExecutionStart`, `workflow_complete` decision, or persisted failure. A prepared execution start requires the exact matching employee, validates the succeeded predecessor state/history, and delegates directly to the public Phase 97 boundary exactly once in canonical `(result, workflow, employee, state_path, events_path)` order. The dependency must return the identical exact valid `RunningStatePersistenceResult`, with the exact proposed running state bytes and byte count, unchanged event history, and supplied target identity. Workflow completion and persisted failure require employee to be absent and are strict zero-call unchanged stop routes. Safe Phase 97 errors preserve identity after successful compensation; unexpected errors are sanitized; missing, partial, unrelated, malformed, or otherwise invalid persistence transitions are rejected and both targets are restored where possible without retry.

```text
Phase 103
PreparedStepExecutionStart | workflow_complete | persisted_failure
    ↓
Phase 104 prepared-start persistence cycle continuation boundary
PreparedStepExecutionStart + employee
    → Phase 97 exactly once
    → exact RunningStatePersistenceResult
workflow_complete | persisted_failure
    → employee absent
    → unchanged zero-call stop
    ↓
Phase 98 (future explicit caller action)
```

Phase 104 advances only execution-start-to-running-persistence. Its only permitted side effect is the exact existing running-state persistence transition. It does not call Phase 90 directly or Phase 98, execute providers/tools, classify runtime results, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

### Phase 105

The persisted-running execution cycle continuation boundary accepts exactly one Phase 104 `RunningStatePersistenceResult`, `workflow_complete` decision, or persisted failure. A persisted-running result requires all exact execution inputs and delegates directly to the public Phase 98 boundary exactly once in canonical `(result, start, workflow, employee, state_path, events_path, resolved_tools, api_key, approval, transport)` order. It accepts and returns only the identical exact matching `StepRuntimeExecutionSuccess` or `StepRuntimeExecutionFailure`. Workflow completion and persisted failure require every execution-only input to be absent and are strict zero-call unchanged stop routes. Safe Phase 98 errors preserve identity after successful compensation; unexpected errors are sanitized; target mutation and malformed returns are rejected; both targets are restored where possible without retry.

```text
Phase 104
RunningStatePersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 105 persisted-running execution cycle continuation boundary
RunningStatePersistenceResult + execution inputs
    → Phase 98 exactly once
    → exact StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
workflow_complete | persisted_failure
    → all execution-only inputs absent
    → unchanged zero-call stop
    ↓
Phase 99 (future explicit caller action)
```

Phase 105 advances only persisted-running state to runtime execution. It does not call Phase 91 directly or Phase 99, persist terminal transitions, classify persisted outcomes, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

### Phase 106
The runtime-result transition persistence cycle continuation boundary accepts exactly one Phase 105 runtime success, runtime failure, workflow-complete decision, or persisted failure. A runtime result is validated against the exact persisted running predecessor state/history and delegated directly to the public Phase 99 boundary exactly once in canonical `(result, workflow, state_path, events_path)` order, returning the exact matching `WorkflowExecutionPersistenceResult`. Workflow completion and persisted failure are strict zero-call unchanged stop routes. Dependency safe errors preserve identity after successful compensation, unexpected errors are sanitized, invalid writes and malformed returns are rejected, both targets are restored where possible, and retry is never performed.

```text
Phase 105
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
| workflow_complete | persisted_failure
    ↓
Phase 106 runtime-result transition persistence cycle continuation boundary
runtime result
    → Phase 99 exactly once
    → exact WorkflowExecutionPersistenceResult
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 100 (future explicit caller action)
```

Phase 106 advances only runtime-result-to-terminal-transition persistence. Its only permitted side effect is the exact existing terminal transition persistence. It does not call Phase 92 directly or Phase 100, classify persisted outcomes, decide progression, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

### Phase 107
The persisted-transition outcome classification cycle continuation boundary accepts exactly one Phase 106 persistence result, workflow-complete decision, or persisted failure. A persistence result is validated against its exact terminal state/history and delegated directly to the public Phase 100 boundary exactly once in canonical `(result, workflow, state_path, events_path)` order, returning the exact matching `PersistedExecutionOutcome`. Workflow completion and persisted failure are strict zero-call unchanged stop routes. Safe Phase 100 errors preserve identity after successful compensation; unexpected errors are sanitized; target mutation and malformed returns are rejected; both targets are restored where possible without retry.
```text
Phase 106
WorkflowExecutionPersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 107 persisted-transition outcome classification cycle continuation boundary
persistence result
    → Phase 100 exactly once
    → exact persisted_success | persisted_failure
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 101 (future explicit caller action)
```
Phase 107 advances only persisted terminal transitions into persisted outcome classification. It does not call Phase 93 directly, decide progression, prepare the next step, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

### Phase 108
The classified persisted-outcome progression cycle continuation boundary accepts exactly one Phase 107 persisted success, persisted failure, or workflow-complete decision. A persisted success is validated against its exact succeeded terminal state/history and delegated directly to the public Phase 101 boundary exactly once in canonical `(result, workflow, state_path, events_path)` order, returning the exact matching `prepare_next_step` or `workflow_complete` progression decision. Persisted failure and workflow completion are strict zero-call unchanged stop routes. Dependency safe errors preserve identity after successful compensation, unexpected errors are sanitized, target mutation and malformed returns are rejected, both targets are restored where possible, and retry is never performed.

```text
Phase 107
PersistedExecutionOutcome
| workflow_complete
    ↓
Phase 108 classified persisted-outcome progression cycle continuation boundary
persisted_success
    → Phase 101 exactly once
    → exact prepare_next_step | workflow_complete
persisted_failure | workflow_complete
    → unchanged zero-call stop
    ↓
Phase 102 (future explicit caller action)
```

Phase 108 advances only classified persisted success into progression and closes one complete execution-cycle edge. It does not call Phase 94 directly, prepare the next step, persist running state, execute providers or tools, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

- Phase 109 `route_progression_to_approved_preparation_cycle_reentry_continuation_boundary()` consumes one exact Phase 108 result. Only an exact `prepare_next_step` decision with its exact approval and next employee delegates once to public Phase 102 in canonical six-argument order and returns its exact `PreparedWorkflowStep`; exact completion and persisted failure remain unchanged zero-call stop routes. The boundary revalidates all exact route/linkage fields and canonical terminal history, preserves state and event targets as a read-only two-target transaction with compensation, preserves only safe Phase 102 errors after successful rollback, and never bypasses Phase 102 or starts or executes the prepared step.

### Phase 110

The prepared-step start cycle reentry continuation boundary accepts exactly one Phase 109 `PreparedWorkflowStep`, `workflow_complete` decision, or persisted failure. A prepared step requires its exact matching employee and delegates directly to the public Phase 103 boundary exactly once in canonical `(result, workflow, employee, state_path, events_path)` order, returning the exact matching `PreparedStepExecutionStart`. Workflow completion and persisted failure require the employee to be absent and are strict zero-call unchanged stop routes. Phase 110 is read-only: safe Phase 103 errors preserve identity when targets remain unchanged or after successful compensation; unexpected errors are sanitized; target mutation and malformed returns are rejected; both targets are restored where possible without retry.

```text
Phase 109
PreparedWorkflowStep | workflow_complete | persisted_failure
    ↓
Phase 110 prepared-step start cycle reentry continuation boundary
PreparedWorkflowStep + employee
    → Phase 103 exactly once
    → exact PreparedStepExecutionStart
workflow_complete | persisted_failure
    → employee absent
    → unchanged zero-call stop
    ↓
Phase 104 (future explicit caller action)
```

Phase 110 advances only prepared-step-to-execution-start preparation. It does not call Phase 96 directly or Phase 104, persist running state, execute providers or tools, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

### Phase 111

The prepared-start persistence cycle reentry continuation boundary accepts exactly one Phase 110 `PreparedStepExecutionStart`, `workflow_complete` decision, or persisted failure. An execution start requires its exact matching employee and delegates directly to the public Phase 104 boundary exactly once in canonical `(result, workflow, employee, state_path, events_path)` order, returning the exact matching `RunningStatePersistenceResult`. This route permits only Phase 104's exact running-state persistence effect. Workflow completion and persisted failure require the employee to be absent and are strict zero-call unchanged stop routes. Dependency safe errors preserve identity after successful compensation; unexpected errors are sanitized; invalid persistence, unrelated or partial writes, target mutation, and malformed returns are rejected; both targets are restored where possible without retry.

```text
Phase 110
PreparedStepExecutionStart | workflow_complete | persisted_failure
    ↓
Phase 111 prepared-start persistence cycle reentry continuation boundary
PreparedStepExecutionStart + employee
    → Phase 104 exactly once
    → exact RunningStatePersistenceResult
    → only exact running-state persistence effect allowed
workflow_complete | persisted_failure
    → employee absent
    → unchanged zero-call stop
    ↓
Phase 105 (future explicit caller action)
```

Phase 111 advances only execution-start preparation into running-state persistence. It does not call Phase 97 directly or Phase 105, execute providers or tools, create runtime results, transition terminal state, classify outcomes, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

### Phase 112

The persisted-running execution cycle reentry continuation boundary accepts exactly one Phase 111 `RunningStatePersistenceResult`, `workflow_complete` decision, or persisted failure. A running persistence result requires the complete exact execution-only inputs and is revalidated against strict persisted-running state/history and the state/event targets. It is then delegated directly to the public Phase 105 boundary exactly once in canonical `(result, start, workflow, employee, state_path, events_path, resolved_tools, api_key, approval, transport)` order, returning the exact matching `StepRuntimeExecutionSuccess` or `StepRuntimeExecutionFailure` unchanged. Workflow completion and persisted failure require all execution-only inputs to be absent and are strict zero-call unchanged stop routes.

```text
Phase 111
RunningStatePersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 112 persisted-running execution cycle reentry continuation boundary
running persistence result
    → Phase 105 exactly once in canonical ten-argument order
    → exact StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 106 (future explicit caller action)
```

Phase 112 is read-only. Dependency-return mismatches, target mutation, safe or unexpected dependency errors, and rollback failures are classified detail-safely; both targets are compensated byte-for-byte where possible, and no dependency call is retried. It advances only persisted-running state into runtime execution through Phase 105. It does not call Phase 98 directly or Phase 106, duplicate provider/tool execution, resolve tools, create credentials or approvals, persist terminal transitions, classify outcomes, retry, automatically continue, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

### Phase 113

The runtime-result transition persistence cycle reentry continuation boundary accepts exactly one Phase 112 `StepRuntimeExecutionSuccess`, `StepRuntimeExecutionFailure`, `workflow_complete` decision, or persisted failure. A runtime execution result is validated against the exact persisted running predecessor state and history, then delegated directly to the public Phase 106 boundary exactly once in canonical `(result, workflow, state_path, events_path)` order, returning its exact matching `WorkflowExecutionPersistenceResult`. This route permits only Phase 106's exact terminal-transition persistence effect. Workflow completion and persisted failure are strict read-only, zero-call, unchanged stop routes. Dependency safe errors preserve identity after successful compensation; unexpected errors are sanitized; invalid persistence, unrelated or partial writes, target mutation, and malformed returns are rejected; both targets are restored where possible without retry.

```text
Phase 112
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
| workflow_complete | persisted_failure
    ↓
Phase 113 runtime-result transition persistence cycle reentry continuation boundary
runtime result
    → Phase 106 exactly once
    → exact WorkflowExecutionPersistenceResult
    → only exact terminal-transition persistence effect allowed
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 107 (future explicit caller action)
```

Phase 113 advances only one exact runtime execution result into one explicit terminal-transition persistence through Phase 106. It does not call Phase 99 directly or Phase 107, classify persisted outcomes, decide progression, retry, automatically continue, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

### Phase 114

The persisted-transition outcome classification cycle reentry continuation boundary accepts exactly one Phase 113 `WorkflowExecutionPersistenceResult`, `workflow_complete` decision, or persisted failure. A persisted terminal transition is validated against its exact terminal state and history, then delegated directly to the public Phase 107 boundary exactly once in canonical `(result, workflow, state_path, events_path)` order, returning its exact matching `PersistedExecutionOutcome`. Workflow completion and persisted failure are strict read-only, zero-call, unchanged stop routes. Dependency safe errors preserve identity after successful compensation; unexpected errors are sanitized; invalid outcomes, unrelated or partial writes, target mutation, and malformed returns are rejected; both targets are restored where possible without retry.

```text
Phase 113
WorkflowExecutionPersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 114 persisted-transition outcome classification cycle reentry continuation boundary
persisted transition
    → Phase 107 exactly once in canonical four-argument order
    → exact PersistedExecutionOutcome
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 108 (future explicit caller action)
```

## Phase 120: runtime-result transition persistence cycle handoff reentry continuation boundary
Phase 120 accepts exactly one Phase 119 `StepRuntimeExecutionSuccess`, `StepRuntimeExecutionFailure`, `WorkflowProgressionDecision(workflow_complete)`, or `PersistedExecutionOutcome(persisted_failure)`. Exact runtime success or failure is validated and delegated exactly once to the public Phase 113 boundary in canonical four-argument order `(result, workflow, state_path, events_path)`, returning the exact `WorkflowExecutionPersistenceResult` produced by that dependency. Exact workflow completion and persisted failure are strict read-only, unchanged zero-call stop routes that return the same supplied object.

Phase 120 performs only the runtime-result persistence handoff through Phase 113. It does not call Phase 106 directly or advance automatically to Phase 114. It does not classify persisted outcomes, perform workflow progression, retry, automatically continue, finalize, schedule, loop, run in parallel, or add CLI/GUI behavior. Focused tests inject a Phase 113 fake and make no real provider, network, paid API, or external tool calls.

```text
Phase 119
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
| workflow_complete | persisted_failure
    ↓
Phase 120 runtime-result transition persistence cycle handoff reentry continuation boundary
runtime success | runtime failure
    → Phase 113 exactly once in canonical four-argument order
    → exact WorkflowExecutionPersistenceResult
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 114 (future explicit caller action)
```

Phase 114 advances only one exact persisted terminal transition into one explicit persisted-outcome classification through Phase 107. It does not call Phase 100 directly or Phase 108, decide progression, prepare the next step, retry, automatically continue, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

## Phase 121: persisted-transition outcome classification cycle handoff reentry continuation boundary
Phase 121 accepts exactly one Phase 120 `WorkflowExecutionPersistenceResult`, `workflow_complete` decision, or persisted failure. The persisted transition is validated against exact workflow, linkage, terminal state/history, target identity, and byte-count contracts, then delegated directly to the public Phase 114 boundary exactly once in canonical `(result, workflow, state_path, events_path)` order, returning its exact matching `PersistedExecutionOutcome`. Workflow completion and persisted failure are strict read-only, zero-call, unchanged stop routes.

Phase 121 performs one explicitly authorized persisted-transition classification handoff through Phase 114. It does not call Phase 107 directly, progress the workflow, prepare the next step, retry, automatically continue, finalize, schedule, loop, run in parallel, or add CLI/GUI behavior. Focused tests use injected Phase 114 fakes only and make no real provider, network, paid API, or external tool calls.

```text
Phase 120
WorkflowExecutionPersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 121 persisted-transition outcome classification cycle handoff reentry continuation boundary
WorkflowExecutionPersistenceResult
    → Phase 114 exactly once in canonical four-argument order
    → exact PersistedExecutionOutcome
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 115 (future explicit caller action)
```

## Phase 115: Classified Persisted Outcome Progression Cycle Reentry Continuation Boundary

`route_classified_persisted_outcome_progression_cycle_reentry_continuation_boundary()` advances exactly one classified persisted success from Phase 114 into one explicit progression decision by delegating directly to Phase 108 in canonical `(result, workflow, state_path, events_path)` order. It validates exact persisted-success, persisted-failure, and workflow-complete objects, exact workflow/step linkage, exact built-in field values, existing regular state/event `Path` targets, and strict terminal state/history before returning.

The boundary is read-only. It captures both targets before dependency execution, requires byte-for-byte unchanged targets on success and stop routes, restores both targets after malformed dependency returns or dependency errors with mutation, preserves safe Phase 108 error identity after unchanged targets or successful compensation, sanitizes unexpected dependency errors, and reports rollback failure as `dependency_rollback`. It never calls Phase 101 directly and does not prepare next steps, start execution, retry, persist, auto-continue, finalize, schedule, loop, run providers/tools, or launch paid CLI/GUI flows.

## Phase 122: Classified Persisted Outcome Progression Cycle Handoff Reentry Continuation Boundary

`route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary()` accepts one exact Phase 121 `PersistedExecutionOutcome` or `WorkflowProgressionDecision(workflow_complete)`. An exact `persisted_success` whose continuation step index is at least 2 is validated against the supplied workflow, persisted terminal state/history, and regular state/event targets, then delegated exactly once to the public Phase 115 boundary in canonical `(result, workflow, state_path, events_path)` order. It returns the exact valid `prepare_next_step` or `workflow_complete` decision from that dependency. Exact persisted failure and workflow completion are strict unchanged zero-call stop routes.

Phase 122 performs one explicitly authorized classified-persisted-success progression handoff through Phase 115. It does not call Phase 108 directly, prepare the next step, retry, automatically continue, finalize, schedule, loop, run in parallel, execute providers/tools, or add CLI/GUI behavior. Dependency mutation, malformed returns, safe or unexpected errors, and rollback failures are classified detail-safely with byte-for-byte compensation and no retry. Focused tests inject Phase 115 fakes only and make no real provider, network, paid API, or external tool call.

## Phase 116: Progression To Approved Preparation Cycle Handoff Reentry Continuation Boundary

`route_progression_to_approved_preparation_cycle_handoff_reentry_continuation_boundary()` advances exactly one Phase 115 `prepare_next_step` progression decision into one explicit approved next-step preparation handoff by delegating directly to Phase 109 in canonical `(result, workflow, approval, employee, state_path, events_path)` order. It validates exact progression, workflow, approval, employee, target, terminal history, and prepared-step linkage before returning the exact `PreparedWorkflowStep` dependency object.

Exact workflow completion and persisted failure are read-only zero-call stop routes that return the same supplied object unchanged. The boundary captures both targets before dependency execution, requires byte-for-byte unchanged targets on success and stop routes, compensates dependency mutation without retry, preserves safe Phase 109 error identity when compensation succeeds, sanitizes unexpected dependency errors, and reports rollback failure as `dependency_rollback`. It never calls Phase 102 directly and does not start execution, persist running state, retry, automatically continue, finalize, schedule, loop, run providers/tools, or launch paid CLI/GUI flows.

## Phase 117: Prepared Step Start Cycle Handoff Reentry Continuation Boundary

`route_prepared_step_start_cycle_handoff_reentry_continuation_boundary()` advances exactly one Phase 116 `PreparedWorkflowStep` into one explicit prepared-step start handoff by delegating directly to Phase 110 in canonical `(result, workflow, employee, state_path, events_path)` order. It validates exact prepared-step, workflow, employee, target, terminal history, and execution-start linkage before returning the exact `PreparedStepExecutionStart` dependency object.

Exact workflow completion and persisted failure are read-only zero-call stop routes that require `employee is None` and return the same supplied object unchanged. The boundary captures both targets before dependency execution, requires byte-for-byte unchanged targets on success and stop routes, compensates dependency mutation without retry, preserves safe Phase 110 error identity when compensation succeeds, sanitizes unexpected dependency errors, and reports rollback failure as `dependency_rollback`. It never calls Phase 103 directly and does not persist running state, execute a provider/tool, retry, automatically continue, finalize, schedule, loop, run in parallel, or launch paid CLI/GUI flows.

## Phase 118: prepared start persistence cycle handoff reentry continuation boundary

Phase 118 is the explicit handoff from the prepared-step start cycle into the prepared-start persistence cycle. It accepts only exact Phase 117 outcomes. For an exact `PreparedStepExecutionStart`, it validates the continuation-step linkage, predecessor terminal history, state/event targets, and dependency persistence result before returning the exact `RunningStatePersistenceResult` produced by Phase 111. For exact terminal completion/failure outcomes, it validates matching terminal persistence and returns the original object without invoking Phase 111. It does not call Phase 104 directly, execute providers/tools, retry, or continue beyond the single handoff.

## Phase 119: Persisted Running Execution Cycle Handoff Reentry Continuation Boundary
`route_persisted_running_execution_cycle_handoff_reentry_continuation_boundary()` accepts exactly one Phase 118 `RunningStatePersistenceResult`, `workflow_complete` decision, or persisted failure. For the execution route it validates the matching Phase 117 `PreparedStepExecutionStart`, explicit employee/tools/credential/approval/transport inputs, and persisted running state/history, then delegates exactly once to the public Phase 112 boundary in canonical ten-argument order and returns the exact matching runtime success or failure object. Completion and persisted failure are strict read-only, unchanged, zero-call stop routes.

Phase 119 is one explicitly authorized runtime-execution handoff through Phase 112. With real execution inputs the injected dependency may perform one provider/tool attempt, but Phase 119 itself does not call Phase 105 directly, persist or classify the runtime result, retry, automatically continue, progress, finalize, schedule, loop, run in parallel, or add CLI/GUI behavior. Focused tests use injected fakes only and make no real provider, network, paid API, or external tool calls.
```text
Phase 118
RunningStatePersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 119 persisted-running execution cycle handoff reentry continuation boundary
RunningStatePersistenceResult + matching PreparedStepExecutionStart
+ exact employee/tools/credential/approval/transport
    → Phase 112 exactly once in canonical ten-argument order
    → exact StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 113 (future explicit caller action)
```

## Phase 123: Progression To Approved Preparation Cycle Handoff Chain Reentry Continuation Boundary

`route_progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary()` accepts one exact Phase 122 result. An exact `WorkflowProgressionDecision(prepare_next_step)` with matching workflow, approval, employee, terminal history, and regular state/event targets is delegated exactly once to the public Phase 116 boundary in canonical `(result, workflow, approval, employee, state_path, events_path)` order and returns the exact `PreparedWorkflowStep` produced by that dependency. Exact `workflow_complete` and `PersistedExecutionOutcome(persisted_failure)` results are strict unchanged zero-call stops.

Phase 123 performs one explicitly authorized progression-to-approved-preparation handoff through Phase 116. It does not call Phase 109 directly, start the prepared step, persist start state, execute a provider or tool, retry, automatically continue, finalize, schedule, loop, run in parallel, or add CLI/GUI behavior. Focused tests inject Phase 116 fakes only and make no real provider, network, paid API, or external tool calls.

## Phase 124: Prepared Step Start Cycle Handoff Chain Reentry Continuation Boundary

`route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary()` accepts one exact Phase 123 result. An exact `PreparedWorkflowStep` from the continuation path, with `step_index >= 3` and matching workflow, employee, predecessor terminal history, and regular state/event targets, is delegated exactly once to the public Phase 117 boundary in canonical `(result, workflow, employee, state_path, events_path)` order and returns its exact `PreparedStepExecutionStart`. Exact `WorkflowProgressionDecision(workflow_complete)` and `PersistedExecutionOutcome(persisted_failure)` results are strict unchanged zero-call stops.

Phase 124 performs one explicitly authorized prepared-step start handoff through Phase 117. It does not call Phase 110 directly, persist prepared-start state, execute a provider or tool, retry, automatically continue, finalize, schedule, loop, run in parallel, or add CLI/GUI behavior. Dependency errors, malformed returns, and target mutation are classified safely with byte-for-byte compensation and no retry. Focused tests inject Phase 117 fakes only and make no real provider, network, paid API, or external tool calls.

```text
Phase 123
PreparedWorkflowStep | workflow_complete | persisted_failure
    ↓
Phase 124 prepared-step start cycle handoff chain reentry continuation boundary
PreparedWorkflowStep + exact employee
    → Phase 117 exactly once in canonical five-argument order
    → exact PreparedStepExecutionStart
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 118 (future explicit caller action)
```

```text
Phase 122
prepare_next_step | workflow_complete | persisted_failure
    ↓
Phase 123 progression-to-approved-preparation cycle handoff chain reentry continuation boundary
prepare_next_step + exact approval + exact employee
    → Phase 116 exactly once in canonical six-argument order
    → exact PreparedWorkflowStep
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 117 (future explicit caller action)
```

## Phase 125: Prepared Start Persistence Cycle Handoff Chain Reentry Continuation Boundary

Phase 125は1回の明示的なprepared-start persistence handoffであり、Phase 124の正確な`PreparedStepExecutionStart`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるread-only boundaryである。継続経路のprepared start（`current_step_index >= 3`）では、正確なworkflow、employee、predecessor terminal history、regular state/event targetsを検証した後、公開Phase 118 boundaryへ`(result, workflow, employee, state_path, events_path)`のcanonical five-argument orderで正確に一度だけ委譲する。Phase 118の正確な`RunningStatePersistenceResult`、proposed running-state bytes、positive byte count、persisted running state、およびevent targetのbyte-for-byte不変性を検証して、依存が返した同じresult objectを返す。

`workflow_complete`と`persisted_failure`はemployeeが`None`であること、terminal state/history、targetsの不変性を検証し、Phase 118を呼ばずに同じobjectを返すzero-call stop routeである。Phase 125はPhase 111を直接呼ばず、provider/tool実行、runtime resultの分類、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しない。safe dependency errorはidentityを保持し、unexpected error、不正な返却、target mutationはdetail-safeに分類し、可能な場合はstateとeventを元のbytesへ補償復元する。復元失敗は`dependency_rollback`とし、retryは行わない。focused testsはinjected Phase 118 fakesだけを使用し、real provider、network、paid API、external toolを呼ばない。

```text
Phase 124
PreparedStepExecutionStart | workflow_complete | persisted_failure
    ↓
Phase 125 prepared-start persistence cycle handoff chain reentry continuation boundary
PreparedStepExecutionStart + exact employee
    → Phase 118 exactly once in canonical five-argument order
    → exact RunningStatePersistenceResult
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 119 (future explicit caller action)
```

## Phase 126: Persisted Running Execution Cycle Handoff Chain Reentry Continuation Boundary

`route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary()` accepts exactly one Phase 125 `RunningStatePersistenceResult`, `WorkflowProgressionDecision(workflow_complete)`, or `PersistedExecutionOutcome(persisted_failure)`. On the continuation route, the exact Phase 125 result is revalidated with its exact `PreparedStepExecutionStart`, workflow, employee, regular state/event targets, resolved tools, credential, approval, and transport. Continuations require `current_step_index >= 3` and the complete succeeded predecessor history. The boundary delegates directly to the public Phase 119 boundary exactly once in canonical `(result, start, workflow, employee, state_path, events_path, resolved_tools, api_key, approval, transport)` order and returns the identical exact `StepRuntimeExecutionSuccess` or `StepRuntimeExecutionFailure` object.

Exact `workflow_complete` and `persisted_failure` values require all execution-only inputs to be `None`, validate strict terminal state/history and unchanged targets, call Phase 119 zero times, and return the supplied object unchanged. Phase 126 is one explicitly authorized persisted-running execution handoff through Phase 119. It does not call Phase 112 directly, persist runtime results, classify persisted outcomes, progress workflow state, retry, automatically continue, finalize, schedule, loop, run in parallel, or add CLI/GUI behavior. Safe dependency errors preserve identity after successful byte-for-byte compensation; unexpected errors, malformed returns, and target mutation are classified detail-safely without retry. Focused tests inject Phase 119 fakes only and make no real provider, network, paid API, external tool, or real transport calls.

```text
Phase 125
RunningStatePersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 126 persisted-running execution cycle handoff chain reentry continuation boundary
RunningStatePersistenceResult + exact PreparedStepExecutionStart + exact execution inputs
    → Phase 119 exactly once in canonical ten-argument order
    → exact StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 120 (future explicit caller action)
```

## Phase 127: Runtime Result Transition Persistence Cycle Handoff Chain Reentry Continuation Boundary

`route_runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary()`は、Phase 126の正確なruntime resultを受け、`StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`を既存の公開Phase 120 boundaryへ`(result, workflow, state_path, events_path)`のcanonical four-argument orderとobject identityを保って正確に一度だけ委譲する。Phase 120が返した正確な`WorkflowExecutionPersistenceResult`について、target identity、positive built-in byte counts、元event bytesからの一つのterminal event、terminal state/event、runtime linkageを検証してから返す。

`workflow_complete`と`persisted_failure`はstrict terminal state/historyとtargetのbyte-for-byte不変性を検証し、Phase 120 call count zeroで同一objectを返す。Phase 127はPhase 120を通る一つの明示的なruntime-result transition persistence handoffに限定し、Phase 113の直接呼び出し、persisted outcomeの分類、workflow progression、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを行わない。Focused testsはinjected Phase 120 fakeだけを使い、real provider、network、有料API、external tool、real transportを呼ばない。

```text
Phase 126
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure | workflow_complete | persisted_failure
    ↓
Phase 127 runtime-result transition persistence cycle handoff chain reentry continuation boundary
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
    → Phase 120 exactly once in canonical four-argument order
    → exact WorkflowExecutionPersistenceResult
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 121 (future explicit caller action)
```

## Phase 128: Persisted Transition Outcome Classification Cycle Handoff Chain Reentry Continuation Boundary

`route_persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary()` accepts one exact Phase 127 `WorkflowExecutionPersistenceResult`, `WorkflowProgressionDecision(workflow_complete)`, or `PersistedExecutionOutcome(persisted_failure)`. The persisted-transition route revalidates exact workflow and step models, regular targets, supplied target identity, positive exact byte counts, terminal state/history, current step index `>= 3`, succeeded predecessor history, and exact terminal event linkage. It then delegates directly to the public Phase 121 boundary exactly once in canonical `(result, workflow, state_path, events_path)` order with supplied-object identity and returns the exact valid `PersistedExecutionOutcome`. Phase 121 is classification-only, so valid execution leaves both targets byte-for-byte unchanged.

Exact workflow completion and persisted failure are strict unchanged zero-call stop routes. Phase 128 is one explicitly authorized persisted-transition outcome-classification handoff through Phase 121. It does not call Phase 114 directly, progress the workflow, prepare the next step, retry, automatically continue, finalize, schedule, loop, run in parallel, or add CLI/GUI behavior. Safe dependency errors preserve identity after successful compensation; unexpected errors, malformed outcomes, and target mutation are classified detail-safely without retry, and restoration failure is `dependency_rollback`. Focused tests inject Phase 121 fakes only and make no real provider, network, paid API, external tool, or real transport calls.

```text
Phase 127
WorkflowExecutionPersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 128 persisted-transition outcome classification cycle handoff chain reentry continuation boundary
WorkflowExecutionPersistenceResult
    → Phase 121 exactly once in canonical four-argument order
    → exact PersistedExecutionOutcome
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 122 (future explicit caller action)
```

## Phase 129: Classified Persisted-Outcome Progression Cycle Handoff Chain Reentry Continuation Boundary

`route_classified_persisted_outcome_progression_cycle_handoff_chain_reentry_continuation_boundary()` accepts one exact Phase 128 `PersistedExecutionOutcome(persisted_success)`, or one exact `PersistedExecutionOutcome(persisted_failure)` / `WorkflowProgressionDecision(workflow_complete)` stop value. The persisted-success route revalidates the exact workflow and step models, regular state/event targets, continuation index `>= 3`, succeeded terminal state/history, every succeeded predecessor, terminal event, and workflow/current-step/employee linkage before delegating directly to the public Phase 122 boundary exactly once in canonical `(result, workflow, state_path, events_path)` order with supplied-object identity.

For an intermediate step, Phase 129 accepts only the exact `prepare_next_step` decision with the exact next-step id/index/employee and `reason == "next_step_available"`. For the final step, it accepts only the exact `workflow_complete` decision with all next-step fields `None` and `reason == "last_step_succeeded"`. Persisted failure and workflow completion are strict unchanged zero-call stop routes. Phase 129 is one explicitly authorized classified-persisted-success progression handoff through Phase 122; it does not call Phase 115 directly, prepare or start a step, persist start state, execute a provider/tool, retry, automatically continue, finalize, schedule, loop, run in parallel, or add CLI/GUI behavior. Safe Phase 122 errors preserve identity after successful compensation; unexpected errors, malformed decisions, and target mutation are classified detail-safely without retry, and restoration failure is `dependency_rollback`. Focused tests inject Phase 122 fakes only and make no real provider, network, paid API, external tool, or real transport calls.

```text
Phase 128
persisted_success | persisted_failure | workflow_complete
    ↓
Phase 129 classified persisted-outcome progression cycle handoff chain reentry continuation boundary
persisted_success
    → Phase 122 exactly once in canonical four-argument order
    → prepare_next_step | workflow_complete
persisted_failure | workflow_complete
    → unchanged zero-call stop
    ↓
Phase 123 (future explicit caller action)
```

## Phase 130: Progression-to-Approved Preparation Cycle Handoff Chain Bridge

Phase 130は、Phase 129から受け取った正確な`WorkflowProgressionDecision(prepare_next_step)`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を検証するread-only bridgeである。prepare routeでは、exact workflow/step/approval/employee、regular state/event targets、`current_step_index >= 3`、current/next linkage、approval linkage、strict succeeded terminal state/history、completed-step prefix、terminal eventのruntime linkageを検証し、terminal providerはexact built-in `str == "openai"`を要求する。検証後、既存の公開Phase 123 `route_progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary()`へ、supplied object identityを保持した`(result, workflow, approval, employee, state_path, events_path)`のcanonical six-argument orderで正確に一度だけ委譲し、exact valid `PreparedWorkflowStep`を返す。

`workflow_complete`と`persisted_failure`はapproval/employeeが`None`であり、Phase 129 stop routeより不必要に厳格なprovider条件を追加しないことを確認したうえで、Phase 123を呼ばず同一objectを返すzero-call stop routeである。Phase 123はread-only dependencyであり、正常経路のstate/events mutationは契約違反として補償後に拒否する。safe dependency errorはsuccessful compensation後もidentityを維持し、unexpected errorはdetail-safeな`dependency_error`にsanitizeする。malformed returnやtarget mutationはrestoreし、restore failureは`dependency_rollback`とする。Phase 130はPhase 116を直接参照・呼び出しせず、prepared-step start、start-state persistence、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しない。Focused testsはinjected Phase 123 fakesだけを使用し、real provider、network、paid API、external tool、real transportを呼ばない。

```text
Phase 129
prepare_next_step | workflow_complete | persisted_failure
    ↓
Phase 130 progression-to-approved-preparation cycle handoff chain bridge reentry continuation boundary
prepare_next_step + exact approval + exact employee
    → Phase 123 exactly once in canonical six-argument order
    → exact PreparedWorkflowStep
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 124 (future explicit caller action)
```

## Phase 131: Prepared Step Start Cycle Handoff Chain Bridge Reentry Continuation Boundary

Phase 131は、Phase 130 continuationからの正確な`PreparedWorkflowStep`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるread-only bridgeである。prepared-step routeでは`step_index >= 4`を要求し、exact workflow/step/employee、regular state/event targets、succeeded predecessor terminal state/history、completed-step prefix、terminal eventのruntime linkageとprovider `"openai"`を再検証する。検証後、公開Phase 124 `route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary()`へ、supplied object identityを保持した`(result, workflow, employee, state_path, events_path)`のcanonical five-argument orderで正確に一度だけ委譲し、exact valid `PreparedStepExecutionStart`を返す。

Phase 124の返却はexact `PreparedStepExecutionStart`でなければならず、nested `ModelInvocationRequest`とrunning `WorkflowExecutionState`、request model/system/task instructions/allowed-tools、workflow/step/index/employee linkage、predecessor由来のcompleted-step prefix、`last_failure_category`を再検証する。`workflow_complete`と`persisted_failure`はemployeeが`None`であり、Phase 130 stop routeより厳しいprovider条件を追加せず、Phase 124を呼ばず同一objectを返すunchanged zero-call stop routeである。Phase 131はPhase 117を迂回せず、prepared-start persistence、provider/tool実行、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しない。safe dependency errorはsuccessful compensation後もidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類する。両targetをbyte-for-byteに補償復元し、復元失敗は`dependency_rollback`とする。Focused testsはinjected Phase 124 fakesのみを使い、real provider、network、paid API、external tool、real transportを呼ばない。

```text
Phase 130
PreparedWorkflowStep | workflow_complete | persisted_failure
    ↓
Phase 131 prepared-step start cycle handoff chain bridge reentry continuation boundary
PreparedWorkflowStep (step_index >= 4) + exact employee
    → Phase 124 exactly once in canonical five-argument order
    → exact PreparedStepExecutionStart
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 125 (future explicit caller action)
```

## Phase 132: Prepared Start Persistence Cycle Handoff Chain Bridge Reentry Continuation Boundary

Phase 132は、Phase 131 continuationからの正確な`PreparedStepExecutionStart`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるbridgeである。prepared-start routeでは`current_step_index >= 4`、exact workflow/step/employee、nested request/running-state、regular state/event targets、succeeded predecessor terminal state/history、completed-step prefix、terminal eventのruntime linkageとprovider=`"openai"`を再検証する。検証後、公開Phase 125 `route_prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary()`へ、supplied object identityを保持した`(result, workflow, employee, state_path, events_path)`のcanonical five-argument orderで正確に一度だけ委譲する。

Phase 125はこのPhase 132で明示的に許可されたstate persistence boundaryである。Phase 132は、exact `RunningStatePersistenceResult`、positive exact built-in `state_bytes_written`、canonical serialized `PreparedStepExecutionStart.running_state`、reloaded exact running state、event targetのbyte-for-byte不変性を再検証し、依存の同じresult objectを返す。正常経路ではstate targetだけがexact running stateへ置換される。`workflow_complete`と`persisted_failure`はPhase 125を呼ばず、strict terminal state/historyとunchanged targetsを確認して同じobjectを返すzero-call stop routeである。

Phase 132はPhase 118を直接参照・呼び出しせず、1回の明示的なprepared-start persistence handoffだけを行う。provider/tool execution、runtime-result persistence、outcome classification、workflow progression、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorは追加しない。safe dependency errorはsuccessful compensation後もidentityを保持し、unexpected error、malformed persistence result、target mutationはdetail-safeに分類する。両targetは元bytesへ補償復元し、復元失敗は`dependency_rollback`、dependency callはretryしない。Focused testsはinjected Phase 125 fakesのみを使用し、real provider、network、paid API、external tool、real transportを呼ばない。

```text
Phase 131
PreparedStepExecutionStart | workflow_complete | persisted_failure
    ↓
Phase 132 prepared-start persistence cycle handoff chain bridge reentry continuation boundary
PreparedStepExecutionStart (current_step_index >= 4) + exact employee
    → Phase 125 exactly once in canonical five-argument order
    → exact RunningStatePersistenceResult; state replaced, events unchanged
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 126 (future explicit caller action)
```

## Persisted-Running Execution Cycle Handoff Chain Bridge Reentry Continuation Boundary（Phase 133）

Phase 133は、Phase 132 continuationからの正確な`RunningStatePersistenceResult`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるouter bridgeである。実行routeでは`current_step_index >= 4`、exact workflow/start/request/running-state/employee/tools/credential/approval、regular targets、Phase 132のpersisted running state bytes、succeeded predecessor history、直前のsucceeded terminal eventのprovider=`"openai"`を再検証する。直前より前のpredecessor providerはPhase 132の契約を超えて制限しない。

検証後、公開Phase 126 `route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary()`へ、`(result, start, workflow, employee, state_path, events_path, resolved_tools, api_key, approval, transport)`のcanonical ten-argument orderで正確に一度だけ委譲する。supplied object identityを保持し、exact `StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`、nested invocation result、provider=`"openai"`、workflow/step/index/employee linkageを再検証する。Phase 126はread-only dependencyとして扱い、正常returnでstate/eventsを変更させない。

`workflow_complete`と`persisted_failure`はexecution-only inputを`None`に限定し、Phase 132 stop routeより不必要に厳しいprovider条件を追加しないunchanged zero-call stop routeである。Phase 133は明示的なpersisted-running execution handoffを一回だけ行い、runtime-result persistence、classification、progression、retry、automatic continuation、finalization、scheduling、loop、parallel execution、CLI/GUI behaviorを行わない。safe error identityはsuccessful compensation後も保持し、unexpected error、malformed return、target mutationはdetail-safeに分類する。両targetを元bytesへ復元し、restore failureは`dependency_rollback`、retryは行わない。Focused testsはinjected Phase 126 fakesだけを使い、real provider、network、paid API、external tool、credential use、real transportを呼ばない。

```text
Phase 132
RunningStatePersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 133 persisted-running execution cycle handoff chain bridge reentry continuation boundary
RunningStatePersistenceResult + exact execution inputs
    → Phase 126 exactly once in canonical ten-argument order
    → exact StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 127 (future explicit caller action)
```

## Phase 134: Runtime Result Transition Persistence Cycle Handoff Chain Bridge Reentry Continuation Boundary

Phase 134は、Phase 133から受け取った正確な`StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`を、既存の公開Phase 127へ接続するruntime-result persistence bridgeである。実行routeでは、exact workflow/step/state/event models、regular targets、persisted running state、`current_step_index >= 4`、workflow/current-step/index/employee linkage、Phase 133が保証したpredecessor historyとrequest IDs、直前predecessor provider=`"openai"`、およびnested invocation resultのexact OpenAI契約を再検証する。直前より前のvalid predecessor providerは不必要に厳格化しない。

Phase 127への委譲は`(result, workflow, state_path, events_path)`のcanonical four-argument orderとobject identityを保って正確に一度だけ行う。返却されるexact `WorkflowExecutionPersistenceResult`について、target identity、positive exact built-in byte counts、canonical terminal state、元event bytesの完全prefix、exactly one terminal event、success/failure linkage、terminal provider=`"openai"`を再検証し、validなら同一return objectを返す。正常routeはruntime resultをexact terminal stateへ遷移させ、eventsは元bytesをprefixとして一件だけappendする。

`workflow_complete`と`persisted_failure`はPhase 127を呼ばず、Phase 133 stop-routeのprovider許容範囲を維持したstrict terminal state/historyとtarget不変性のzero-call stopで同じobjectを返す。Phase 134はPhase 120を直接参照・呼び出しせず、classification、progression、next-step preparation、start、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しない。safe error identityを補償成功後も保持し、unexpected error、malformed return、invalid persistence、mutationをdetail-safeに分類する。両targetを補償復元し、復元失敗は`dependency_rollback`、retryは行わない。Focused testsはinjected Phase 127 fakesのみを使い、real provider、network、paid API、external tool、credential、real transportを呼ばない。

```text
Phase 133
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure | workflow_complete | persisted_failure
    ↓
Phase 134 runtime-result transition persistence cycle handoff chain bridge reentry continuation boundary
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure (current_step_index >= 4)
    → Phase 127 exactly once in canonical four-argument order
    → exact WorkflowExecutionPersistenceResult
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 128 (future explicit caller action)
```

## Phase 135: Persisted-Transition Outcome Classification Cycle Handoff Chain Bridge

Phase 135は、Phase 134の正確な`WorkflowExecutionPersistenceResult`を受け、公開Phase 128 `route_persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary()`へ、`(result, workflow, state_path, events_path)`のcanonical four-argument orderとsupplied-object identityを保って正確に一度だけ委譲する。Phase 128から返るexact `PersistedExecutionOutcome`のterminal state/linkage/index/failure categoryを再検証して同一objectを返す。`workflow_complete`と`persisted_failure`は、Phase 134 stop-routeのprovider/index許容範囲を維持したstrict terminal state/historyとtarget不変性を確認し、Phase 128 call count zeroで同じobjectを返す。

classification routeではcurrent step index `>= 4`、exact workflow/step/state/event models、regular target identity、positive exact built-in byte counts、canonical persisted state bytes、complete predecessor history、直前predecessor provider=`"openai"`、terminal provider=`"openai"`を再検証する。Phase 128はread-only dependencyとして扱い、正常経路のstate/events mutationは許可しない。safe dependency errorは補償成功後もidentityを保持し、unexpected error、malformed outcome、target mutationはdetail-safeに分類して両targetを元bytesへ復元する。restore failureは`dependency_rollback`、retryはない。

Phase 135はPhase 121を迂回せず、workflow progression、next-step preparation、prepared-step start、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しない。Focused testsはinjected Phase 128 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを実行しない。

Phase 128については、Phase 127/134 success persistenceが生成しうるexact built-in `str output_text == ""`を受理する狭い互換修正を行った。response_id、provider、predecessor、failure semantics、public APIその他の契約は変更していない。

```text
Phase 134
WorkflowExecutionPersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 135 persisted-transition outcome classification cycle handoff chain bridge reentry continuation boundary
WorkflowExecutionPersistenceResult
    → Phase 128 exactly once in canonical four-argument order
    → exact persisted_success | persisted_failure
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 136 (future explicit caller action)
```

## Phase 136: Classified Persisted-Outcome Progression Cycle Handoff Chain Bridge Reentry Continuation Boundary

Phase 136 is the outer bridge from Phase 135's exact `PersistedExecutionOutcome(persisted_success)` to the public Phase 129 classified progression boundary. It revalidates the Phase 135 provenance: exact workflow, step/state/history models, regular target identity, continuation index `>= 4`, workflow/current-step/index/employee linkage, the immediate predecessor's exact `openai` provider, strict predecessor provider/request/response/output fields, terminal provider `openai`, terminal response/request fields, and success/failure terminal semantics. Earlier valid predecessor providers remain nonempty exact strings and are not unnecessarily restricted.

The bridge delegates exactly once to public Phase 129 in canonical `(result, workflow, state_path, events_path)` order with supplied-object identity. It accepts only the exact `WorkflowProgressionDecision` returned by Phase 129, then revalidates intermediate `prepare_next_step` current/next linkage and `next_step_available` reason or final `workflow_complete` linkage and `last_step_succeeded` reason. Valid classification is read-only: state and events remain byte-for-byte unchanged. The persisted-success route permits only the narrow Phase 129 compatibility case of an exact built-in empty success `output_text`; all other terminal, predecessor, history, and linkage contracts remain strict.

`persisted_failure` and `workflow_complete` inherit the Phase 135 stop contract, return the supplied object unchanged, and make zero Phase 129 calls. In particular, a workflow-complete success terminal with empty output remains rejected. Phase 136 does not call Phase 122 directly and adds no progression, preparation, start, persistence, execution, retry, automatic continuation, finalization, scheduling, looping, parallel execution, or CLI/GUI behavior. Safe dependency errors preserve identity after successful compensation; unexpected errors, malformed decisions, and target mutation are detail-safe, both targets are restored when possible, restore failure is `dependency_rollback`, and no retry occurs. Focused tests inject Phase 129 fakes only and make no real provider, network, paid API, external tool, credential, or transport calls.

```text
Phase 135
persisted_success | persisted_failure | workflow_complete
    ↓
Phase 136 classified persisted-outcome progression cycle handoff chain bridge reentry continuation boundary
persisted_success (current_step_index >= 4)
    → Phase 129 exactly once in canonical four-argument order
    → exact prepare_next_step | workflow_complete decision
persisted_failure | workflow_complete
    → unchanged zero-call stop
    ↓
Phase 137 (future explicit caller action)
```

## Phase 137: Progression-to-Approved Preparation Cycle Handoff Chain Bridge Outer Reentry Continuation Boundary

Phase 137は、Phase 136の正確な`WorkflowProgressionDecision(prepare_next_step)`を受けるouter bridgeである。prepare routeでは、Phase 136 provenanceを維持したexact workflow/step/approval/employee、regular state/event targets、`current_step_index >= 4`、current/next/reason linkage、completed-step prefix、直前predecessor provider=`"openai"`、terminal provider=`"openai"`、terminal response/request/outputのexact contractを再検証する。Phase 136が許可するterminal successのexact built-in empty `output_text`も維持する。

検証後、公開Phase 130 `route_progression_to_approved_preparation_cycle_handoff_chain_bridge_reentry_continuation_boundary()`へ、`(result, workflow, approval, employee, state_path, events_path)`のcanonical six-argument orderとsupplied-object identityを保持して正確に一度だけ委譲する。Phase 130のexact `PreparedWorkflowStep`についてworkflow/step/index/employee、instructions、model、allowed-tool tupleを再検証し、同じobjectを返す。Phase 130がread-onlyであることを確認し、正常経路でstate/eventsをbyte-for-byte不変に保つ。safe error identity、両target補償、unexpected errorのdetail-safe sanitize、`dependency_rollback`、no retryを維持する。

`workflow_complete`と`persisted_failure`はapproval/employeeが`None`であり、Phase 136 stop contractより不必要に厳しいprovider/index条件を追加せず、Phase 130を呼ばず同じobjectを返すunchanged zero-call stop routeである。workflow-complete success terminal outputはstop contractどおりnon-emptyとする。Phase 137はPhase 123およびPhase 131を直接呼び出さず、prepared-step start、start-state persistence、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しない。Focused testsはinjected Phase 130 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを呼ばない。

Phase 130には、prepare-next-step routeだけで、Phase 136の有効なexact built-in empty success `output_text`を受理する狭い互換修正を追加した。workflow current-step linkage、completed prefix、predecessor、terminal provider/response/request、failure semantics、stop behavior、public API、共有terminal history contractは変更していない。

```text
Phase 136
prepare_next_step | workflow_complete | persisted_failure
    ↓
Phase 137 progression-to-approved-preparation cycle handoff chain bridge outer reentry continuation boundary
prepare_next_step (current_step_index >= 4) + exact approval/employee
    → Phase 130 exactly once in canonical six-argument order
    → exact PreparedWorkflowStep
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 131 (future explicit caller action)
```

## Phase 138: Prepared-step Start Cycle Handoff Chain Bridge Outer Reentry Continuation Boundary

Phase 138は、Phase 137のexact `PreparedWorkflowStep`と`EmployeeDefinition`を受けるprepared-start outer bridgeである。`step_index >= 5`、workflow/step/employee linkage、prepared predecessor state/history、completed prefix、直前predecessor provider=`"openai"`、terminal response/request/outputのexact contractを検証し、exact built-in empty success `output_text`も許容する。

valid routeでは公開Phase 131 `route_prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary()`へ、`(result, workflow, employee, state_path, events_path)`のcanonical five-argument orderとsupplied-object identityを保持してexactly once委譲する。返却されたexact `PreparedStepExecutionStart`とnested request/running stateを再検証し、state/eventsはbyte-for-byte不変に保つ。safe error identity、両target compensation、unexpected errorのsanitize、`dependency_rollback`、no retryを継承する。

`workflow_complete`と`persisted_failure`はemployee=`None`のunchanged zero-call stopであり、Phase 137より厳しいprovider/index条件を追加しない。workflow-complete success terminal outputはnon-emptyを維持する。Phase 138はPhase 124/132、persistence、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加せず、Focused testsはinjected Phase 131 fakesのみを用いる。

Phase 131の互換修正はprepared-step routeのexact empty success `output_text`だけを受理する狭い変更であり、workflow linkage、completed prefix、predecessor、terminal provider/response/request、failure semantics、stop behavior、public API、`terminal_history_contract.py`は変更しない。

```text
Phase 137
PreparedWorkflowStep | workflow_complete | persisted_failure
    ↓
Phase 138 prepared-step start cycle handoff chain bridge outer reentry continuation boundary
PreparedWorkflowStep (step_index >= 5) + exact employee
    → Phase 131 exactly once in canonical five-argument order
    → exact PreparedStepExecutionStart
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 132 (future explicit caller action)
```

## Phase 139: Prepared-start Persistence Cycle Handoff Chain Bridge Outer Reentry Continuation Boundary

Phase 139は、Phase 138のexact `PreparedStepExecutionStart`と`EmployeeDefinition`を受けるouter persistence bridgeである。`running_state.current_step_index >= 5`、exact nested request/running-state、workflow/step/employee linkage、Phase 138 predecessor terminal state/history、immediate predecessor provider=`"openai"`、terminal provider/response/request/outputのexact contractを検証し、exact built-in empty success `output_text`も許容する。

検証後、公開Phase 132 `route_prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary()`へ、supplied object identityを保持した`(result, workflow, employee, state_path, events_path)`のcanonical five-argument orderで正確に一度だけ委譲する。正常なexact `RunningStatePersistenceResult`ではstate targetをsupplied running stateのserialized bytesへ更新し、events targetをbyte-for-byte不変に保って同じresult objectを返す。malformed persistence、wrong state、event mutation、safe/unexpected errorは両targetを補償し、restore failureを`dependency_rollback`とする。retryは行わない。

`workflow_complete`と`persisted_failure`はemployee=`None`のunchanged zero-call stopであり、Phase 138より厳しいprovider/index条件を追加しない。workflow-completeのempty success outputは拒否する。Phase 139はPhase 125を直接参照・呼び出しせず、Phase 133、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しない。Focused testsはinjected Phase 132 fakesのみを用いる。

Phase 132にはprepared-start routeだけでexact built-in empty success `output_text`を受理する狭いfallbackを追加した。workflow current-step linkage、completed prefix、predecessor、terminal provider/response/request、failure semantics、stop behavior、public API、共有terminal history contractは緩和していない。

```text
Phase 138
PreparedStepExecutionStart | workflow_complete | persisted_failure
    ↓
Phase 139 prepared-start persistence cycle handoff chain bridge outer reentry continuation boundary
PreparedStepExecutionStart (running index >= 5) + exact employee
    → Phase 132 exactly once in canonical five-argument order
    → exact RunningStatePersistenceResult
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 133 (future explicit caller action)
```

## Phase 140: Non-final Empty-success Terminal-history Compatibility Repair

Phase 140は新しいorchestration boundaryではなく、共有`terminal_history_contract.py`の狭い互換・正確性修正である。Phase 139のprepared-start persistence default chainが、Phase 138/139で既に有効な非final succeeded historyを、Phase 132のfallbackだけでなくPhase 125、118、111、104へ実際に通せるようにする。

`state.status == "succeeded"`かつ`state.current_step_index < len(workflow.steps)`の場合に限り、terminal succeeded eventとそのhistory内のearlier succeeded eventについて、`output_text`をexact built-in `str`として空文字またはnon-emptyで受理する。`response_id`のnon-empty contract、workflow/current-step/index/employee linkage、`running -> succeeded`、failure-category/message、completed-step prefix、history order、file-loading、および既存のprovider/request意味論は緩和しない。

final succeeded history（`state.current_step_index == len(workflow.steps)`）のterminal empty outputは従来どおり拒否し、workflow-complete stop behaviorを変更しない。failed historyも変更しない。Phase 140はPhase 139→133 outer bridge、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behavior、公開APIを追加しない。

```text
non-final succeeded terminal history
    → exact success output_text may be empty
    → valid through the Phase 139 default persistence chain

final succeeded workflow-complete history
    → existing strict non-empty success-output contract remains
```

Phase 129のpersisted-success local validatorも、shared loaderが正常に返る非final
historyでは同じexact built-in `str`のempty/non-empty outputを受理する。final
persisted-successのlegacy fallbackは維持し、workflow-completeのempty success、
failed history、provider/request/response、workflow linkage、failure/message semanticsは
緩和しない。これはPhase 129の新機能ではなく、shared contractとの互換整合だけを
追加する最小修正である。

## Phase 141: Persisted-Running Execution Cycle Handoff Chain Bridge Outer Reentry Continuation Boundary

Phase 141は、Phase 139のexact `RunningStatePersistenceResult`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるouter boundaryである。実行routeでは`current_step_index >= 5`、exact workflow/start/request/running-state/employee/tools/credential/approval/transport、regular targets、Phase 139のpersisted running state bytes、succeeded predecessor history、immediate predecessor provider=`"openai"`を再検証する。exact built-in empty success `output_text`はPhase 140の非final契約どおり許容する。

検証後、公開Phase 133 `route_persisted_running_execution_cycle_handoff_chain_bridge_reentry_continuation_boundary()`へ、supplied object identityを保持した`(result, start, workflow, employee, state_path, events_path, resolved_tools, api_key, approval, transport)`のcanonical ten-argument orderで正確に一度だけ委譲する。exact `StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`、nested invocation result、provider=`"openai"`、workflow/step/index/employee linkage、target byte-for-byte不変性を再検証し、同じresult objectを返す。Phase 133はread-only dependencyとして扱い、正常returnでstate/eventsを変更させない。

`workflow_complete`と`persisted_failure`はexecution-only inputを`None`に限定し、Phase 133を呼ばないunchanged zero-call stop routeである。Phase 141は明示的なpersisted-running execution handoffを一回だけ行い、runtime-result persistence、classification、progression、retry、automatic continuation、finalization、scheduling、loop、parallel execution、CLI/GUI behaviorを行わない。safe error identityはsuccessful compensation後も保持し、unexpected error、malformed return、target mutationはdetail-safeに分類する。両targetを元bytesへ復元し、restore failureは`dependency_rollback`、retryは行わない。Focused testsはinjected Phase 133 fakesだけを使い、real provider、network、paid API、external tool、credential use、real transportを呼ばない。

Phase 133には、実行routeの非final predecessor historyだけを対象に、exact built-in empty success `output_text`を受理する狭い互換修正を追加した。`_valid_predecessor_event`の`allow_empty_output`スイッチを実行routeの`_check_predecessor`からのみ有効にし、workflow-complete final-historyのnon-empty契約、failed history、provider/response/request、failure semantics、stop behavior、public API、共有terminal history contractは緩和していない。Phase 141はPhase 126を直接参照・呼び出しせず、Phase 134、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しない。

```text
Phase 139
RunningStatePersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 141 persisted-running execution cycle handoff chain bridge outer reentry continuation boundary
RunningStatePersistenceResult (current_step_index >= 5) + exact execution inputs
    → Phase 133 exactly once in canonical ten-argument order
    → exact StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 134 (future explicit caller action)
```

## Phase 142: Runtime-Result Transition Persistence Cycle Handoff Chain Bridge Outer Reentry Continuation Boundary

Phase 142は、Phase 141のexact `StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるouter boundaryである。実行routeでは`current_step_index >= 5`、exact workflow/running-state/runtime-result linkage、regular targets、succeeded predecessor history、immediate predecessor provider=`"openai"`を再検証する。exact built-in empty success `output_text`は実行routeのpredecessor検証で許容する。

検証後、公開Phase 134 `route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary()`へ、supplied object identityを保持した`(result, workflow, state_path, events_path)`のcanonical four-argument orderで正確に一度だけ委譲する。exact `WorkflowExecutionPersistenceResult`、target identity、byte counts、reloadしたterminal state/history、terminal eventのlinkage・semantics、provider=`"openai"`、request/response provenanceを再検証し、同じpersistence result objectを返す。Phase 134は明示的に認可されたtransition-persistence stepであり、正常実行ではrunning stateをexact terminal stateへ置換し、terminal eventを正確に1件appendする。Phase 142はそのside effectを再検証する境界である。

`workflow_complete`と`persisted_failure`はterminal historyを先に検証し、Phase 134を呼ばないunchanged zero-call stop routeである。Phase 142は明示的なruntime-result transition persistence handoffを一回だけ行い、outcome classification、workflow progression、next-step preparation、prepared-step start、retry、automatic continuation、finalization、scheduling、loop、parallel execution、CLI/GUI behaviorを行わない。safe error identityはsuccessful compensation後も保持し、unexpected error、malformed return、target mutationはdetail-safeに分類する。両targetを元bytesへ復元し、restore failureは`dependency_rollback`、retryは行わない。Focused testsはinjected Phase 134 fakesだけを使い、real provider、network、paid API、external tool、credential use、real transportを呼ばない。

Phase 134には、実行routeの非final predecessor historyだけを対象に、exact built-in empty success `output_text`を受理する狭い互換修正を追加した。`_valid_predecessor_event`の`allow_empty_output`スイッチを実行routeの`_check_predecessor_history`からのみ有効にし、workflow-complete stop routeのterminal `output_text` non-empty契約、failed history、provider/response/request、failure semantics、stop behavior、public API、共有terminal history contractは緩和していない。Phase 142はPhase 127を直接参照・呼び出しせず、Phase 141、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しない。

```text
Phase 141
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure | workflow_complete | persisted_failure
    ↓
Phase 142 runtime-result transition persistence cycle handoff chain bridge outer reentry continuation boundary
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure (current_step_index >= 5) + exact runtime inputs
    → Phase 134 exactly once in canonical four-argument order
    → exact WorkflowExecutionPersistenceResult
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 135 (existing explicit caller action)
```

## Phase 143: Persisted-Transition Outcome Classification Cycle Handoff Chain Bridge Outer Reentry Continuation Boundary

Phase 143は、Phase 142のexact `WorkflowExecutionPersistenceResult`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるouter boundaryである。persistence/classification routeでは、exact workflow/step models、regular targets、supplied target identity、positive exact built-in byte counts、terminal state/history、current step index `>= 5`、succeeded predecessor history、immediate predecessor provider=`"openai"`、terminal event linkageを再検証する。検証後、公開Phase 135 `route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary()`へ、supplied object identityを保持した`(result, workflow, state_path, events_path)`のcanonical four-argument orderでexactly once委譲し、exact `PersistedExecutionOutcome`を再検証して同一objectで返す。正常経路では両targetをbyte-for-byte不変に保つ。

`workflow_complete`と`persisted_failure`はPhase 135を呼ばず、terminal state/historyを検証して同じobjectを返すunchanged zero-call stop routeである。stop routeはPhase 140–142のpredecessor compatibilityを維持し、非終端succeeded predecessorのexact built-in `str output_text == ""`を許容する。workflow_completeの最終terminal succeeded eventの`output_text` non-empty契約とpersisted-failure terminal semanticsは維持する。Phase 135 stop-route behaviorは広げない。

Phase 143はPhase 128を直接参照・呼び出しせず、Phase 136へ進まない。progression、next-step preparation、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorは追加しない。safe dependency errorはsuccessful compensation後もidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類して両targetを補償復元する。復元失敗は`dependency_rollback`、dependency callはretryしない。Focused testsはinjected Phase 135 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを実行しない。

```text
Phase 142
WorkflowExecutionPersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 143 persisted-transition outcome-classification cycle handoff chain bridge outer reentry continuation boundary
WorkflowExecutionPersistenceResult (current_step_index >= 5)
    → Phase 135 exactly once in canonical four-argument order
    → exact PersistedExecutionOutcome
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 136 (future explicit caller action)
```

## Phase 144: Classified Persisted-Outcome Progression Cycle Handoff Chain Bridge Outer Reentry Continuation Boundary

Phase 144は、Phase 143のexact `PersistedExecutionOutcome(persisted_success)`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるouter boundaryである。persisted-success routeでは、exact workflow/step models、regular targets、supplied target identity、positive exact built-in byte counts、terminal state/history、current step index `>= 5`、succeeded predecessor history、immediate predecessor provider=`"openai"`、terminal event linkageを再検証する。検証後、公開Phase 136 `route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary()`へ、supplied object identityを保持した`(result, workflow, state_path, events_path)`のcanonical four-argument orderでexactly once委譲し、返却されたexact `WorkflowProgressionDecision`を再検証して同一objectで返す。正常経路では両targetをbyte-for-byte不変に保つ。

`workflow_complete`と`persisted_failure`はPhase 136を呼ばず、terminal state/historyを検証して同じobjectを返すunchanged zero-call stop routeである。stop routeは`minimum_index=1`を受理し、non-openai terminal providerと非終端succeeded predecessorのexact built-in `str output_text == ""`を許容する。workflow_completeの最終terminal succeeded eventの`output_text` non-empty契約とpersisted-failure terminal semanticsは維持する。Phase 136のpersisted-success routeは空predecessor `output_text`を許容するが、stop routeはnon-empty契約を維持する。

Phase 144自身はprogression logicを重複実装しない。public Phase 136をexactly once呼ぶことで、明示的に認可された1回のprogression handoffを実行する。Phase 129は直接呼ばず、Phase 137へ自動継続しない。next-step preparation、step start、start-state persistence、runtime execution、runtime-result persistence、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorは追加しない。Phase 129/137/143のpublic route identifier、`._validate_`、`._top`、`._raise`は使用しない。safe dependency errorはsuccessful compensation後もidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類して両targetを補償復元する。復元失敗は`dependency_rollback`、dependency callはretryしない。Focused testsはinjected Phase 136 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを実行しない。

```text
Phase 143
PersistedExecutionOutcome(persisted_success) | workflow_complete | persisted_failure
    ↓
Phase 144 classified persisted-outcome progression cycle handoff chain bridge outer reentry continuation boundary
PersistedExecutionOutcome(persisted_success) (current_step_index >= 5)
    → Phase 136 exactly once in canonical four-argument order
    → exact WorkflowProgressionDecision
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 137 (future explicit caller action; not called by Phase 144)
```

## Phase 145: Progression-to-Approved Preparation Cycle Handoff Chain Bridge Outer-Chain Reentry Continuation Boundary

Phase 145は、Phase 144のexact `WorkflowProgressionDecision(prepare_next_step)`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるouter-chain boundaryである。prepare routeでは、exact workflow/step models、regular targets、current step index `>= 5`（Phase 137の`>= 4`より強いprovenance）、current/next/reason linkage、completed-step prefix、Phase 144 provenanceのsucceeded predecessor history、immediate predecessor provider=`"openai"`、terminal event linkage（terminal provider=`"openai"`、response_id non-empty、request_id `None`またはnon-empty、success `output_text`はexact built-in `str`でemptyも許容）を再検証する。検証後、公開Phase 137 `route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary()`へcanonical six-argument order `(result, workflow, approval, employee, state_path, events_path)`でexactly once委譲し、返却されたexact `PreparedWorkflowStep`を再検証して返す。正常経路では両targetをbyte-for-byte不変に保つ。

`workflow_complete`と`persisted_failure`はPhase 137を呼ばず、terminal state/historyを検証して同じobjectを返すunchanged zero-call stop routeである。stop routeは`minimum_index=1`を受理し、non-openai terminal providerとsucceeded predecessorのexact built-in `str output_text == ""`を許容するが、workflow_completeの最終terminal succeeded eventの`output_text` non-empty契約とpersisted-failure terminal semanticsは維持する。

Phase 145自身はprogression logicを重複実装しない。public Phase 137をexactly once呼ぶことで、明示的に認可された1回のprogression-to-preparation handoffを実行する。Phase 130/138/144のpublic route identifier、`._validate_`、`._top`、`._raise`は使用しない。Phase 145はPhase 138や他の後続phaseを直接呼ばず、provider、network、paid API、external tool、credential、transport、start-state persistence、retry、自動継続を実行しない。safe dependency error（Phase 137 error）はsuccessful compensation後もidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類して両targetを補償復元する。復元失敗は`dependency_rollback`、retryはない。Focused testsはinjected Phase 137 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを実行しない。

```text
Phase 144
WorkflowProgressionDecision(prepare_next_step) | workflow_complete | persisted_failure
    ↓
Phase 145 progression-to-approved-preparation cycle handoff chain bridge outer-chain reentry continuation boundary
prepare_next_step (current_step_index >= 5, Phase 144 provenance)
    → Phase 137 exactly once in canonical six-argument order
    → exact PreparedWorkflowStep
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 138 (future explicit caller action; not called by Phase 145)
```

## Phase 146: Prepared-Step Start Cycle Handoff Chain Bridge Outer-Chain Reentry Continuation Boundary

Phase 146は、Phase 145のexact `PreparedWorkflowStep`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるouter-chain boundaryである。prepared-step routeでは、exact `PreparedWorkflowStep`、exact `WorkflowDefinition`/`WorkflowStepDefinition`、exact `EmployeeDefinition`、regular targets、exact built-in `int step_index >= 6`（Phase 145 continuation provenance）、workflow/step/index/employee linkage、employee ID/instructions/model/allowed-tools linkage、exact built-in tuple `allowed_tool_names`、persisted predecessor terminal succeeded state（`prepared.step_index - 1`、index `>= 5`）、complete ordered succeeded predecessor/terminal history、succeeded completed-step prefix、`last_failure_category is None`、Phase 145 provenance（全historyがexact `RuntimeStepEvent`、earlier predecessorはexact `step_succeeded`・`running -> succeeded`・provider non-empty str・immediate predecessor provider=`"openai"`・still earlier providerはvalid non-`openai`・response_id/request_id non-empty・`output_text`はexact built-in `str`でemptyも許容・failure_category/messageは`None`、terminal eventはprovider=`"openai"`・request_id `None`またはnon-empty・response_id non-empty・`output_text`はexact built-in `str`でemptyも許容）を再検証する。検証後、公開Phase 138 `route_prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary()`へcanonical five-argument order `(result, workflow, employee, state_path, events_path)`でexactly once委譲し、返却されたexact `PreparedStepExecutionStart`（exact nested `ModelInvocationRequest`/`WorkflowExecutionState`、request linkage、running stateはstatus=`"running"`・`current_step_index >= 6`・completed-step prefix継承・`last_failure_category is None`）を再検証して返す。正常経路では両targetをbyte-for-byte不変に保つ。

`workflow_complete`と`persisted_failure`はPhase 138を呼ばず、Phase 145 stop-route契約でterminal state/historyを検証して同じobjectを返すunchanged zero-call stop routeである。stop routeは`step_index >= 6`を課さず、non-openai terminal providerとsucceeded predecessorのexact built-in `str output_text == ""`を許容するが、workflow_completeの最終terminal succeeded eventの`output_text` non-empty契約とpersisted-failure terminal semanticsは維持する。

Phase 146自身はstart logicを重複実装しない。public Phase 138をexactly once呼ぶことで、明示的に認可された1回のprepared-step-start handoffを実行する。Phase 131/139/145のpublic route identifier、`._validate_`、`._top`、`._raise`は使用しない。Phase 146はPhase 139、provider、network、paid API、external tool、credential、transport、start-state persistence、retry、自動継続を実行しない。safe dependency error（Phase 138 error）はsuccessful compensation後もidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類して両targetを補償復元する。復元失敗は`dependency_rollback`、retryはない。Focused testsはinjected Phase 138 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを実行しない。

```text
Phase 145
PreparedWorkflowStep | workflow_complete | persisted_failure
    ↓
Phase 146 prepared-step start cycle handoff chain bridge outer-chain reentry continuation boundary
PreparedWorkflowStep (step_index >= 6) + exact employee
    → Phase 138 exactly once in canonical five-argument order
    → exact PreparedStepExecutionStart
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 139 (future explicit caller action; not called by Phase 146)
```
