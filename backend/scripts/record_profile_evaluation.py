"""Bind an evaluation result to the current revision of one connection profile.

Evaluated routing (ROUTING_ENABLED) only selects a profile whose current
revision has passed the configured suite (ROUTING_EVAL_SUITE_HASH). Run the
evaluation suite first, then record its outcome:

    python -m scripts.record_profile_evaluation PROFILE_ID --suite-hash HASH \
        --status passed --metrics results.json

The result is bound to the profile's current revision, provider and model;
editing the profile later makes it unevaluated again. This script never runs a
model and never marks a profile as passed by itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

from app.config import load_settings
from app.connections.models import EvaluationEvidence
from app.connections.repository import ConnectionProfiles
from app.db.connection import open_connection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("profile_id", type=UUID)
    parser.add_argument("--suite-hash", required=True, help="SHA-256 of the evaluation suite")
    parser.add_argument("--status", required=True, choices=("passed", "failed"))
    parser.add_argument("--metrics", type=Path, required=True,
                        help="JSON object of metrics produced by the evaluation run")
    args = parser.parse_args(argv)
    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))
    if not isinstance(metrics, dict):
        parser.error("--metrics must contain a JSON object")

    settings = load_settings()
    store = ConnectionProfiles(settings.profile_encryption_key)
    with open_connection(settings) as conn:
        profile = store.get(conn, args.profile_id)
        if profile is None:
            print(f"No connection profile {args.profile_id}.", file=sys.stderr)
            return 1
        store.record_evaluation(conn, EvaluationEvidence(
            evaluation_id=uuid4(), profile_id=profile.profile_id,
            profile_revision=profile.revision, provider=profile.provider,
            model_id=profile.model_id, suite_hash=args.suite_hash,
            status=args.status, metrics=metrics,
        ))
        conn.commit()
    print(f"Recorded {args.status} for {profile.name} revision {profile.revision} "
          f"({profile.provider}/{profile.model_id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
