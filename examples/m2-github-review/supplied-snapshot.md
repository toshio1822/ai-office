# Supplied offline GitHub review snapshot

This fixture is fictional and sanitized. It was supplied to the workflow; it is not
evidence of live GitHub, repository, pull-request diff, or CI inspection.

## Issue snapshot

- Repository: `example/acme-widget`
- Issue: `example/acme-widget#314`
- Title: Reject incomplete widget configuration before publication
- Source reference: `fixture://issue/314`
- Requirements:
  - R1: reject a configuration when `display_name` is missing or blank;
  - R2: return a safe validation message without echoing the submitted value;
  - R3: preserve the existing valid-configuration publication path.

## Pull request snapshot

- Pull request: `example/acme-widget#2718`
- Title: Validate widget display name before publication
- Source reference: `fixture://pull/2718`
- Described changed files:
  - `src/widget/validation.py`: adds the required-field check;
  - `tests/test_widget_validation.py`: adds missing, blank, and valid cases;
  - `docs/widget-publication.md`: documents the validation failure.
- Described behavior: validation runs before the publication transport is selected.

## Validation snapshot

- Source reference: `fixture://ci/2718/unit`
- Unit tests: supplied status `passed`, 84 tests.
- Lint: supplied status `passed`.
- Integration tests: not supplied.
- Live branch protection and required-check configuration: not supplied.

## Supplied review notes

- The snapshot does not include the actual diff or implementation source.
- The snapshot does not include a test proving zero publication transport calls on
  invalid input.
- Whether whitespace-only Unicode values are normalized before validation is unknown.
