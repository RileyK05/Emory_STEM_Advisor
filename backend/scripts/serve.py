"""Run the HTTP API (dev).

Usage (from backend/):
    python -m scripts.serve [--host 127.0.0.1] [--port 8000]

Requires `DATABASE_URL` (or the embedded dev database). Providers are loaded
lazily on first request, so startup is fast.
"""

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        "app.api:app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
