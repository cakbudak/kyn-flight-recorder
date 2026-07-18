# Privacy and data lifecycle

Kyn.ist Flight Recorder is local-first and telemetry-free. The bundled trace is
synthetic. The normal demo makes no application network request beyond loading
its own local files from `serve.py`.

| Data | Source | Location | Lifetime | Deletion |
| --- | --- | --- | --- | --- |
| Bundled sample | repository | `app/data/demo-run.json` | repository lifetime | remove repository |
| Imported trace | user-selected JSON | browser memory | current page | reload, close, or Reset demo |
| Command receipt | local demo action | browser `sessionStorage` | current tab session | Reset demo or close tab |
| HTTP access line | local request | server process stdout | terminal/session policy | stop/clear terminal |
| GPT-5.6 review packet | explicit script invocation | OpenAI API transit | OpenAI API policy | governed by API account; request uses `store: false` |
| GPT-5.6 evidence | model response subset | `evidence/gpt-5.6-review.json` | repository lifetime | forward change removing artifact |

The GPT-5.6 runner is not invoked by the application. It extracts an explicit
allow list from the synthetic fixture, uses `store: false`, and persists hashes,
model/response identifiers, token totals, and the structured review. It does not
persist the API key or raw request/response. Run `--dry-run` to inspect hashes and
packet size without network access.

Redaction protects values under declared sensitive key classes before rendering,
but it is not a content-aware data-loss-prevention system. Do not import real
credentials, personal data, or confidential production traces into this Build
Week cut.

There are no cookies, analytics, ad identifiers, service workers, remote fonts,
or third-party scripts.
