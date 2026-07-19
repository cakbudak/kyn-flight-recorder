# Clean-clone verification contract

A clean clone requires Python 3.11+, a modern browser, and `OPENAI_API_KEY` only for real
model actions.

```bash
git clone https://github.com/cakbudak/kyn-flight-recorder.git
cd kyn-flight-recorder
cp .env.example .env
# set OPENAI_API_KEY in .env
python3 scripts/verify.py
python3 serve.py
```

No `pip`, `npm`, compiler, migration command, Kyn package, external database, or build step is
required. `Store.initialize()` creates the flat SQLite schema on first start.

The deterministic browser proof additionally requires Node 20+ and Chromium:

```bash
python3 scripts/verify.py --browser
```

The real-model gate is intentionally separate because it consumes API calls.
