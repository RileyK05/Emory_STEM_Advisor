"""Re-encrypt stored provider credentials under a new PROFILE_ENCRYPTION_KEY.

Both keys come from the environment, never the command line (shell history):
PROFILE_ENCRYPTION_KEY is the current key and NEW_PROFILE_ENCRYPTION_KEY the new
one. Profile revisions are unchanged, so evaluation approvals stay valid.

    python -m scripts.rotate_profile_key --dry-run   # verify only, writes nothing
    python -m scripts.rotate_profile_key             # rotate

Stop API and worker processes first, then restart them with the new key; see
docs/architecture.md (Persistent backup and recovery). `--generate-key` prints a fresh key.
"""

from __future__ import annotations

import argparse
import os
import sys

from cryptography.fernet import Fernet

from app.config import load_settings
from app.connections.repository import ConnectionProfiles
from app.db.connection import open_connection


def rotate(conn, current_key: str, new_key: str, *, dry_run: bool) -> int:
    if current_key == new_key:
        raise ValueError("NEW_PROFILE_ENCRYPTION_KEY must differ from PROFILE_ENCRYPTION_KEY")
    store = ConnectionProfiles(current_key)
    count = store.rotate_encryption_key(conn, new_key, dry_run=dry_run)
    if not dry_run:
        conn.commit()
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="decrypt and re-encrypt every secret but write nothing")
    parser.add_argument("--generate-key", action="store_true",
                        help="print a new random key and exit")
    args = parser.parse_args(argv)
    if args.generate_key:
        print(Fernet.generate_key().decode())
        return 0
    settings = load_settings()
    new_key = os.environ.get("NEW_PROFILE_ENCRYPTION_KEY")
    if not settings.profile_encryption_key or not new_key:
        print("PROFILE_ENCRYPTION_KEY and NEW_PROFILE_ENCRYPTION_KEY must both be set.",
              file=sys.stderr)
        return 2
    with open_connection(settings) as conn:
        try:
            count = rotate(conn, settings.profile_encryption_key, new_key, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001 - report the type only; never echo key material
            conn.rollback()
            print(f"Key rotation failed ({type(exc).__name__}); nothing was changed.", file=sys.stderr)
            return 1
    verb = "would re-encrypt" if args.dry_run else "re-encrypted"
    print(f"Key rotation: {verb} {count} stored credential(s).")
    if not args.dry_run:
        print("Now set PROFILE_ENCRYPTION_KEY to the new key everywhere and restart API and workers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
