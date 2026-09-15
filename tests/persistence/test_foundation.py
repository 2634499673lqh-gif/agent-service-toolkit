import asyncio
import sys
import traceback
from datetime import UTC
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError

from core.settings import Settings
from persistence.base import Base
from persistence.engine import normalize_business_database_url
from persistence.migration_filters import include_name
from persistence.models import Organization, utc_now
from persistence.repositories import OrganizationRepository


def _fake_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"USE_FAKE_MODEL": True, "_env_file": None}
    values.update(overrides)
    return Settings(**values)


def test_taskpilot_metadata_is_schema_scoped() -> None:
    assert Base.metadata.schema == "taskpilot"
    assert set(Base.metadata.tables) == {"taskpilot.organizations"}
    assert Organization.__table__.schema == "taskpilot"


def test_organization_defaults_are_application_safe() -> None:
    organization = Organization(name="Acme")
    assert organization.id is None  # SQLAlchemy applies uuid4 on flush, not import/constructor.
    assert organization.name == "Acme"
    assert organization.is_active is None
    assert organization.created_at is None

    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == UTC.utcoffset(now)
    assert Organization.__table__.c.id.type.python_type is UUID


def test_business_url_normalizes_postgresql_and_rejects_sqlite() -> None:
    normalized = normalize_business_database_url(
        SecretStr("postgresql://user:secret@localhost/taskpilot")
    )
    assert normalized.startswith("postgresql+psycopg://user:secret@localhost/taskpilot")
    with pytest.raises(ValueError, match="PostgreSQL"):
        normalize_business_database_url("sqlite+aiosqlite:///business.db")


def test_migration_reflection_filters_only_taskpilot() -> None:
    assert include_name("taskpilot", "schema", {}) is True
    assert include_name("public", "schema", {}) is False
    assert include_name(None, "schema", {}) is False
    assert include_name("organizations", "table", {"schema_name": "taskpilot"}) is True
    assert include_name("checkpoints", "table", {"schema_name": "public"}) is False
    assert include_name("checkpoints", "table", {"schema_name": None}) is False


def test_invalid_business_url_errors_do_not_expose_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "super-secret-password"
    dsn = f"postgresql://user:{secret}@host/taskpilot"

    def raising_parser(value: str) -> object:
        raise ValueError(f"parser saw {value}")

    monkeypatch.setattr("persistence.engine.make_url", raising_parser)
    try:
        normalize_business_database_url(dsn)
    except ValueError:
        trace = traceback.format_exc()
    else:
        pytest.fail("invalid DSN unexpectedly parsed")
    assert secret not in trace
    assert dsn not in trace


def test_settings_rejects_sqlite_business_url_without_affecting_langgraph_default() -> None:
    with pytest.raises(ValidationError, match="TASKPILOT_DATABASE_URL"):
        _fake_settings(TASKPILOT_DATABASE_URL="sqlite+aiosqlite:///business.db")
    settings = _fake_settings()
    assert settings.DATABASE_TYPE.value == "sqlite"
    assert settings.TASKPILOT_DATABASE_URL is None


@pytest.mark.asyncio
async def test_windows_persistence_tests_use_selector_event_loop() -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-only psycopg event-loop compatibility check")
    assert isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)


@pytest.mark.asyncio
async def test_repository_flushes_without_commit() -> None:
    session = Mock()
    session.flush = AsyncMock()
    repository = OrganizationRepository(session)
    organization = Organization(name="Acme")

    assert await repository.add(organization) is organization
    session.add.assert_called_once_with(organization)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_called()
