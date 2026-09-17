"""T023 authentication contract tests using in-memory repository fakes.

These tests cover password hashing, opaque token handling, session lifecycle
rules, and the generic login failure contract without requiring PostgreSQL.
Database-level persistence and migration behavior lives in
``tests/persistence/test_postgres_integration.py``.
"""

import base64
import logging
import string
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import NamedTuple, cast
from uuid import UUID, uuid4

import pytest

from persistence import (
    AuthSessionRepository,
    MembershipRepository,
    OrganizationRepository,
    UserRepository,
)
from persistence.models import AuthSession, Membership, Organization, Role, User
from persistence.passwords import hash_password, verify_password
from persistence.tokens import TOKEN_BYTES, generate_token, hash_token
from service.bootstrap import (
    PROMPT_LABEL_CONFIRM_PASSWORD,
    PROMPT_LABEL_EMAIL,
    PROMPT_LABEL_ORGANIZATION,
    PROMPT_LABEL_PASSWORD,
    BootstrapError,
    BootstrapOutcome,
    BootstrapService,
)
from service.session import (
    ORGANIZATION_SELECTION_REQUIRED,
    SESSION_TTL,
    AuthenticatedSession,
    AuthService,
    LoginError,
    LoginFailure,
    OrganizationSelectionRequired,
    utc_now,
)

_BASE64URL_ALPHABET = set(string.ascii_letters + string.digits + "-_")
_FIXED_NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)


class _FakeSession:
    """Minimal AsyncSession stand-in that records writes without a database."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_calls = 0
        self.commit_calls = 0

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        self.flush_calls += 1
        for instance in self.added:
            if isinstance(instance, AuthSession) and instance.id is None:
                instance.id = uuid4()

    async def commit(self) -> None:  # pragma: no cover - must never be called
        self.commit_calls += 1


class _FakeUserRepository:
    def __init__(self, users: list[User] | None = None) -> None:
        self.users = users or []
        self.get_by_email_calls = 0

    async def get_by_email(self, email: str) -> User | None:
        from persistence.identity import canonicalize_email

        self.get_by_email_calls += 1
        _, normalized = canonicalize_email(email)
        return next((user for user in self.users if user.normalized_email == normalized), None)


class _FakeMembershipRepository:
    def __init__(
        self,
        memberships: list[Membership] | None = None,
        *,
        organizations: dict[UUID, Organization] | None = None,
    ) -> None:
        self.memberships = memberships or []
        self.organizations = organizations or {}
        self.eligible_calls = 0

    async def list_for_user(self, user_id: UUID) -> list[Membership]:
        return [item for item in self.memberships if item.user_id == user_id]

    async def list_eligible_for_user(self, user_id: UUID) -> list[Membership]:
        self.eligible_calls += 1
        eligible: list[Membership] = []
        for membership in self.memberships:
            if membership.user_id != user_id or not membership.is_active:
                continue
            organization = self.organizations.get(membership.organization_id)
            if organization is None:
                # No organization registered: active unless a test says otherwise.
                organization = _organization(membership.organization_id)
            if organization.is_active:
                eligible.append(membership)
        return eligible


class _FakeAuthSessionRepository:
    def __init__(self) -> None:
        self.sessions: list[AuthSession] = []
        self.revoked: list[AuthSession] = []
        self.deleted_expired: list[datetime] = []

    async def add(self, auth_session: AuthSession) -> AuthSession:
        if auth_session.id is None:
            auth_session.id = uuid4()
        self.sessions.append(auth_session)
        return auth_session

    async def get(self, session_id: UUID) -> AuthSession | None:
        return next((item for item in self.sessions if item.id == session_id), None)

    async def get_by_token_hash(self, token_hash: str) -> AuthSession | None:
        return next((item for item in self.sessions if item.token_hash == token_hash), None)

    async def revoke(self, auth_session: AuthSession, revoked_at: datetime) -> AuthSession:
        auth_session.revoked_at = revoked_at
        self.revoked.append(auth_session)
        return auth_session

    async def delete_expired(self, now: datetime) -> int:
        self.deleted_expired.append(now)
        expired = [item for item in self.sessions if item.expires_at < now]
        for item in expired:
            self.sessions.remove(item)
        return len(expired)


def _organization(organization_id: UUID, *, is_active: bool = True) -> Organization:
    organization = Organization(name="Fake Org")
    organization.id = organization_id
    organization.is_active = is_active
    return organization


def _user(email: str = "owner@example.com", *, is_active: bool = True) -> User:
    user = User(email=email, password_hash=hash_password("correct horse battery staple"))
    user.id = uuid4()
    user.is_active = is_active
    return user


def _membership(
    user: User,
    *,
    organization_id: UUID | None = None,
    is_active: bool = True,
    role: Role = Role.OWNER,
) -> Membership:
    membership = Membership(
        user_id=user.id,
        organization_id=organization_id or uuid4(),
        role=role,
        is_active=is_active,
    )
    membership.id = uuid4()
    return membership


class _ServiceHarness(NamedTuple):
    service: AuthService
    auth_sessions: _FakeAuthSessionRepository
    users: _FakeUserRepository
    memberships: _FakeMembershipRepository


class _UnscopedMembershipRepository:
    """Repository double that returns rows without a user predicate.

    Used only to prove the service-level ownership invariant: a regression,
    adapter bug, or future refactor that drops the ``user_id`` scope must not let
    a foreign membership reach selection, metadata, or session issuance.
    """

    def __init__(self, rows: list[Membership]) -> None:
        self.rows = rows
        self.eligible_calls = 0

    async def list_for_user(self, user_id: UUID) -> list[Membership]:
        del user_id
        return list(self.rows)

    async def list_eligible_for_user(self, user_id: UUID) -> list[Membership]:
        del user_id
        self.eligible_calls += 1
        return list(self.rows)


def _service(
    *,
    users: list[User] | None = None,
    memberships: list[Membership] | None = None,
    organizations: dict[UUID, Organization] | None = None,
    now: datetime = _FIXED_NOW,
) -> _ServiceHarness:
    session = _FakeSession()
    auth_sessions = _FakeAuthSessionRepository()
    user_repository = _FakeUserRepository(users)
    membership_repository = _FakeMembershipRepository(memberships, organizations=organizations)
    service = AuthService(
        cast("object", session),  # type: ignore[arg-type]
        now=lambda: now,
        user_repository=cast(UserRepository, user_repository),
        membership_repository=cast(MembershipRepository, membership_repository),
        auth_session_repository=cast(AuthSessionRepository, auth_sessions),
    )
    return _ServiceHarness(service, auth_sessions, user_repository, membership_repository)


def _service_with_unscoped_repository(
    *,
    users: list[User],
    rows: list[Membership],
    token_factory: Callable[[], str] = generate_token,
) -> tuple[AuthService, _FakeAuthSessionRepository, _FakeSession, _UnscopedMembershipRepository]:
    """Build a service whose membership repository ignores user scope."""

    session = _FakeSession()
    auth_sessions = _FakeAuthSessionRepository()
    memberships = _UnscopedMembershipRepository(rows)
    service = AuthService(
        cast("object", session),  # type: ignore[arg-type]
        now=lambda: _FIXED_NOW,
        token_factory=token_factory,
        user_repository=cast(UserRepository, _FakeUserRepository(users)),
        membership_repository=cast(MembershipRepository, memberships),
        auth_session_repository=cast(AuthSessionRepository, auth_sessions),
    )
    return service, auth_sessions, session, memberships


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #


def test_password_hash_uses_argon2id_and_verifies() -> None:
    password = "correct horse battery staple"
    password_hash = hash_password(password)

    assert password_hash.startswith("$argon2id$")
    assert password not in password_hash
    assert verify_password(password, password_hash) is True
    assert verify_password("wrong password", password_hash) is False


def test_password_hash_is_salted_per_call() -> None:
    assert hash_password("same-password") != hash_password("same-password")


def test_verify_password_returns_false_for_malformed_hash_or_input() -> None:
    assert verify_password("secret", "") is False
    assert verify_password("secret", "not-a-hash") is False
    assert verify_password("secret", "$argon2id$broken") is False
    assert verify_password("", hash_password("secret")) is False


def test_password_hash_rejects_blank_or_non_string_password() -> None:
    with pytest.raises(ValueError):
        hash_password("")
    with pytest.raises(TypeError):
        hash_password(b"bytes")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Opaque tokens
# --------------------------------------------------------------------------- #


def test_generated_token_is_base64url_from_32_random_bytes() -> None:
    token = generate_token()

    assert isinstance(token, str)
    assert "=" not in token
    assert set(token) <= _BASE64URL_ALPHABET
    # 32 bytes encode to 43 unpadded base64url characters.
    assert len(token) == 43
    assert TOKEN_BYTES >= 32
    assert len(base64.urlsafe_b64decode(token + "=")) == TOKEN_BYTES


def test_generated_tokens_are_unique() -> None:
    tokens = {generate_token() for _ in range(64)}
    assert len(tokens) == 64


def test_token_hash_is_deterministic_sha256_hex() -> None:
    token = generate_token()
    digest = hash_token(token)

    assert len(digest) == 64
    assert digest == hash_token(token)
    assert digest != hash_token(generate_token())
    assert token not in digest
    with pytest.raises(TypeError):
        hash_token(b"bytes")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Login / issuance
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_login_issues_a_24_hour_session_bound_to_the_membership() -> None:
    user = _user()
    membership = _membership(user)
    harness = _service(users=[user], memberships=[membership])
    service = harness.service
    auth_sessions = harness.auth_sessions

    result = await service.login("  Owner@Example.COM ", "correct horse battery staple")
    assert isinstance(result, AuthenticatedSession)

    assert result.token and result.token not in repr(result)
    assert result.session_id == auth_sessions.sessions[0].id
    assert result.user_id == user.id
    assert result.membership_id == membership.id
    assert result.organization_id == membership.organization_id
    assert result.expires_at == _FIXED_NOW + SESSION_TTL
    assert SESSION_TTL == timedelta(hours=24)
    assert result.expires_at.utcoffset() == timedelta(0)

    stored = auth_sessions.sessions[0]
    assert stored.token_hash == hash_token(result.token)
    # The raw token is never persisted on the row.
    assert result.token not in vars(stored).values()
    assert "token_hash" not in repr(stored)


@pytest.mark.asyncio
@pytest.mark.parametrize("email", ["unknown@example.com", "OWNER@example.com "])
async def test_login_failures_share_one_generic_error(email: str) -> None:
    user = _user()
    membership = _membership(user)
    service = _service(users=[user], memberships=[membership]).service

    with pytest.raises(LoginError) as raised:
        await service.login(email, "wrong password")
    assert str(raised.value) == "Invalid credentials"


@pytest.mark.asyncio
async def test_unknown_email_and_wrong_password_are_indistinguishable() -> None:
    user = _user()
    service = _service(users=[user], memberships=[_membership(user)]).service

    messages = []
    for email, password in (
        ("unknown@example.com", "correct horse battery staple"),
        ("owner@example.com", "wrong password"),
    ):
        with pytest.raises(LoginError) as raised:
            await service.login(email, password)
        messages.append(str(raised.value))
    assert messages[0] == messages[1]


@pytest.mark.asyncio
async def test_inactive_user_cannot_log_in() -> None:
    user = _user(is_active=False)
    harness = _service(users=[user], memberships=[_membership(user)])

    with pytest.raises(LoginError) as raised:
        await harness.service.login("owner@example.com", "correct horse battery staple")
    assert str(raised.value) == "Invalid credentials"
    assert harness.auth_sessions.sessions == []
    # Credential failures never read membership metadata.
    assert harness.memberships.eligible_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("   ", "correct horse battery staple"),
        ("owner@example.com", ""),
        (None, "correct horse battery staple"),
        ("owner@example.com", None),
    ],
)
async def test_malformed_credentials_fail_generically(email: object, password: object) -> None:
    user = _user()
    harness = _service(users=[user], memberships=[_membership(user)])

    with pytest.raises(LoginError) as raised:
        await harness.service.login(email, password)  # type: ignore[arg-type]
    assert str(raised.value) == "Invalid credentials"
    assert harness.auth_sessions.sessions == []
    assert harness.memberships.eligible_calls == 0


# --------------------------------------------------------------------------- #
# Membership selection (frozen T023 addendum)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_zero_eligible_memberships_fails_generically() -> None:
    user = _user()
    harness = _service(users=[user], memberships=[])

    with pytest.raises(LoginError, match="Invalid credentials"):
        await harness.service.login("owner@example.com", "correct horse battery staple")
    assert harness.auth_sessions.sessions == []


# --------------------------------------------------------------------------- #
# Service-boundary ownership invariant (defense in depth)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_foreign_membership_from_repository_fails_closed() -> None:
    user = _user()
    foreign_organization_id = uuid4()
    foreign = _membership(_user("other@example.com"), organization_id=foreign_organization_id)
    token_calls: list[int] = []

    service, auth_sessions, session, _ = _service_with_unscoped_repository(
        users=[user],
        rows=[foreign],
        token_factory=lambda: token_calls.append(1) or generate_token(),
    )

    with pytest.raises(LoginError) as raised:
        await service.login("owner@example.com", "correct horse battery staple")

    assert str(raised.value) == "Invalid credentials"
    # The foreign organization is never disclosed, and nothing is issued.
    assert str(foreign_organization_id) not in str(raised.value)
    assert token_calls == []
    assert auth_sessions.sessions == []
    assert session.added == []


@pytest.mark.asyncio
async def test_mixed_own_and_foreign_memberships_fail_closed_without_filtering() -> None:
    user = _user()
    own = _membership(user)
    foreign = _membership(_user("other@example.com"))
    token_calls: list[int] = []

    service, auth_sessions, session, _ = _service_with_unscoped_repository(
        users=[user],
        rows=[own, foreign],
        token_factory=lambda: token_calls.append(1) or generate_token(),
    )

    # A legitimate own row does not license silently dropping the foreign row:
    # the scoped-result invariant is broken, so the whole result fails closed.
    with pytest.raises(LoginError) as raised:
        await service.login("owner@example.com", "correct horse battery staple")
    assert str(raised.value) == "Invalid credentials"
    assert token_calls == []
    assert auth_sessions.sessions == []
    assert session.added == []

    # The same holds for an explicit selector, which must not expose a list.
    with pytest.raises(LoginError) as selected:
        await service.login(
            "owner@example.com",
            "correct horse battery staple",
            organization_id=own.organization_id,
        )
    assert str(selected.value) == "Invalid credentials"
    assert token_calls == []
    assert auth_sessions.sessions == []


@pytest.mark.asyncio
async def test_foreign_membership_never_reaches_selection_metadata() -> None:
    user = _user()
    foreign = _membership(_user("other@example.com"))

    service, auth_sessions, _, _ = _service_with_unscoped_repository(
        users=[user], rows=[foreign, _membership(user)]
    )

    with pytest.raises(LoginError):
        await service.login("owner@example.com", "correct horse battery staple")

    # No OrganizationSelectionRequired is produced, so the foreign organization
    # id cannot leak through the selection list.
    assert auth_sessions.sessions == []


@pytest.mark.asyncio
async def test_owned_rows_still_select_normally_through_the_same_boundary() -> None:
    user = _user()
    only = _membership(user)
    service, auth_sessions, _, repository = _service_with_unscoped_repository(
        users=[user], rows=[only]
    )

    result = await service.login("owner@example.com", "correct horse battery staple")

    assert isinstance(result, AuthenticatedSession)
    assert result.membership_id == only.id
    assert result.organization_id == only.organization_id
    assert repository.eligible_calls == 1
    assert len(auth_sessions.sessions) == 1


@pytest.mark.asyncio
async def test_owned_rows_still_support_explicit_selector_through_the_same_boundary() -> None:
    user = _user()
    organization_a = uuid4()
    organization_b = uuid4()
    membership_a = _membership(user, organization_id=organization_a)
    membership_b = _membership(user, organization_id=organization_b)
    service, auth_sessions, _, _ = _service_with_unscoped_repository(
        users=[user], rows=[membership_a, membership_b]
    )

    result = await service.login(
        "owner@example.com", "correct horse battery staple", organization_id=organization_b
    )

    assert isinstance(result, AuthenticatedSession)
    assert result.membership_id == membership_b.id
    assert result.organization_id == organization_b
    assert len(auth_sessions.sessions) == 1


@pytest.mark.asyncio
async def test_owned_multiple_rows_still_require_selection_through_the_same_boundary() -> None:
    user = _user()
    organization_a = uuid4()
    organization_b = uuid4()
    service, auth_sessions, _, _ = _service_with_unscoped_repository(
        users=[user],
        rows=[
            _membership(user, organization_id=organization_a),
            _membership(user, organization_id=organization_b),
        ],
    )

    result = await service.login("owner@example.com", "correct horse battery staple")

    assert isinstance(result, OrganizationSelectionRequired)
    assert result.organization_ids == tuple(sorted({organization_a, organization_b}, key=str))
    assert auth_sessions.sessions == []


@pytest.mark.asyncio
async def test_two_eligible_memberships_require_selection_without_issuing_anything() -> None:
    user = _user()
    organization_a = uuid4()
    organization_b = uuid4()
    membership_a = _membership(user, organization_id=organization_a)
    membership_b = _membership(user, organization_id=organization_b)
    harness = _service(users=[user], memberships=[membership_a, membership_b])

    result = await harness.service.login("owner@example.com", "correct horse battery staple")

    assert isinstance(result, OrganizationSelectionRequired)
    assert result.code == ORGANIZATION_SELECTION_REQUIRED
    assert result.code == "ORGANIZATION_SELECTION_REQUIRED"
    # Only eligible organization IDs, sorted by canonical UUID string.
    assert result.organization_ids == tuple(sorted({organization_a, organization_b}, key=str))
    # No session row, no token, and no membership/user metadata.
    assert harness.auth_sessions.sessions == []
    rendered = f"{result!r} {result}"
    assert "token" not in rendered
    assert "role" not in rendered
    assert "membership" not in rendered
    assert "owner" not in rendered
    assert not hasattr(result, "membership_id")
    assert not hasattr(result, "token")
    assert not hasattr(result, "user_id")
    assert not hasattr(result, "session_id")


@pytest.mark.asyncio
async def test_selection_result_is_immutable_and_deduplicated() -> None:
    user = _user()
    organization_a = uuid4()
    organization_b = uuid4()
    memberships = [
        _membership(user, organization_id=organization_a),
        _membership(user, organization_id=organization_b),
        # A duplicate row for the same organization must not appear twice.
        _membership(user, organization_id=organization_a),
    ]
    harness = _service(users=[user], memberships=memberships)

    result = await harness.service.login("owner@example.com", "correct horse battery staple")

    assert isinstance(result, OrganizationSelectionRequired)
    assert result.organization_ids == tuple(sorted({organization_a, organization_b}, key=str))
    assert len(result.organization_ids) == 2
    with pytest.raises(Exception):
        result.organization_ids = ()  # type: ignore[misc]
    with pytest.raises(Exception):
        result.code = "CHANGED"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_eligibility_ignores_inactive_membership_and_inactive_organization() -> None:
    user = _user()
    organization_a = uuid4()
    organization_b = uuid4()
    organization_c = uuid4()
    membership_a = _membership(user, organization_id=organization_a)
    membership_b = _membership(user, organization_id=organization_b, is_active=False)
    membership_c = _membership(user, organization_id=organization_c)
    harness = _service(
        users=[user],
        memberships=[membership_a, membership_b, membership_c],
        organizations={organization_c: _organization(organization_c, is_active=False)},
    )

    # Active A plus inactive B (and an inactive-organization C) leaves exactly one
    # eligible row, so the session is issued automatically for A.
    result = await harness.service.login("owner@example.com", "correct horse battery staple")

    assert isinstance(result, AuthenticatedSession)
    assert result.membership_id == membership_a.id
    assert result.organization_id == organization_a


@pytest.mark.asyncio
async def test_inactive_membership_cannot_issue_a_session() -> None:
    user = _user()
    harness = _service(users=[user], memberships=[_membership(user, is_active=False)])

    with pytest.raises(LoginError, match="Invalid credentials"):
        await harness.service.login("owner@example.com", "correct horse battery staple")
    assert harness.auth_sessions.sessions == []


@pytest.mark.asyncio
async def test_inactive_organization_cannot_issue_a_session() -> None:
    user = _user()
    membership = _membership(user)
    harness = _service(
        users=[user],
        memberships=[membership],
        organizations={
            membership.organization_id: _organization(membership.organization_id, is_active=False)
        },
    )

    with pytest.raises(LoginError) as raised:
        await harness.service.login("owner@example.com", "correct horse battery staple")
    assert str(raised.value) == "Invalid credentials"
    assert harness.auth_sessions.sessions == []


# --------------------------------------------------------------------------- #
# Explicit organization selector
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_explicit_selector_picks_the_matching_eligible_membership() -> None:
    user = _user()
    organization_a = uuid4()
    organization_b = uuid4()
    membership_a = _membership(user, organization_id=organization_a, role=Role.OWNER)
    membership_b = _membership(user, organization_id=organization_b, role=Role.MEMBER)
    harness = _service(users=[user], memberships=[membership_a, membership_b])

    first = await harness.service.login(
        "owner@example.com", "correct horse battery staple", organization_id=organization_a
    )
    assert isinstance(first, AuthenticatedSession)
    assert first.membership_id == membership_a.id
    assert first.organization_id == organization_a

    second = await harness.service.login(
        "owner@example.com", "correct horse battery staple", organization_id=organization_b
    )
    assert isinstance(second, AuthenticatedSession)
    assert second.membership_id == membership_b.id
    assert second.organization_id == organization_b

    # Role never comes from the caller; both sessions come from the verified rows.
    assert first.membership_id != second.membership_id
    assert len(harness.auth_sessions.sessions) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["not-a-uuid", 123, "  ", object()])
async def test_malformed_selector_fails_generically(selector: object) -> None:
    user = _user()
    membership = _membership(user)
    harness = _service(users=[user], memberships=[membership])

    with pytest.raises(LoginError) as raised:
        await harness.service.login(
            "owner@example.com",
            "correct horse battery staple",
            organization_id=selector,  # type: ignore[arg-type]
        )
    assert str(raised.value) == "Invalid credentials"
    assert harness.auth_sessions.sessions == []
    assert harness.memberships.eligible_calls == 0


@pytest.mark.asyncio
async def test_selector_for_unknown_organization_fails_without_fallback() -> None:
    user = _user()
    membership = _membership(user)
    harness = _service(users=[user], memberships=[membership])

    with pytest.raises(LoginError) as raised:
        await harness.service.login(
            "owner@example.com", "correct horse battery staple", organization_id=uuid4()
        )
    assert str(raised.value) == "Invalid credentials"
    # No silent fallback to the user's real membership, and no selection list.
    assert harness.auth_sessions.sessions == []


@pytest.mark.asyncio
async def test_selector_for_another_users_membership_fails_generically() -> None:
    owner = _user()
    stranger = _user("stranger@example.com")
    stranger_membership = _membership(stranger)
    harness = _service(users=[owner, stranger], memberships=[stranger_membership])

    with pytest.raises(LoginError) as raised:
        await harness.service.login(
            "owner@example.com",
            "correct horse battery staple",
            organization_id=stranger_membership.organization_id,
        )
    assert str(raised.value) == "Invalid credentials"
    assert harness.auth_sessions.sessions == []


@pytest.mark.asyncio
async def test_selector_for_inactive_membership_fails_generically() -> None:
    user = _user()
    organization_a = uuid4()
    organization_b = uuid4()
    active = _membership(user, organization_id=organization_a)
    inactive = _membership(user, organization_id=organization_b, is_active=False)
    harness = _service(users=[user], memberships=[active, inactive])

    with pytest.raises(LoginError) as raised:
        await harness.service.login(
            "owner@example.com",
            "correct horse battery staple",
            organization_id=organization_b,
        )
    assert str(raised.value) == "Invalid credentials"
    assert harness.auth_sessions.sessions == []


@pytest.mark.asyncio
async def test_selector_for_inactive_organization_fails_generically() -> None:
    user = _user()
    organization_a = uuid4()
    organization_b = uuid4()
    active = _membership(user, organization_id=organization_a)
    other = _membership(user, organization_id=organization_b)
    harness = _service(
        users=[user],
        memberships=[active, other],
        organizations={organization_b: _organization(organization_b, is_active=False)},
    )

    with pytest.raises(LoginError) as raised:
        await harness.service.login(
            "owner@example.com",
            "correct horse battery staple",
            organization_id=organization_b,
        )
    assert str(raised.value) == "Invalid credentials"
    assert harness.auth_sessions.sessions == []


@pytest.mark.asyncio
async def test_wrong_password_with_multi_org_account_is_never_selection_required() -> None:
    user = _user()
    harness = _service(users=[user], memberships=[_membership(user), _membership(user)])

    with pytest.raises(LoginError) as raised:
        await harness.service.login("owner@example.com", "wrong password")

    assert str(raised.value) == "Invalid credentials"
    assert harness.auth_sessions.sessions == []
    # Credential failure must not consult memberships at all.
    assert harness.memberships.eligible_calls == 0


@pytest.mark.asyncio
async def test_unknown_email_never_returns_organization_metadata() -> None:
    user = _user()
    harness = _service(users=[user], memberships=[_membership(user), _membership(user)])

    with pytest.raises(LoginError) as raised:
        await harness.service.login("nobody@example.com", "correct horse battery staple")

    assert str(raised.value) == "Invalid credentials"
    assert harness.memberships.eligible_calls == 0
    assert harness.auth_sessions.sessions == []


@pytest.mark.asyncio
async def test_selection_required_does_not_generate_a_token_or_session() -> None:
    user = _user()
    session = _FakeSession()
    auth_sessions = _FakeAuthSessionRepository()
    token_calls: list[int] = []

    def counting_token_factory() -> str:
        token_calls.append(1)
        return generate_token()

    service = AuthService(
        cast("object", session),  # type: ignore[arg-type]
        now=lambda: _FIXED_NOW,
        token_factory=counting_token_factory,
        user_repository=cast(UserRepository, _FakeUserRepository([user])),
        membership_repository=cast(
            MembershipRepository, _FakeMembershipRepository([_membership(user), _membership(user)])
        ),
        auth_session_repository=cast(AuthSessionRepository, auth_sessions),
    )

    result = await service.login("owner@example.com", "correct horse battery staple")

    assert isinstance(result, OrganizationSelectionRequired)
    # Selection happens before any token is generated or session staged.
    assert token_calls == []
    assert auth_sessions.sessions == []
    assert session.added == []


@pytest.mark.asyncio
async def test_switching_issues_a_second_session_without_revoking_the_first() -> None:
    user = _user()
    organization_a = uuid4()
    organization_b = uuid4()
    membership_a = _membership(user, organization_id=organization_a)
    membership_b = _membership(user, organization_id=organization_b)
    harness = _service(users=[user], memberships=[membership_a, membership_b])
    service = harness.service

    session_a = await service.login(
        "owner@example.com", "correct horse battery staple", organization_id=organization_a
    )
    session_b = await service.login(
        "owner@example.com", "correct horse battery staple", organization_id=organization_b
    )
    assert isinstance(session_a, AuthenticatedSession)
    assert isinstance(session_b, AuthenticatedSession)

    assert session_a.session_id != session_b.session_id
    assert session_a.membership_id == membership_a.id
    assert session_b.membership_id == membership_b.id
    assert len(harness.auth_sessions.sessions) == 2
    # The first session is untouched: present, still bound to A, not revoked.
    stored_a = await harness.auth_sessions.get(session_a.session_id)
    assert stored_a is not None
    assert stored_a.membership_id == membership_a.id
    assert stored_a.revoked_at is None
    assert await service.get_valid_session(session_a.token) is not None


@pytest.mark.asyncio
async def test_resubmission_rechecks_password_and_current_eligibility() -> None:
    user = _user()
    organization_a = uuid4()
    organization_b = uuid4()
    membership_a = _membership(user, organization_id=organization_a)
    membership_b = _membership(user, organization_id=organization_b)
    harness = _service(users=[user], memberships=[membership_a, membership_b])
    service = harness.service

    first = await service.login("owner@example.com", "correct horse battery staple")
    assert isinstance(first, OrganizationSelectionRequired)

    # A wrong password is rejected even though a selection result already
    # exists: the previous result is not a credential.
    with pytest.raises(LoginError):
        await service.login("owner@example.com", "wrong password", organization_id=organization_a)

    # A membership deactivated since the first attempt is no longer eligible, so
    # an explicit request for it fails generically with no fallback and no list.
    membership_b.is_active = False
    with pytest.raises(LoginError) as stale:
        await service.login(
            "owner@example.com", "correct horse battery staple", organization_id=organization_b
        )
    assert str(stale.value) == "Invalid credentials"

    # A fresh credential login still re-derives current eligibility, and the one
    # remaining eligible organization is issued without another selection round.
    reselected = await service.login("owner@example.com", "correct horse battery staple")
    assert isinstance(reselected, AuthenticatedSession)
    assert reselected.organization_id == organization_a


# --------------------------------------------------------------------------- #
# Session lookup, expiry, revocation, cleanup
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_session_lookup_accepts_only_the_matching_unexpired_token() -> None:
    user = _user()
    service = _service(users=[user], memberships=[_membership(user)]).service
    result = await service.login("owner@example.com", "correct horse battery staple")
    assert isinstance(result, AuthenticatedSession)

    found = await service.get_valid_session(result.token)
    assert found is not None and found.id == result.session_id
    assert await service.get_valid_session("tampered-token") is None
    assert await service.get_valid_session("") is None

    stored = await service._auth_sessions.get(result.session_id)  # noqa: SLF001 - test access
    assert stored is not None
    assert service.is_session_valid(stored) is True

    stored.expires_at = _FIXED_NOW - timedelta(seconds=1)
    assert service.is_session_valid(stored) is False
    assert await service.get_valid_session(result.token) is None


@pytest.mark.asyncio
async def test_revocation_invalidates_the_session_and_retains_the_row() -> None:
    user = _user()
    harness = _service(users=[user], memberships=[_membership(user)])
    service = harness.service
    auth_sessions = harness.auth_sessions
    result = await service.login("owner@example.com", "correct horse battery staple")
    assert isinstance(result, AuthenticatedSession)

    assert await service.revoke_token(result.token) is True
    stored = auth_sessions.sessions[0]
    assert stored.revoked_at == _FIXED_NOW
    assert stored.revoked_at.tzinfo is not None
    assert await service.get_valid_session(result.token) is None
    assert await service.revoke_token(result.token) is False
    assert len(auth_sessions.sessions) == 1


@pytest.mark.asyncio
async def test_cleanup_removes_only_expired_rows() -> None:
    user = _user()
    harness = _service(users=[user], memberships=[_membership(user)])
    service = harness.service
    auth_sessions = harness.auth_sessions
    await service.login("owner@example.com", "correct horse battery staple")
    assert len(auth_sessions.sessions) == 1

    assert await service.cleanup_expired_sessions() == 0
    auth_sessions.sessions[0].expires_at = _FIXED_NOW - timedelta(hours=1)
    assert await service.cleanup_expired_sessions() == 1
    assert auth_sessions.sessions == []


def test_utc_now_is_timezone_aware() -> None:
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


# --------------------------------------------------------------------------- #
# Bootstrap service
# --------------------------------------------------------------------------- #


class _BootstrapUserRepository:
    def __init__(self, users: list[User] | None = None) -> None:
        self.users = users or []

    async def add(self, user: User) -> User:
        if user.id is None:
            user.id = uuid4()
        self.users.append(user)
        return user

    def _find(self, email: str) -> User | None:
        from persistence.identity import canonicalize_email

        _, normalized = canonicalize_email(email)
        return next((user for user in self.users if user.normalized_email == normalized), None)

    async def get_by_email(self, email: str) -> User | None:
        return self._find(email)


class _BootstrapOrganizationRepository:
    def __init__(self, organizations: list[Organization] | None = None) -> None:
        self.organizations = organizations or []

    async def add(self, organization: Organization) -> Organization:
        if organization.id is None:
            organization.id = uuid4()
        self.organizations.append(organization)
        return organization

    async def get(self, organization_id: UUID) -> Organization | None:
        return next((item for item in self.organizations if item.id == organization_id), None)

    async def get_by_name(self, name: str) -> Organization | None:
        return next((item for item in self.organizations if item.name == name), None)


class _BootstrapMembershipRepository:
    def __init__(self, memberships: list[Membership] | None = None) -> None:
        self.memberships = memberships or []

    async def add(self, membership: Membership) -> Membership:
        if membership.id is None:
            membership.id = uuid4()
        self.memberships.append(membership)
        return membership

    async def list_for_user(self, user_id: UUID) -> list[Membership]:
        return [item for item in self.memberships if item.user_id == user_id]


def _bootstrap_service(
    *,
    organizations: list | None = None,
    users: list[User] | None = None,
    memberships: list[Membership] | None = None,
) -> tuple[BootstrapService, _FakeSession]:
    session = _FakeSession()
    service = BootstrapService(
        cast("object", session),  # type: ignore[arg-type]
        organization_repository=cast(
            OrganizationRepository, _BootstrapOrganizationRepository(organizations)
        ),
        user_repository=cast(UserRepository, _BootstrapUserRepository(users)),
        membership_repository=cast(
            MembershipRepository, _BootstrapMembershipRepository(memberships)
        ),
    )
    return service, session


def test_bootstrap_password_helpers_expose_prompt_labels() -> None:
    assert PROMPT_LABEL_ORGANIZATION.strip().endswith(":")
    assert PROMPT_LABEL_EMAIL.strip().endswith(":")
    assert PROMPT_LABEL_PASSWORD.strip().endswith(":")
    assert PROMPT_LABEL_CONFIRM_PASSWORD.strip().endswith(":")


def test_bootstrap_error_carries_a_non_secret_message() -> None:
    error = BootstrapError("bootstrap state conflicts")
    assert "conflicts" in str(error)
    assert not hasattr(error, "password")


def test_bootstrap_outcome_values() -> None:
    assert {outcome.name for outcome in BootstrapOutcome} == {"CREATED", "ALREADY_INITIALIZED"}


def test_auth_session_repr_never_exposes_token_material() -> None:
    digest = hash_token(generate_token())
    auth_session = AuthSession(
        user_id=uuid4(),
        membership_id=uuid4(),
        token_hash=digest,
        expires_at=_FIXED_NOW,
    )
    rendered = repr(auth_session)
    assert digest not in rendered
    assert "token_hash" not in rendered


def test_service_objects_and_errors_never_render_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    password = "correct horse battery staple"
    user = _user()
    membership = _membership(user)
    token = generate_token()
    auth_session = AuthSession(
        user_id=user.id,
        membership_id=membership.id,
        token_hash=hash_token(token),
        expires_at=_FIXED_NOW,
    )
    with caplog.at_level(logging.DEBUG):
        # This is exactly what a debug log of the model objects would emit.
        logging.getLogger("tests.auth").debug(
            "user=%r membership=%r session=%r error=%r",
            user,
            membership,
            auth_session,
            LoginError(LoginFailure.INVALID_CREDENTIAL),
        )

    # The password_hash column must never appear in logs, and the raw token and
    # its digest must never appear in logs or in the session's own repr.
    assert user.password_hash not in caplog.text
    assert password not in caplog.text
    assert token not in caplog.text
    assert auth_session.token_hash not in caplog.text
    assert auth_session.token_hash not in repr(auth_session)
    assert "token_hash" not in repr(auth_session)
