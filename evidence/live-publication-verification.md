# Live publication verification

- Date: 2026-07-19
- Public origin: <https://buildweek.kyn.ist/app/>
- Public repository: <https://github.com/cakbudak/kyn-flight-recorder>
- Publication commit under anonymous-clone test:
  `de9d0b6123cfed6475da0f18059b34bdb776ecd6`
- Live application and origin configuration introduced at:
  `7f700acb65de90c3c57e193597eeee18229d0543`

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

Before visibility changed, an isolated authenticated clone of the private candidate
resolved to `de9d0b6`. It passed all 28 Python checks, all 25 JavaScript checks, and
all 38 local Chromium checks, then remained clean.

After the repository became public, a second isolated HTTPS clone ran with GitHub
tokens unset, credential helpers disabled, and interactive prompting disabled. It
resolved to the same `de9d0b6`, passed the same **28 + 25 + 38** checks, and remained
clean. The logged-out GitHub page rendered the README, public license, homepage,
topics, and committed screenshot asset.

Verdict: **PUBLICATION-COMPLETE**. Repository and demo are unrestricted. Video,
Devpost registration, entry creation, and final submission remain separate gates.
