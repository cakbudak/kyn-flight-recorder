# Threat model

Date: 2026-07-18  
Target: applicable OWASP ASVS 5.0 Level 1 controls for this local static surface  
Claim boundary: security review target, not ASVS certification

## Assets and trust boundaries

Protected assets are the developer's trace content, local command receipt, causal
integrity, browser context, and API key used by the separate GPT-5.6 evidence
runner. The browser trusts repository code and the bundled synthetic fixture. A
user-selected import is untrusted until validation and redaction finish. The
Python server is a read-only loopback origin. The OpenAI API boundary exists only
when a developer explicitly runs `scripts/gpt56_review.py` without `--dry-run`.

The demo has no identity system, database, production credential, connector, or
external tool authority. Those are exclusions, not silently assumed controls.

## Threats and controls

| Threat | Attack path | Control | Evidence |
| --- | --- | --- | --- |
| Script injection | HTML/script strings in imported fields | JSON parse, fail-closed contract, dynamic DOM via `textContent`, CSP rejects inline/remote script | static sink tests + browser CSP check |
| Credential disclosure | Secret-like fields in fixture/import | recursive key-class redaction before state/render; allow-listed GPT packet | core redaction tests + GPT packet negative test |
| Trace confusion | Dangling graph, foreign correlation, duplicate/gapped events | unique ids, endpoint checks, exact correlation, contiguous sequence validation | negative state-machine tests |
| Confused deputy | Crafted trace requests a different command/effect | only `approve_tool_call`; blocked-only source; fixed actor; acknowledgement; standalone `external_effect=false` | command-contract tests |
| Lost update/replay | Stale revision or duplicate apply | compare expected/current revision; one-revision transition; idempotency receipt; terminal absorption | revision/idempotency tests |
| Local persistence surprise | Receipt survives reload | session storage only, fixture-bound rehydration, visible Reset deletion | reload/reset browser journey |
| Resource exhaustion | Very large import or recursive data | 1 MiB file cap; local browser only | browser import path; residual depth limit below |
| Path traversal/listing | HTTP request escapes project root | stdlib path resolution, no listing, only static reads, missing/traversal 404 | server negative tests |
| Unauthorized write | POST or app network call | server has no write handler; UI has no effect endpoint; browser network inventory must stay local | server + browser checks |
| Supply-chain execution | Package install/build fetches code | no runtime or test package install; remote assets absent | clean runtime contract + static tests |
| API-key leakage | GPT evidence runner logs/persists key | environment-only key, fixed HTTPS endpoint, sanitized artifact, no raw request/response persistence | evidence-runner tests; code review |
| Model authority escalation | GPT review authorizes a command | review runner is separate from app and output is evidence-only | architecture boundary + no import path from evidence result |

## Security headers

`serve.py` sends a restrictive Content Security Policy, `nosniff`, no-referrer,
same-origin opener/resource policy, denied framing, and `Cache-Control: no-store`
for application and JSON responses. These headers are defense in depth; DOM-safe
construction and semantic validation remain the primary controls.

## Residual risks

- Redaction is key-name based, not a general secret-content classifier. A secret
  hidden under an innocent key may render. Do not import real secrets.
- JSON nesting depth is bounded indirectly by the 1 MiB limit, not an explicit
  depth counter. A hostile deeply nested file could still stress a browser parser.
- A valid synthetic trace can lie about its observations. Authenticity/signature
  verification is outside this cut.
- Loopback serving does not provide user authentication. Do not bind to a public
  interface on an untrusted network.
- The GPT evidence call sends the allow-listed synthetic packet to OpenAI when
  explicitly executed. It is not part of offline demo operation.
- No physical assistive-technology or cross-browser security pass was available.

## Release rule

Do not relabel this cut as production-live without adding authenticated trace
provenance, explicit tenant authorization, durable audit storage, rate/depth
limits, connector isolation, and real deployment threat modeling.
