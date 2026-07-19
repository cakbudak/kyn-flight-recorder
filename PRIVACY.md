# Privacy and data lifecycle

Kyn.ist Agent Studio has no analytics, advertising, remote fonts, third-party
browser scripts, or application telemetry. The browser talks only to its own
origin. OpenAI access occurs through the same-origin server using the visitor's
browser-owned credential.

| Data | Stored where | Purpose | Lifetime / deletion |
| --- | --- | --- | --- |
| Workspace token | browser `HttpOnly` cookie; SHA-256 hash in SQLite | isolate one anonymous lab | cookie and access expire after 24 hours |
| Agent resources and runs | local SQLite | execute and inspect the closed loop | operator-controlled demo database retention |
| Safe event payloads | local SQLite | authoritative replay evidence | same as workspace rows |
| Model-call metadata | local SQLite | response id, model, status, usage, input/output hashes | same as workspace rows |
| Tool arguments/results | local SQLite after recursive secret-key redaction | receipt and effect proof | same as workspace rows |
| Sandbox effects | local SQLite | prove safe idempotent local effects | same as workspace rows |
| API key | browser `sessionStorage`, then ephemeral server SDK client for one model command | authenticate Responses calls | tab lifetime; never written by the application |
| OpenAI request | OpenAI API transit with `store: false` | agent inference | governed by the API account and OpenAI API policy |
| HTTP metadata | process/hosting access logs | operation and abuse response | operator/infrastructure policy |

Workspace expiry denies further API access; it is not represented as deletion of immutable
evidence. The Build Week operator may rotate or remove the entire demo SQLite database under
its retention policy. Do not submit personal, confidential, or regulated data to this public
lab.

Prompts sent to OpenAI contain visitor-supplied Flow input, pinned
Agent/Prompt/Skill instructions, and bounded safe evidence required for the
selected Action or Repair Lab command. They do not contain the workspace cookie,
API key, unrelated workspace rows, or hidden reasoning. Every request sets
`store: false`.

The application recursively redacts values whose keys look like credentials, tokens,
passwords, secrets, cookies, or authorization data before tool arguments/results enter the
ledger. This is defense in depth, not a general data-loss-prevention system.

Committed evidence contains screenshots plus browser assertions over safe ids, hashes,
statuses, counts, and effects. It intentionally excludes raw provider requests/responses,
full prompt text, cookies, the API key, and hidden reasoning.
