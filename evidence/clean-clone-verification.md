# Clean-clone verification contract

The standalone runtime requires Python 3.11+ and the official OpenAI SDK. A key is
optional: deterministic Actions and Flows work without one; model commands take a
visitor-supplied key through the browser Settings view.

```bash
git clone https://github.com/cakbudak/kyn-agent-studio.git
cd kyn-agent-studio
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm ci
npm run build
.venv/bin/python scripts/verify.py --browser --performance
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

On 2026-07-21, public runtime commit
`8013539dceff432318b17e4da580dea718f534da` was cloned from the public GitHub URL
with the credential helper and global/system Git configuration disabled. In that
untouched clone:

- `pip install -r requirements.txt` and `npm ci` completed;
- 274 Python contract tests passed;
- 9 pure browser-state tests passed;
- the production frontend rebuilt byte-for-byte to the committed asset names;
- the deterministic Chromium product journey passed 45/45, including completion
  refusal/admission on one pinned Flow version and the complete Capability Forge
  quarantine/qualification/promotion path;
- `npm audit` reported zero vulnerabilities;
- `pip check` reported no broken requirements;
- `.env` and `OPENAI_API_KEY` were absent;
- `git status --short` remained empty after build and verification.

The clean clone path was temporary and contained no configuration symlink,
private Kyn dependency, pre-existing database, or uncommitted asset. This
proof verifies the runtime source deployed at `https://buildweek.kyn.ist`; later
evidence-only commits do not change that runtime tree.
