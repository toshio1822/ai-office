# Synthetic engineering evidence sample

This fixture is fictional and exists only to demonstrate that the reusable
research workflow is not tied to a particular product or device domain.

## Investigation question

Why might a configuration loader accept a file that omits a required timeout?

## Supplied evidence

- Issue excerpt (`fixture://example/issues/17`): a maintainer reports that a
  client started without an explicit timeout. The behavior has not been
  independently reproduced.
- Source excerpt (`fixture://example/revision/r1`, `src/client_config.py:20-27`):
  the `timeout_seconds` field has a default of `None`; this excerpt does not show
  how the value is consumed.
- Test excerpt (`fixture://example/revision/r1`, `tests/test_client_config.py:8-14`):
  the supplied test covers an explicit timeout only.

## Limits and safe scope

The deployed revision, runtime logs, and downstream timeout behavior are not
provided. Do not infer a root cause or claim reproduction. Further checks should
begin with read-only inspection of the deployed revision, relevant logs, and
existing tests; no network, production, or destructive operations are authorized
by this sample.
