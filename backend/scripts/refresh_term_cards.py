"""Recompute which Atlas result cards search skips.

Imports and offering publication do this themselves. Run it once after migration
082 on a database that already holds offerings:
    python -m scripts.refresh_term_cards --collection NAME
"""

import argparse

from app.atlas.offerings import refresh_term_card_search
from app.config import load_settings
from app.db import queries
from scripts._db import open_connection


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", required=True)
    args = parser.parse_args()
    with open_connection(load_settings()) as conn:
        row = conn.execute(queries.get("get_collection_by_name"),
                           {"name": args.collection}).fetchone()
        if row is None:
            parser.error(f"no collection named {args.collection}")
        hidden = refresh_term_card_search(conn, collection_id=row["collection_id"])
        conn.commit()
    print(f"{hidden} repeated result card(s) hidden from search")


if __name__ == "__main__":
    main()
