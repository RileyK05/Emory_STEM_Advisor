"""Launch the whole local development stack with one command.

From the repository root, with the project venv active:

    python dev.py

Starts, in order:
  * the background worker (`python -m scripts.worker`),
  * the Vite dev server (`npm run dev`),
  * the API in this process (`scripts.serve`), so Ctrl-C shuts it down cleanly
    and stops the embedded Postgres instead of leaving a dirty data dir.

Press Ctrl-C once to stop everything. The API also accepts the usual
`scripts.serve` flags, e.g. `python dev.py --port 9000`.

Manual equivalent (three terminals, from the repository root):
    cd backend && ../.venv/Scripts/python -m scripts.serve
    cd backend && ../.venv/Scripts/python -m scripts.worker
    cd frontend && npm run dev          # needs frontend/.env.local -> VITE_API_MODE=http
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def _ensure_frontend_uses_http() -> None:
    """The dev client defaults to the in-browser mock corpus; point it at the API."""
    env_local = FRONTEND / ".env.local"
    if not env_local.exists():
        env_local.write_text("VITE_API_MODE=http\n", encoding="utf-8")
        print("[i] wrote frontend/.env.local (VITE_API_MODE=http)")
    elif "VITE_API_MODE=http" not in env_local.read_text(encoding="utf-8").replace(" ", ""):
        print("[!] frontend/.env.local does not set VITE_API_MODE=http; the UI will use the mock corpus.")


def _ensure_frontend_installed() -> None:
    if (FRONTEND / "node_modules").is_dir():
        return
    print("[i] installing frontend dependencies (npm ci)... this runs once")
    subprocess.run("npm ci", cwd=FRONTEND, shell=True, check=True)


def _stop(children: list[subprocess.Popen]) -> None:
    for child in children:
        if child.poll() is not None:
            continue
        if os.name == "nt":
            # npm run dev forks cmd -> node; kill the whole tree so Vite frees :5173.
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(child.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            child.terminate()
    for child in children:
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()


def main() -> int:
    if not (BACKEND / "app").is_dir() or not FRONTEND.is_dir():
        print("Run dev.py from the repository root (it must sit next to backend/ and frontend/).")
        return 2

    os.chdir(ROOT)
    _ensure_frontend_uses_http()
    _ensure_frontend_installed()

    sys.path.insert(0, str(BACKEND))
    try:
        from app.config import configure_runtime_database, load_settings
        from app.db.bootstrap import prepare_database
        from app.db.connection import open_connection, resolved_database_url
        from scripts import serve
    except ImportError:
        print("[!] cannot import the backend; run this with the project venv, e.g.")
        print("    .venv/Scripts/python dev.py")
        return 1

    settings = load_settings()
    with open_connection(settings) as startup_conn:
        prepare_database(startup_conn, settings.default_collection)
        dsn = resolved_database_url(settings, startup_conn)
        configure_runtime_database(dsn)
        os.environ["DATABASE_URL"] = dsn

        children: list[subprocess.Popen] = [
            subprocess.Popen([sys.executable, "-m", "scripts.worker"], cwd=BACKEND, env=os.environ),
            subprocess.Popen("npm run dev", cwd=FRONTEND, shell=True),
        ]

        print("[i] worker + Vite dev server started")
        print("[i] API starting on http://127.0.0.1:8000 ; open http://localhost:5173 when Vite is ready")
        print("[i] press Ctrl-C here to stop everything")

        try:
            serve.main()  # blocks; Ctrl-C returns here and the embedded database stops cleanly
        except KeyboardInterrupt:
            pass
        finally:
            _stop(children)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
