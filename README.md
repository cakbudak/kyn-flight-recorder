# Kyn.ist Flight Recorder

Standalone Build Week edition of Kyn Console: a dependency-free, local-first
flight recorder for autonomous agent runs.

This repository was created during OpenAI Build Week 2026. It turns one portable
agent trace into a causal graph, a deterministic replay, and a revision-fenced
intervention receipt.

## Run

```bash
python3 serve.py
```

Open <http://127.0.0.1:4173/app/>. No package installation, database, secret,
cloud account, build step, or network service is required.

## Status

`implemented` — the standalone journey and local trace import work; formal proof
and submission evidence follow in this history.

## Product contract

See [`docs/product-contract.md`](docs/product-contract.md).

## License

MIT — see [`LICENSE`](LICENSE).
