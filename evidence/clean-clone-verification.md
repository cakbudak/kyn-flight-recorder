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
`752489e92101b6f12e938988230b17b218ef8411` with tree
`b525497acea0bb4a32774da265d1c0272f4dacaa` was cloned from the public GitHub URL
with interactive authentication and the credential helper disabled. In that
untouched clone, with `OPENAI_API_KEY` removed from the process environment:

- `pip install -r requirements.txt` and `npm ci` completed;
- 291 Python contract tests passed;
- 9 pure browser-state tests passed;
- the production frontend rebuilt byte-for-byte to the committed asset names;
- the deterministic Chromium product journey passed 56/56, including a
  visible and pointer-reachable Knowledge import confirmation, exact SmartRead
  citations, a concurrent three-member BoardRoom with a code-owned quorum
  barrier, governed Memory promotion and recall, completion refusal/admission
  on one pinned Flow version, and the complete Capability Forge
  quarantine/qualification/promotion path;
- the 64-node/63-route runtime load gate passed at 324.477 ms p95 for a complete
  Run and 170.806 ms p95 for the loaded workspace snapshot, below the 2,000 ms
  and 400 ms release limits;
- the 64-node Chromium editor rendered in 139.924 ms, Fit View completed in
  70.882 ms, all 128 independent source handles were present, and the page had
  zero document overflow;
- `npm audit` reported zero vulnerabilities;
- `pip check` reported no broken requirements;
- `.env` and `OPENAI_API_KEY` were absent;
- the private `deploy/` and `submission/` release-material directories were absent;
- no `deploy/` or `submission/` object existed anywhere in the cloned history;
- `git status --short` remained empty after build and verification.

The clean clone path was temporary and contained no configuration symlink,
private Kyn dependency, pre-existing database, or uncommitted asset. This
proof verifies the runtime source deployed at `https://buildweek.kyn.ist`; later
evidence-only commits do not change that runtime tree.
