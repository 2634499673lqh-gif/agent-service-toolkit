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
from persistence.identity import canonicalize_email
from persistence.migration_filters import include_name
from persistence.models import Organization, User, utc_now
from persistence.repositories import OrganizationRepository, UserRepository


def _fake_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"USE_FAKE_MODEL": True, "_env_file": None}
    values.update(overrides)
    return Settings(**values)


def test_taskpilot_metadata_is_schema_scoped() -> None:
    assert Base.metadata.schema == "taskpilot"
    assert set(Base.metadata.tables) == {"taskpilot.organizations", "taskpilot.users"}
    assert Organization.__table__.schema == "taskpilot"
    assert User.__table__.schema == "taskpilot"


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


def test_email_canonicalization_preserves_display_and_uses_casefold() -> None:
    display_email, normalized_email = canonicalize_email("\u00a0Straße@Example.COM\u2003")

    assert display_email == "Straße@Example.COM"
    assert normalized_email == "strasse@example.com"
    assert display_email.lower() != normalized_email


def test_email_canonicalization_uses_real_unicode_casefold() -> None:
    # ``lower()`` alone cannot produce these identities, so this pins the
    # contract to ``str.casefold`` instead of PostgreSQL ``lower()``/collation.
    assert canonicalize_email("Straße@Example.COM") == (
        "Straße@Example.COM",
        "strasse@example.com",
    )
    assert canonicalize_email("İstanbul@Example.COM") == (
        "İstanbul@Example.COM",
        "i\u0307stanbul@example.com",
    )
    assert canonicalize_email("GREEK\u03a3@Example.COM")[1] == "greek\u03c3@example.com"
    assert (
        canonicalize_email("GREEK\u03c2@Example.COM")[1]
        == canonicalize_email("GREEK\u03a3@Example.COM")[1]
    )


def test_user_derives_normalized_email_and_hides_password_hash() -> None:
    password_hash = "$argon2id$v=19$test-only-opaque-value"
    user = User(email="  Alice@Example.COM  ", password_hash=password_hash)

    assert user.id is None
    assert user.email == "Alice@Example.COM"
    assert user.normalized_email == "alice@example.com"
    assert user.is_active is None
    assert user.created_at is None
    assert password_hash not in repr(user)
    assert "password_hash" not in repr(user)
    assert password_hash not in str(user)
    assert User.__table__.c.id.type.python_type is UUID
    assert User.__table__.c.created_at.type.timezone is True
    assert User.__table__.c.updated_at.type.timezone is True

    user.email = " Bob@Example.COM "
    assert user.email == "Bob@Example.COM"
    assert user.normalized_email == "bob@example.com"


def test_empty_email_is_rejected_by_the_identity_helper_and_user_model() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        canonicalize_email("\u2003\t\n")
    with pytest.raises(ValueError, match="must not be blank"):
        User(email=" \u00a0 ", password_hash="opaque-test-hash")


def test_user_model_rejects_non_string_email() -> None:
    with pytest.raises(TypeError, match="must be a string"):
        User(email=None, password_hash="opaque-test-hash")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="derived from email"):
        User(  # type: ignore[call-arg]
            email="user@example.com",
            normalized_email="forged@example.com",
            password_hash="opaque-test-hash",
        )


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

    user_session = Mock()
    user_session.flush = AsyncMock()
    user_repository = UserRepository(user_session)
    user = User(email="user@example.com", password_hash="opaque-test-hash")

    assert await user_repository.add(user) is user
    user_session.add.assert_called_once_with(user)
    user_session.flush.assert_awaited_once_with()
    user_session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_repository_email_coercion_reuses_the_identity_contract() -> None:
    session = Mock()
    session.scalar = AsyncMock(return_value=None)
    repository = UserRepository(session)

    assert await repository.get_by_email("  Mixed@Example.COM ") is None
    statement = session.scalar.await_args.args[0]
    assert statement.compile().params["normalized_email_1"] == "mixed@example.com"

    with pytest.raises(ValueError, match="must not be blank"):
        await repository.get_by_email("   ")
