# Emory STEM Advisor

AI/LLM advisor that ingests internal documentation and surfaces relevant
information (RAG-based web app: Python backend API + React frontend).

## Contributing

You can't push here directly: fork, branch, and open a pull request.
Step-by-step instructions: [CONTRIBUTING.md](CONTRIBUTING.md).

## Layout

- `backend/` — Python backend (`app/` package, `tests/`)
- `frontend/` — React frontend
- `docs/` — project documentation
- `data/` — source documents, indexes, caches (gitignored)

## Backend quick start

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e "backend[dev]"   # first time also:
                                                        # pip install torch --index-url https://download.pytorch.org/whl/cpu
cd backend
.venv/Scripts/python -m pytest                          # fast unit tests
.venv/Scripts/python -m scripts.smoke_local_model       # loads MiniCPM-2B, generates once (~4 GB first-run download)
```

Config is env-driven: `LLM_PROVIDER` (default `hf-local`), `HF_MODEL_ID`
(default `openbmb/MiniCPM-2B-128k`), `HF_MAX_CONTEXT` (default 4096),
`HF_MAX_NEW_TOKENS` (default 256). See `docs/architecture.md`.
