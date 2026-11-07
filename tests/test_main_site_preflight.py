"""A wrong cluster, empty database, or schema cannot silently become the main site."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "main_site_preflight", Path(__file__).parents[1] / "scripts/main_site_preflight.py"
)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


class Cursor:
    def __init__(self, database=("original", "cluster"), revision=None, users=None, counts=48):
        self.database = database
        self.revision = revision if revision is not None else [("t037_proposal_identity",)]
        self.users = users if users is not None else [("identity",)]
        self.counts = counts
        self.query = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query, params=None):
        if not isinstance(query, str):
            query = query.as_string()
        assert query.startswith("SELECT ")
        self.query = query

    def fetchone(self):
        return self.database if "pg_control_system" in self.query else (self.counts,)

    def fetchall(self):
        return self.revision if "alembic_version" in self.query else self.users


class Connection:
    def __init__(self, cursor):
        self.current = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def cursor(self):
        return self.current


@pytest.fixture
def configured(monkeypatch):
    values = {
        "TASKPILOT_DATABASE_URL": "postgresql://example/original",
        "TASKPILOT_EXPECTED_DATABASE": "original",
        "TASKPILOT_EXPECTED_SYSTEM_ID": "cluster",
        "TASKPILOT_EXPECTED_USER_IDS_SHA256": hashlib.sha256(b"identity").hexdigest(),
        "TASKPILOT_PROTECTED_USER_IDS": "00000000-0000-0000-0000-000000000001",
        "TASKPILOT_MIN_TASKS": "46",
        "TASKPILOT_MIN_TASK_RUNS": "48",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def connect(monkeypatch, cursor):
    def checked(**kwargs):
        assert "default_transaction_read_only=on" in kwargs["options"]
        return Connection(cursor)

    monkeypatch.setattr(preflight.psycopg, "connect", checked)


def test_existing_identity_passes_without_writes(configured):
    connect(configured, Cursor())
    preflight.verify()


@pytest.mark.parametrize(
    "cursor",
    [
        Cursor(database=("empty", "cluster")),
        Cursor(database=("original", "wrong")),
        Cursor(revision=[]),
        Cursor(users=[]),
        Cursor(counts=0),
    ],
)
def test_wrong_database_schema_accounts_or_counts_rejected(configured, cursor):
    connect(configured, cursor)
    with pytest.raises(ValueError):
        preflight.verify()


def test_missing_guards_rejected(configured):
    configured.delenv("TASKPILOT_EXPECTED_SYSTEM_ID")
    with pytest.raises(ValueError, match="required"):
        preflight.verify()
