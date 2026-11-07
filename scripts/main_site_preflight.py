"""Fail closed before starting a main-site API; never migrate or create a database."""

import hashlib
import os
import sys
from uuid import UUID

import psycopg
from psycopg import sql
from sqlalchemy import make_url


def verify() -> None:
    required = [
        "TASKPILOT_DATABASE_URL",
        "TASKPILOT_EXPECTED_DATABASE",
        "TASKPILOT_EXPECTED_SYSTEM_ID",
        "TASKPILOT_EXPECTED_USER_IDS_SHA256",
        "TASKPILOT_PROTECTED_USER_IDS",
    ]
    if any(not os.environ.get(key) for key in required):
        raise ValueError("database identity guard settings are required")
    url = make_url(os.environ["TASKPILOT_DATABASE_URL"])
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("PostgreSQL is required")
    with psycopg.connect(
        host=url.host,
        port=url.port or 5432,
        dbname=url.database,
        user=url.username,
        password=url.password,
        connect_timeout=5,
        options="-c default_transaction_read_only=on -c statement_timeout=5000",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_database(), system_identifier::text FROM pg_control_system()"
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("database identity unavailable")
            name, identity = row
            if (name, identity) != (os.environ[required[1]], os.environ[required[2]]):
                raise ValueError("database identity mismatch")
            cur.execute("SELECT version_num FROM taskpilot.alembic_version")
            if cur.fetchall() != [("t037_proposal_identity",)]:
                raise ValueError("schema revision mismatch; migrations require separate approval")
            protected = [str(UUID(value)) for value in os.environ[required[4]].split(",")]
            cur.execute(
                "SELECT id::text FROM taskpilot.users WHERE id = ANY(%s::uuid[]) ORDER BY id",
                (protected,),
            )
            ids = "\n".join(row[0] for row in cur.fetchall())
            if not ids or hashlib.sha256(ids.encode()).hexdigest() != os.environ[required[3]]:
                raise ValueError("account identity mismatch")
            for table in ["tasks", "task_runs"]:
                cur.execute(
                    sql.SQL("SELECT count(*) FROM taskpilot.{}").format(sql.Identifier(table))
                )
                count = cur.fetchone()
                if count is None or count[0] < int(os.environ[f"TASKPILOT_MIN_{table.upper()}"]):
                    raise ValueError("historical row count below protected minimum")


if __name__ == "__main__":
    try:
        verify()
    except Exception:
        print(
            "Main-site preflight failed; credentials and database details suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print("Main-site database identity and schema verified; no migrations performed.")
