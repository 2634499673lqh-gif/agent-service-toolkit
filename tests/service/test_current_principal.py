"""T024 tests for the server-derived request principal and bearer dependency.

These exercise ``AuthService.authenticate`` (the boundary the FastAPI
dependency delegates to) plus the pure principal builder, using repository
doubles so ordinary runs need no database.  The FastAPI dependency itself is
covered end to end against PostgreSQL in the persistence integration suite.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import Depends

from persistence.models import AuthSession, Membership, Organization, Role, User
from persistence.passwords import hash_password
from persistence.tokens import hash_token
from service import auth_dependency
from service.auth_dependency import (
    AUTHENTICATION_ERROR_DETAIL,
    _extract_bearer_token,
    require_principal,
)
from service.session import (
    AuthService,
    CurrentPrincipal,
    LoginError,
    build_principal,
)

PASSWORD = "correct horse battery staple"
_FIXED_NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)
_TOKEN = "opaque-session-token-for-testing"


class _FakeSession:
    """Minimal AsyncSession stand-in; authentication must never write."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.commit_calls = 0
        self.rollback_calls = 0

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:  # pragma: no cover - must never be called
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


class _FakeUserRepository:
    def __init__(self, users: list[User]) -> None:
        self.users = users

    async def get(self, user_id: UUID) -> User | None:
        return next((item for item in self.users if item.id == user_id), None)

    async def get_by_email(self, email: str) -> User | None:
        from persistence.identity import canonicalize_email

        _, normalized = canonicalize_email(email)
        return next((item for item in self.users if item.normalized_email == normalized), None)

    async def add(self, user: User) -> User:  # pragma: no cover - unused here
        self.users.append(user)
        return user


class _FakeMembershipRepository:
    def __init__(self, memberships: list[Membership]) -> None:
        self.memberships = memberships

    async def get(self, membership_id: UUID) -> Membership | None:
        return next((item for item in self.memberships if item.id == membership_id), None)

    async def list_for_user(self, user_id: UUID) -> list[Membership]:
        return [item for item in self.memberships if item.user_id == user_id]

    async def list_eligible_for_user(self, user_id: UUID) -> list[Membership]:
        return [item for item in self.memberships if item.user_id == user_id]

    async def add(self, membership: Membership) -> Membership:  # pragma: no cover
        self.memberships.append(membership)
        return membership


class _FakeOrganizationRepository:
    def __init__(self, organizations: list[Organization]) -> None:
        self.organizations = organizations

    async def get(self, organization_id: UUID) -> Organization | None:
        return next((item for item in self.organizations if item.id == organization_id), None)

    async def get_by_name(self, name: str) -> Organization | None:
        return next((item for item in self.organizations if item.name == name), None)


class _FakeAuthSessionRepository:
    def __init__(self, sessions: list[AuthSession]) -> None:
        self.sessions = sessions

    async def get(self, session_id: UUID) -> AuthSession | None:
        return next((item for item in self.sessions if item.id == session_id), None)

    async def get_by_token_hash(self, token_hash: str) -> AuthSession | None:
        return next((item for item in self.sessions if item.token_hash == token_hash), None)


def _user(*, is_active: bool = True) -> User:
    user = User(email="principal@example.com", password_hash=hash_password(PASSWORD))
    user.id = uuid4()
    user.is_active = is_active
    return user


def _organization(*, is_active: bool = True) -> Organization:
    organization = Organization(name="Principal Org")
    organization.id = uuid4()
    organization.is_active = is_active
    return organization


def _membership(
    user: User,
    organization: Organization,
    *,
    role: Role = Role.OWNER,
    is_active: bool = True,
) -> Membership:
    membership = Membership(
        user_id=user.id,
        organization_id=organization.id,
        role=role,
        is_active=is_active,
    )
    membership.id = uuid4()
    return membership


def _auth_session(
    user: User,
    membership: Membership,
    *,
    token: str = _TOKEN,
    revoked: bool = False,
    expires_in: timedelta = timedelta(hours=24),
    user_id: UUID | None = None,
    membership_id: UUID | None = None,
) -> AuthSession:
    auth_session = AuthSession(
        user_id=user_id or user.id,
        membership_id=membership_id or membership.id,
        token_hash=hash_token(token),
        expires_at=_FIXED_NOW + expires_in,
        revoked_at=_FIXED_NOW - timedelta(minutes=1) if revoked else None,
    )
    auth_session.id = uuid4()
    auth_session.created_at = _FIXED_NOW - timedelta(minutes=5)
    auth_session.updated_at = _FIXED_NOW - timedelta(minutes=5)
    return auth_session


def _service(
    *,
    users: list[User],
    memberships: list[Membership],
    organizations: list[Organization],
    sessions: list[AuthSession],
) -> tuple[AuthService, _FakeSession]:
    session = _FakeSession()
    service = AuthService(
        session,  # type: ignore[arg-type]
        now=lambda: _FIXED_NOW,
        user_repository=_FakeUserRepository(users),  # type: ignore[arg-type]
        membership_repository=_FakeMembershipRepository(memberships),  # type: ignore[arg-type]
        organization_repository=_FakeOrganizationRepository(organizations),  # type: ignore[arg-type]
        auth_session_repository=_FakeAuthSessionRepository(sessions),  # type: ignore[arg-type]
    )
    return service, session


def _valid_stack() -> tuple[User, Organization, Membership, AuthSession]:
    user = _user()
    organization = _organization()
    membership = _membership(user, organization)
    return user, organization, membership, _auth_session(user, membership)


# --------------------------------------------------------------------------- #
# Value object
# --------------------------------------------------------------------------- #


def test_principal_fields_come_from_the_verified_rows() -> None:
    user, organization, membership, auth_session = _valid_stack()

    principal = build_principal(
        auth_session=auth_session,
        user=user,
        membership=membership,
        organization=organization,
    )

    assert isinstance(principal, CurrentPrincipal)
    assert principal.user_id == user.id
    assert principal.membership_id == membership.id
    assert principal.organization_id == organization.id
    assert principal.role is Role.OWNER
    assert principal.session_id == auth_session.id


def test_principal_is_immutable() -> None:
    user, organization, membership, auth_session = _valid_stack()
    principal = build_principal(
        auth_session=auth_session, user=user, membership=membership, organization=organization
    )

    for field, value in (
        ("user_id", uuid4()),
        ("membership_id", uuid4()),
        ("organization_id", uuid4()),
        ("role", Role.MEMBER),
        ("session_id", uuid4()),
    ):
        with pytest.raises(Exception):
            setattr(principal, field, value)


def test_principal_holds_no_orm_session_or_credential_material() -> None:
    user, organization, membership, auth_session = _valid_stack()
    principal = build_principal(
        auth_session=auth_session, user=user, membership=membership, organization=organization
    )

    assert set(principal.__slots__) == {
        "user_id",
        "membership_id",
        "organization_id",
        "role",
        "session_id",
    }
    rendered = f"{principal!r} {principal}"
    for secret in (_TOKEN, auth_session.token_hash, PASSWORD, user.password_hash):
        assert secret not in rendered
    for forbidden in ("token", "AsyncSession", "Session", "Membership(", "User("):
        assert forbidden not in rendered


@pytest.mark.parametrize(
    "mismatch",
    ["user", "membership_owner", "membership_id", "organization"],
)
def test_principal_builder_fails_closed_on_inconsistent_rows(mismatch: str) -> None:
    user, organization, membership, auth_session = _valid_stack()

    if mismatch == "user":
        user.id = uuid4()
    elif mismatch == "membership_owner":
        membership.user_id = uuid4()
    elif mismatch == "membership_id":
        auth_session.membership_id = uuid4()
    else:
        membership.organization_id = uuid4()

    with pytest.raises(LoginError):
        build_principal(
            auth_session=auth_session,
            user=user,
            membership=membership,
            organization=organization,
        )


def test_principal_builder_error_is_the_generic_credential_message() -> None:
    user, organization, membership, auth_session = _valid_stack()
    membership.user_id = uuid4()

    with pytest.raises(LoginError) as raised:
        build_principal(
            auth_session=auth_session,
            user=user,
            membership=membership,
            organization=organization,
        )

    assert str(raised.value) == "Invalid credentials"


# --------------------------------------------------------------------------- #
# Bearer credential extraction
# --------------------------------------------------------------------------- #


def test_bearer_token_extraction_accepts_only_a_bearer_header() -> None:
    assert _extract_bearer_token(f"Bearer {_TOKEN}") == _TOKEN
    assert _extract_bearer_token(f"bearer {_TOKEN}") == _TOKEN
    for value in (
        None,
        "",
        _TOKEN,
        "Basic dXNlcjpwYXNz",
        "Bearer",
        "Bearer ",
        f"Token {_TOKEN}",
        f"Bearer {_TOKEN} extra",
    ):
        assert _extract_bearer_token(value) is None


# --------------------------------------------------------------------------- #
# Request-scoped session lifecycle
# --------------------------------------------------------------------------- #


class _TrackedSession:
    """AsyncSession double that counts lifecycle calls."""

    def __init__(self, *, fail_rollback: bool = False) -> None:
        self.close_calls = 0
        self.rollback_calls = 0
        self._fail_rollback = fail_rollback

    async def close(self) -> None:
        self.close_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1
        if self._fail_rollback:
            raise RuntimeError("rollback unavailable")


def _patch_authenticate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: CurrentPrincipal | None = None,
    error: BaseException | None = None,
) -> None:
    async def fake_authenticate(self: AuthService, raw_token: str) -> CurrentPrincipal | None:
        del self, raw_token
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(AuthService, "authenticate", fake_authenticate)


def _principal() -> CurrentPrincipal:
    user, organization, membership, auth_session = _valid_stack()
    return CurrentPrincipal(
        user_id=user.id,
        membership_id=membership.id,
        organization_id=organization.id,
        role=membership.role,
        session_id=auth_session.id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Basic dXNlcjpwYXNz",
        "Bearer",
        "Bearer ",
        "Token abcdef",
        f"Bearer {_TOKEN} extra",
    ],
)
async def test_malformed_credentials_still_close_the_session(
    monkeypatch: pytest.MonkeyPatch, authorization: str | None
) -> None:
    session = _TrackedSession()

    with pytest.raises(Exception) as raised:
        await require_principal(session, authorization)  # type: ignore[arg-type]

    assert getattr(raised.value, "status_code", None) == 401
    assert raised.value.detail == AUTHENTICATION_ERROR_DETAIL  # type: ignore[attr-defined]
    # The blocker: cleanup must run even though the credential was rejected
    # before any authentication call.
    assert session.close_calls == 1
    assert session.rollback_calls == 0


@pytest.mark.asyncio
async def test_unknown_or_invalid_token_closes_the_session_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _TrackedSession()
    _patch_authenticate(monkeypatch, result=None)

    with pytest.raises(Exception) as raised:
        await require_principal(session, f"Bearer {_TOKEN}")  # type: ignore[arg-type]

    assert getattr(raised.value, "status_code", None) == 401
    assert session.close_calls == 1
    assert session.rollback_calls == 0


@pytest.mark.asyncio
async def test_successful_authentication_closes_the_session_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _TrackedSession()
    expected = _principal()
    _patch_authenticate(monkeypatch, result=expected)

    principal = await require_principal(session, f"Bearer {_TOKEN}")  # type: ignore[arg-type]

    assert principal is expected
    assert session.close_calls == 1
    assert session.rollback_calls == 0


@pytest.mark.asyncio
async def test_unexpected_failure_rolls_back_then_closes_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _TrackedSession()
    _patch_authenticate(monkeypatch, error=RuntimeError("database exploded"))

    with pytest.raises(RuntimeError, match="database exploded"):
        await require_principal(session, f"Bearer {_TOKEN}")  # type: ignore[arg-type]

    assert session.rollback_calls == 1
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_rollback_failure_still_closes_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _TrackedSession(fail_rollback=True)
    _patch_authenticate(monkeypatch, error=RuntimeError("database exploded"))

    with pytest.raises(RuntimeError, match="database exploded"):
        await require_principal(session, f"Bearer {_TOKEN}")  # type: ignore[arg-type]

    # A broken rollback must not prevent cleanup or replace the original error.
    assert session.rollback_calls == 1
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_dependency_level_missing_credential_closes_the_injected_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the real dependency chain, not just a helper function."""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    sessions: list[_TrackedSession] = []

    class _Factory:
        def __call__(self) -> _TrackedSession:
            session = _TrackedSession()
            sessions.append(session)
            return session

    factory = _Factory()
    app = FastAPI()

    @app.get("/protected")
    async def protected(
        principal: Annotated[CurrentPrincipal, Depends(require_principal)],
    ) -> dict[str, str]:
        del principal
        return {"ok": "true"}

    app.dependency_overrides[auth_dependency.get_session_factory] = lambda: factory

    with TestClient(app) as client:
        missing = client.get("/protected")
        malformed = client.get("/protected", headers={"Authorization": "Bearer "})

    assert missing.status_code == 401
    assert malformed.status_code == 401
    # Both rejected requests still acquired and released their session.
    assert len(sessions) == 2
    assert [session.close_calls for session in sessions] == [1, 1]
    assert all(session.rollback_calls == 0 for session in sessions)


# --------------------------------------------------------------------------- #
# Authentication successes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_valid_session_yields_principal_without_writing() -> None:
    user, organization, membership, auth_session = _valid_stack()
    service, session = _service(
        users=[user],
        memberships=[membership],
        organizations=[organization],
        sessions=[auth_session],
    )

    principal = await service.authenticate(_TOKEN)

    assert principal is not None
    assert principal.user_id == user.id
    assert principal.membership_id == membership.id
    assert principal.organization_id == organization.id
    assert principal.session_id == auth_session.id
    # Authentication is a read path.
    assert session.added == []
    assert session.commit_calls == 0
    assert session.rollback_calls == 0


@pytest.mark.asyncio
async def test_authentication_is_idempotent_for_the_same_token() -> None:
    user, organization, membership, auth_session = _valid_stack()
    service, session = _service(
        users=[user],
        memberships=[membership],
        organizations=[organization],
        sessions=[auth_session],
    )

    first = await service.authenticate(_TOKEN)
    second = await service.authenticate(_TOKEN)

    assert first == second
    assert session.added == []
    assert session.commit_calls == 0


@pytest.mark.asyncio
async def test_other_users_session_never_yields_a_principal() -> None:
    owner = _user()
    stranger = _user()
    organization = _organization()
    stranger_membership = _membership(stranger, organization)
    # A session that claims the owner's identity but points at a foreign membership.
    forged = _auth_session(owner, stranger_membership)
    service, _ = _service(
        users=[owner, stranger],
        memberships=[stranger_membership],
        organizations=[organization],
        sessions=[forged],
    )

    assert await service.authenticate(_TOKEN) is None


# --------------------------------------------------------------------------- #
# Authentication failures
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "unknown-token", _TOKEN.upper()])
async def test_missing_malformed_or_unknown_token_has_no_principal(token: str) -> None:
    user, organization, membership, auth_session = _valid_stack()
    service, _ = _service(
        users=[user],
        memberships=[membership],
        organizations=[organization],
        sessions=[auth_session],
    )

    assert await service.authenticate(token) is None


@pytest.mark.asyncio
async def test_revoked_and_expired_sessions_have_no_principal() -> None:
    user = _user()
    organization = _organization()
    membership = _membership(user, organization)
    revoked = _auth_session(user, membership, token="revoked-token", revoked=True)
    expired = _auth_session(user, membership, token="expired-token", expires_in=timedelta(hours=-1))
    service, _ = _service(
        users=[user],
        memberships=[membership],
        organizations=[organization],
        sessions=[revoked, expired],
    )

    assert await service.authenticate("revoked-token") is None
    assert await service.authenticate("expired-token") is None


@pytest.mark.asyncio
async def test_inactive_user_membership_or_organization_has_no_principal() -> None:
    active_user = _user()
    inactive_user = _user(is_active=False)
    organization = _organization()
    inactive_organization = _organization(is_active=False)
    active_membership = _membership(active_user, organization)
    inactive_membership = _membership(active_user, organization, is_active=False)
    inactive_user_membership = _membership(inactive_user, organization)
    inactive_organization_membership = _membership(active_user, inactive_organization)

    users = [active_user, inactive_user]
    memberships = [
        active_membership,
        inactive_membership,
        inactive_user_membership,
        inactive_organization_membership,
    ]
    organizations = [organization, inactive_organization]
    sessions = [
        _auth_session(inactive_user, inactive_user_membership, token="inactive-user"),
        _auth_session(active_user, inactive_membership, token="inactive-membership"),
        _auth_session(active_user, inactive_organization_membership, token="inactive-organization"),
    ]
    service, _ = _service(
        users=users, memberships=memberships, organizations=organizations, sessions=sessions
    )

    assert await service.authenticate("inactive-user") is None
    assert await service.authenticate("inactive-membership") is None
    assert await service.authenticate("inactive-organization") is None


@pytest.mark.asyncio
async def test_state_becoming_inactive_after_issuance_is_rejected_next_request() -> None:
    user, organization, membership, auth_session = _valid_stack()
    service, _ = _service(
        users=[user],
        memberships=[membership],
        organizations=[organization],
        sessions=[auth_session],
    )

    assert await service.authenticate(_TOKEN) is not None

    # The session row is untouched; only the current database state changes.
    user.is_active = False
    assert await service.authenticate(_TOKEN) is None
    user.is_active = True

    membership.is_active = False
    assert await service.authenticate(_TOKEN) is None
    membership.is_active = True

    organization.is_active = False
    assert await service.authenticate(_TOKEN) is None


@pytest.mark.asyncio
async def test_missing_membership_or_organization_row_has_no_principal() -> None:
    user, organization, membership, auth_session = _valid_stack()
    dangling_membership_session = _auth_session(
        user, membership, token="dangling-membership", membership_id=uuid4()
    )
    dangling_organization_membership = _membership(user, organization)
    dangling_organization_membership.organization_id = uuid4()
    dangling_organization_session = _auth_session(
        user, dangling_organization_membership, token="dangling-organization"
    )
    service, _ = _service(
        users=[user],
        memberships=[membership, dangling_organization_membership],
        organizations=[organization],
        sessions=[auth_session, dangling_membership_session, dangling_organization_session],
    )

    assert await service.authenticate("dangling-membership") is None
    assert await service.authenticate("dangling-organization") is None


@pytest.mark.asyncio
async def test_role_change_after_issuance_is_reflected_on_the_next_request() -> None:
    user, organization, membership, auth_session = _valid_stack()
    service, _ = _service(
        users=[user],
        memberships=[membership],
        organizations=[organization],
        sessions=[auth_session],
    )

    first = await service.authenticate(_TOKEN)
    assert first is not None and first.role is Role.OWNER

    # The membership row is the single source of truth for the role.
    membership.role = Role.MEMBER
    second = await service.authenticate(_TOKEN)

    assert second is not None
    assert second.role is Role.MEMBER
    assert second.membership_id == membership.id


@pytest.mark.asyncio
async def test_authenticated_principal_never_leaks_credential_material() -> None:
    user, organization, membership, auth_session = _valid_stack()
    service, _ = _service(
        users=[user],
        memberships=[membership],
        organizations=[organization],
        sessions=[auth_session],
    )

    principal = await service.authenticate(_TOKEN)

    assert principal is not None
    rendered = f"{principal!r} {principal}"
    for secret in (_TOKEN, auth_session.token_hash, PASSWORD, user.password_hash):
        assert secret not in rendered
