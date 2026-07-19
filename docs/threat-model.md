# Threat model

## Boundary

The public surface is an anonymous, short-lived Build Week lab. The browser and same-origin
Python API are untrusted at their input boundary. OpenAI output is untrusted model output.
SQLite and the statically coded tool registry are the local authority boundary.

The configurable public Data Store Action creates an idempotent row in
`automation_effects`. There is no shell, arbitrary filesystem, arbitrary URL
fetch, MCP connector, production deployer, or generic code registry.

## Threats and controls

| Threat | Control | Residual risk |
| --- | --- | --- |
| Model requests an unauthorized tool | Responses receives only effective tools; runtime rechecks static registry and pinned skill grants | model call can fail, but cannot gain authority |
| Model prose claims an effect | terminal outcome derives from committed tool receipts/effect rows, never prose | misleading prose may exist only as hashed output, not product truth |
| Prompt injection widens authority | code validates every call against pinned Skill grants, strict schemas, and static callable Action kinds | untrusted content can still degrade model output or make a Run fail |
| Diagnosis invents evidence | deterministic candidate plus exact owned evidence-id validation | supported fault vocabulary is intentionally narrow |
| Repair changes unrelated fields | one operation, allowed operation/path/value checks, proposal hash, exact Action and Flow revision fences | only the public Data Store authority-policy mismatch is automatically repairable |
| Agent applies its own repair | no apply tool; separate human HTTP command requires acknowledgement, actor, reason, hash, and revision | anonymous actor label is not strong identity |
| Stale/concurrent approval | `BEGIN IMMEDIATE` compare-and-swap on flow revision; idempotent identical replay | SQLite limits horizontal scaling |
| Cross-workspace read | random opaque cookie token, only hash stored, every lookup scoped by workspace | anonymous bearer cookie can be used by anyone who steals it |
| CSRF/cross-origin mutation | exact same-origin validation, SameSite=Strict cookie, no CORS | same-origin script compromise remains in scope of CSP/code integrity |
| Overspend/denial of service | body, turn, tool, output-token, workspace, address/global hourly, and concurrency bounds | distributed abuse behind shared/proxied addresses can still consume global budget |
| API-key disclosure | explicit temporary/restricted-key warning, browser-tab session storage, same-origin model-command header, ephemeral SDK client, no operator fallback, no key in DB/events/responses/logs | browser or host compromise remains a visitor-owned risk; never use a production credential |
| Secret in tool payload | exact schemas plus recursive secret-key redaction before persistence/render | redaction is key-based, not semantic DLP |
| Event tampering | append-only triggers, contiguous sequence, predecessor + SHA-256 chain | host/DB owner can replace the entire database |
| Proxy spoofing | deployment is behind Cloudflare/Traefik; nginx forwards host/proto and bounded client metadata | direct exposure of host port must be prevented by firewall |

## Failure behavior

- A missing key is rejected before an operator model command starts. Webhook and
  schedule activation are the deliberate exception: they persist a fully pinned
  `created` Run with `run.credential_required` evidence, but perform no model I/O
  until a workspace operator continues it with a browser-owned key. Provider
  failure after Run creation becomes terminal `failed` evidence and is never
  mislabeled as an authority-policy block.
- Invalid Responses shapes, bad JSON, missing required tools, unknown tools, extra structured
  fields, foreign evidence, and unsafe repair paths fail closed.
- External I/O is never performed while a SQLite write transaction is open.
- HTTP errors expose stable codes and safe messages, not provider bodies or SQL details.

## Deployment assumptions

- TLS terminates before nginx; `X-Forwarded-Proto=https` is preserved so cookies are Secure.
- The host runtime binds only to the Docker bridge (`172.17.0.1`), so the nginx container can
  reach it without exposing the port on the public host interface.
- The service has no operator OpenAI credential; non-secret settings and SQLite
  remain readable only within the hardened service boundary.
- The operator controls SQLite retention, backups, host logs, firewall, and service updates.

## Explicitly not proved

This cut does not prove strong user identity, production connector safety,
multi-host SQLite coordination, arbitrary automatic workflow repair,
prompt-injection resistance for untrusted user prompts, or host-level tamper
resistance. Those are not claimed by the submission.
