# Repository instructions

These rules apply to the whole repository.

## Product boundary

- This is the standalone Build Week cut of Kyn Console.
- The demo must run without Kynist, Appiyon, Kynllm, Ainou, PostgreSQL, secrets,
  cloud services, package installation, or a build step.
- Runtime truth is deterministic fixture data plus browser-local demo state.
- Never describe simulated state as a live production integration.

## Architecture

- One application entry: `app/index.html` served by `serve.py`.
- One canonical fixture: `app/data/demo-run.json`.
- One intervention state machine: preview, authorize, apply, acknowledge.
- No second data store, graph model, or mutation path.

## Quality

- WCAG 2.2 AA is the target.
- Keyboard, focus, reduced motion, narrow viewport, empty/error states, and
  deterministic reset are required.
- Security-sensitive content is fixture-only and redacted before rendering.
- Tests use the Python standard library; browser verification may use an external
  harness but cannot become a runtime dependency.

## Git mandate: forward only

Do not reset, revert, stash, rebase, amend, squash, switch branches, or rewrite
history. Work on the active branch. At each stable state, stage every change with
`git add -A` and create a new commit.
