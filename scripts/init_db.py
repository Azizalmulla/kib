#!/usr/bin/env python3
"""Initialize the KIB database schema."""

import os
import sys
from pathlib import Path

import psycopg


def main() -> int:
    db_url = os.environ.get("KIB_DATABASE_URL")
    if not db_url:
        print("ERROR: KIB_DATABASE_URL not set", file=sys.stderr)
        return 1

    schema_path = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
    if not schema_path.exists():
        print(f"ERROR: Schema file not found: {schema_path}", file=sys.stderr)
        return 1

    schema_sql = schema_path.read_text()

    print(f"Connecting to database...")
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(schema_sql)
            conn.commit()
        print("Database schema initialized successfully.")
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
