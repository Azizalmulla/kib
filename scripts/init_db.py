#!/usr/bin/env python3
"""Initialize the KIB database schema."""

import os
import re
import sys
from pathlib import Path

import psycopg


def main() -> int:
    db_url = os.environ.get("KIB_DATABASE_URL")
    if not db_url:
        print("ERROR: KIB_DATABASE_URL not set", file=sys.stderr)
        return 1
    strict = os.environ.get("KIB_STRICT_DB_INIT", "").lower() in {"1", "true", "yes"}

    schema_path = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
    if not schema_path.exists():
        print(f"ERROR: Schema file not found: {schema_path}", file=sys.stderr)
        return 1

    schema_sql = schema_path.read_text()
    if os.environ.get("KIB_CREATE_VECTOR_INDEX", "").lower() not in {"1", "true", "yes"}:
        schema_sql = re.sub(
            r"\n-- Vector index for similarity search.*?USING hnsw \(embedding vector_cosine_ops\);\n",
            "\n-- Vector index skipped during deploy. Set KIB_CREATE_VECTOR_INDEX=true to create it.\n",
            schema_sql,
            flags=re.DOTALL,
        )

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
        if strict:
            return 1
        print(
            "WARNING: Database schema initialization failed; continuing because "
            "KIB_STRICT_DB_INIT is not enabled.",
            file=sys.stderr,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
