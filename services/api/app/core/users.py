from typing import Any
from uuid import UUID

from psycopg import Connection

from .security import AuthUser


def ensure_user(conn: Connection, user: AuthUser) -> UUID:
    row = conn.execute(
        """
        INSERT INTO users (email, display_name, department, attributes)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (email)
        DO UPDATE SET
            display_name = EXCLUDED.display_name,
            department = EXCLUDED.department,
            attributes = EXCLUDED.attributes,
            updated_at = now()
        RETURNING id
        """,
        (user.email, user.display_name, user.department, user.attributes),
    ).fetchone()
    return row["id"]
