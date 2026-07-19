# Clean-clone verification contract

The standalone runtime requires Python 3.11+ and the official OpenAI SDK. A key is
optional: deterministic Actions and Flows work without one; model commands take a
visitor-supplied key through the browser Settings view.

```bash
git clone https://github.com/cakbudak/kyn-agent-studio.git
cd kyn-agent-studio
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/verify.py
.venv/bin/python serve.py
```

Open <http://127.0.0.1:4173/app/>. Do **not** put `OPENAI_API_KEY` in the server
environment: the composition root deliberately ignores operator keys.
`Store.initialize()` creates the flat SQLite schema on first start; no private Kyn
package, external database, compiler, migration command, or frontend build is
required.

The full Chromium proof additionally requires Node 20+, npm, and system Chromium:

```bash
npm ci
node scripts/browser_verify.mjs
```

The default browser proof is deterministic. A real-model run is intentionally
separate because it consumes the visitor's API quota.

## Current clean-clone proof

On 2026-07-19, public source commit
`7770410f002cfe09dd0a48a8645cf7d473144fcf` was cloned from the renamed GitHub URL
with the credential helper disabled. In that untouched clone:

- `pip install -r requirements.txt` completed;
- 68 Python contract tests passed;
- 7 pure browser-state tests passed;
- the production frontend rebuilt successfully;
- the current Chromium product journey passed 30/30;
- `npm audit` reported zero vulnerabilities;
- `OPENAI_API_KEY` was absent;
- `git status --short` remained empty after build and verification.

The clean clone path was temporary and contained no configuration symlink,
private Kyn dependency, pre-existing database, or uncommitted asset. This evidence
commit adds reports and documentation only; the product source verified above is
unchanged.
