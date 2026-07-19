# Live publication verification

Date: 2026-07-19  
Public origin: <https://buildweek.kyn.ist/app/>  
Repository: <https://github.com/cakbudak/kyn-flight-recorder>  
Candidate commit under test: `7f700acb65de90c3c57e193597eeee18229d0543`

## Delivery boundary

The application is served from a read-only clone of the GitHub repository through
the existing Kyn.ist Cloudflare → Traefik → nginx path. The versioned nginx server
block is [`deploy/nginx-buildweek.conf`](../deploy/nginx-buildweek.conf). It exposes
only `/app/`, `/schema/`, `/healthz`, and the HTTPS redirect from `/`; it does not
run `serve.py`, a package manager, a database, or a Kynist service.

The `*.kyn.ist` DNS record and shared Traefik path already existed. This release
added no new credential or dedicated network service.

## RED → GREEN deployment proof

The first real-browser run against the public origin failed before render. Direct
header inspection located two origin defects: `/` emitted an HTTP redirect because
nginx saw the internal proxy hop, and `.mjs` files were served as
`application/octet-stream`. The live browser timed out waiting for the initial
`blocked` state, proving that the deployment was not usable despite HTTP 200 on the
HTML document.

The forward correction pins the redirect to HTTPS and declares
`application/javascript` for the app's `.mjs` files. After nginx config validation
and reload, the identical browser command completed **38/38 checks** with first
meaningful render at **226.1 ms**. The generated report is
[`live-browser-verification.json`](live-browser-verification.json).

An independent Playwright 1.60.0 run then exercised the visible UI controls on the
same public origin: queue and approval evidence, acknowledgement, authorized
transition, receipt, reload persistence, reset, and the 390 × 520 dialog all
passed. It observed one request origin, no failed requests, and no console errors.
The captured result is [`live-playwright-verification.json`](live-playwright-verification.json).
Playwright was an external verification tool, not a project dependency.

## Reproducible checks

```bash
python3 scripts/verify.py
node scripts/browser_verify.mjs --base-url https://buildweek.kyn.ist
curl -I https://buildweek.kyn.ist/
curl -I https://buildweek.kyn.ist/app/
curl https://buildweek.kyn.ist/healthz
```

| Gate | Observed result |
| --- | --- |
| HTTPS root | `307` to `https://buildweek.kyn.ist/app/` |
| Application | `200 text/html` |
| Health | `200 application/json`; `mode=standalone-demo`; `external_dependencies=0` |
| JavaScript module | `200 application/javascript` |
| Trace schema | `200 application/json` |
| CSP | restrictive same-origin policy with `object-src 'none'` and `frame-ancestors 'none'` |
| Browser cache | `Cache-Control: no-store` |
| Transport | HSTS; certificate covers `*.kyn.ist` and `kyn.ist` |
| Hidden/source paths | `/README.md`, `/.git/config`, `/docs/`, missing paths, and normalized traversal: `404` |
| Write-like methods | POST, PUT, PATCH, and DELETE on `/healthz`: `405` |
| Full Chromium journey | `38/38 PASS`; desktop, mobile, short dialog, keyboard, reload, reset, empty/error, invalid import |
| Independent Playwright journey | `PASS`; visible controls, receipt, reload, reset, short-mobile dialog |
| Browser network inventory | no cross-origin request, failed request, console error, or page exception |

## Remote artifact proof

Before deployment, an isolated authenticated clone of the private candidate remote
resolved to the exact commit above. It passed all 28 Python checks, all 25 JavaScript
checks, and all 38 local Chromium checks, then remained clean. Anonymous cloning is
deliberately deferred until the repository visibility changes to public; that final
gate must be appended before submission.

Verdict: **LIVE CANDIDATE, NOT YET PUBLICATION-COMPLETE**. The HTTPS demo is working;
GitHub visibility and anonymous-clone proof remain intentionally open.
