"""Hold one course's prerequisite rule for curator review.

A hold marks an exact course in an exact Atlas master source as conflicting
with captured registration evidence. Advising reads then report the course's
requirement as held instead of presenting the parsed rule as settled. The hold
never edits graph facts. A verified Atlas import that publishes the cited
capture as the course's authority releases it.

    python -m scripts.requirement_hold --source-id UUID --course "CS 171" \
        --master-hash SHA256 --reason "Atlas lists CS 170; catalog lists MUS 221" \
        --capture PATH --capture-sha256 SHA256 --curator NAME

Published offering evidence may be cited with --evidence-source-id,
--evidence-index-revision and --evidence-capture-set-hash (all three).
"""

from __future__ import annotations

import argparse
import sys
from uuid import UUID

from app.advising.requirement_holds import apply_requirement_hold
from app.config import load_settings
from app.db.connection import open_connection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-id", type=UUID, required=True, help="exact master source")
    parser.add_argument("--course", required=True, help="course code in that master")
    parser.add_argument("--master-hash", required=True, help="SHA-256 of the master file")
    parser.add_argument("--reason", required=True, help="the conflict, in reviewable words")
    parser.add_argument("--capture", required=True, help="path to the saved evidence capture")
    parser.add_argument("--capture-sha256", required=True, help="SHA-256 of that capture")
    parser.add_argument("--curator", required=True, help="who reviewed the conflict")
    parser.add_argument("--evidence-source-id", type=UUID)
    parser.add_argument("--evidence-index-revision", type=int)
    parser.add_argument("--evidence-capture-set-hash")
    args = parser.parse_args(argv)

    settings = load_settings()
    with open_connection(settings) as conn:
        try:
            hold = apply_requirement_hold(
                conn, source_id=args.source_id, course_code=args.course,
                master_file_hash=args.master_hash, conflict_reason=args.reason,
                evidence_capture_sha256=args.capture_sha256,
                evidence_capture_path=args.capture, curator=args.curator,
                evidence_source_id=args.evidence_source_id,
                evidence_index_revision=args.evidence_index_revision,
                evidence_capture_set_hash=args.evidence_capture_set_hash,
            )
        except (TypeError, ValueError) as exc:
            conn.rollback()
            print(f"Hold not recorded: {exc}", file=sys.stderr)
            return 1
        conn.commit()
    print(f"Held {hold['course_code']} ({hold['hold_id']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
