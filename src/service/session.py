"""Opaque authentication session issuance and validation.

This module owns the T023 credential lifecycle: password verification, opaque
token issuance, session lookup by token digest, revocation, and expiry.  It
deliberately does not build a request-scoped principal or make authorization
decisions; that is T024/T025.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from functools import lru_cache
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import AuthSession, Membership, Organization, Role, User
from persistence.passwords import hash_password, verify_password
from persistence.repositories import (
    AuthSessionRepository,
    MembershipRepository,
    OrganizationRepository,
    UserRepository,
)
from persistence.tokens import generate_token, hash_token

SESSION_TTL = timedelta(hours=24)

ORGANIZATION_SELECTION_REQUIRED = "ORGANIZATION_SELECTION_REQUIRED"


class LoginFailure(Enum):
    """Internal reason a credential was rejected; never a response body."""

    INVALID_CREDENTIAL = "invalid_credential"


class LoginError(Exception):
    """Raised for every failed login with one generic external message."""

    def __init__(self, reason: LoginFailure) -> None:
        super().__init__("Invalid credentials")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    """Result of successful issuance.

    ``token`` is the raw credential and exists only in this in-memory value.  It
    is never logged, never persisted, and never included in ``repr`` output
    beyond the default dataclass rendering of the other fields.
    """

    token: str
    session_id: UUID
    user_id: UUID
    membership_id: UUID
    organization_id: UUID
    expires_at: datetime

    def __repr__(self) -> str:
        return (
            f"AuthenticatedSession(session_id={self.session_id!r}, "
            f"user_id={self.user_id!r}, membership_id={self.membership_id!r}, "
            f"organization_id={self.organization_id!r}, expires_at={self.expires_at!r})"
        )


@dataclass(frozen=True, slots=True)
class OrganizationSelectionRequired:
    """Result that asks an authenticated multi-organization user to choose.

    It grants no authority: no token, no session, no membership id, no role, and
    no user data.  It carries only the fixed code and the organization IDs the
    authenticated user may actually select.  ``frozen=True`` plus ``slots=True``
    keeps it immutable, so it cannot be mutated into a credential.
    """

    organization_ids: tuple[UUID, ...]
    code: str = ORGANIZATION_SELECTION_REQUIRED

    def __repr__(self) -> str:
        return (
            f"OrganizationSelectionRequired(code={self.code!r}, "
            f"organization_ids={len(self.organization_ids)} ids)"
        )


@dataclass(frozen=True, slots=True)
class CurrentPrincipal:
    """Server-derived request-scoped identity.

    Every field comes from the current database state resolved through the
    presented opaque credential.  The value object holds plain scalars only: no
    ORM row, no ``AsyncSession``, no repository, no request object, and no
    credential material (neither the raw token nor its digest), so it is safe to
    pass around and to render in diagnostics.
    """

    user_id: UUID
    membership_id: UUID
    organization_id: UUID
    role: Role
    session_id: UUID

    def __repr__(self) -> str:
        """Render identity without any credential material."""

        return (
            f"CurrentPrincipal(user_id={self.user_id!r}, "
            f"membership_id={self.membership_id!r}, "
            f"organization_id={self.organization_id!r}, role={self.role!r}, "
            f"session_id={self.session_id!r})"
        )


@lru_cache(maxsize=1)
def _placeholder_password_hash() -> str:
    """Return a dummy Argon2id hash used only to equalize failed-login work."""

    return hash_password("taskpilot-placeholder-password-not-a-credential")


def build_principal(
    *,
    auth_session: AuthSession,
    user: User,
    membership: Membership,
    organization: Organization,
) -> CurrentPrincipal:
    """Build the request principal, re-asserting the trust chain defensively.

    Raises the generic :class:`LoginError` when the rows do not agree, so a
    repository or adapter regression fails closed instead of producing a
    principal that mixes identities or tenants.
    """

    if (
        user.id != auth_session.user_id
        or membership.user_id != auth_session.user_id
        or membership.id != auth_session.membership_id
        or organization.id != membership.organization_id
    ):
        raise LoginError(LoginFailure.INVALID_CREDENTIAL)
    return CurrentPrincipal(
        user_id=user.id,
        membership_id=membership.id,
        organization_id=organization.id,
        role=membership.role,
        session_id=auth_session.id,
    )


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(UTC)


class AuthService:
    """Service-layer owner of login, session lookup, revocation, and cleanup."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        now: Callable[[], datetime] = utc_now,
        password_verifier: Callable[[str, str], bool] = verify_password,
        token_factory: Callable[[], str] = generate_token,
        user_repository: UserRepository | None = None,
        membership_repository: MembershipRepository | None = None,
        organization_repository: OrganizationRepository | None = None,
        auth_session_repository: AuthSessionRepository | None = None,
    ) -> None:
        self.session = session
        self.now = now
        self._password_verifier = password_verifier
        self._token_factory = token_factory
        self._users = user_repository or UserRepository(session)
        self._memberships = membership_repository or MembershipRepository(session)
        self._organizations = organization_repository or OrganizationRepository(session)
        self._auth_sessions = auth_session_repository or AuthSessionRepository(session)

    async def login(
        self,
        email: str,
        password: str,
        *,
        organization_id: UUID | None = None,
    ) -> AuthenticatedSession | OrganizationSelectionRequired:
        """Authenticate one credential and issue a session for a verified membership.

        ``organization_id`` is a requested context selector, never authorization
        truth: it is matched only against the authenticated user's own eligible
        memberships, and a miss fails exactly like a bad credential.

        Unknown email, wrong password, malformed credentials, and inactive users
        raise one generic :class:`LoginError` before any membership is read.  An
        active user with no eligible membership fails the same way.  An active
        user with several eligible memberships and no selector receives
        :class:`OrganizationSelectionRequired` instead of a guessed default.
        """

        if not isinstance(email, str) or not isinstance(password, str) or not email.strip():
            # A malformed credential must fail exactly like a wrong one instead
            # of surfacing a validation error to the caller.
            raise LoginError(LoginFailure.INVALID_CREDENTIAL)
        if organization_id is not None and not isinstance(organization_id, UUID):
            # A malformed selector is rejected before any lookup, and never
            # falls back to another membership.
            raise LoginError(LoginFailure.INVALID_CREDENTIAL)

        user = await self._users.get_by_email(email)

        # Always spend one Argon2id verification so a missing account is not
        # distinguishable from a wrong password by response time.
        stored_hash = user.password_hash if user is not None else _placeholder_password_hash()
        password_valid = self._password_verifier(password, stored_hash)

        if user is None or not password_valid:
            raise LoginError(LoginFailure.INVALID_CREDENTIAL)
        if not user.is_active:
            raise LoginError(LoginFailure.INVALID_CREDENTIAL)

        # Only after the credential and user state are verified may membership
        # or organization metadata influence the result.
        selection = await self._resolve_selection(user.id, organization_id)
        if isinstance(selection, OrganizationSelectionRequired):
            return selection
        return await self._issue_session(user, selection)

    async def get_valid_session(self, token: str) -> AuthSession | None:
        """Return the session for ``token`` only when it is present, unrevoked, and unexpired."""

        if not isinstance(token, str) or not token:
            return None
        auth_session = await self._auth_sessions.get_by_token_hash(hash_token(token))
        if auth_session is None:
            return None
        return auth_session if self.is_session_valid(auth_session) else None

    def is_session_valid(self, auth_session: AuthSession) -> bool:
        """Return whether a session is unrevoked and unexpired."""

        if auth_session.revoked_at is not None:
            return False
        expires_at = auth_session.expires_at
        if expires_at.tzinfo is None:
            # Defensive: a naive value must never be compared against aware UTC.
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at > self.now()

    async def revoke_session(self, auth_session: AuthSession) -> AuthSession:
        """Explicitly revoke one session; the row is retained for audit."""

        return await self._auth_sessions.revoke(auth_session, self.now())

    async def revoke_token(self, token: str) -> bool:
        """Revoke the session owning ``token``; return whether one was revoked."""

        auth_session = await self.get_valid_session(token)
        if auth_session is None:
            return False
        await self.revoke_session(auth_session)
        return True

    async def cleanup_expired_sessions(self) -> int:
        """Delete sessions whose 24-hour window already closed."""

        return await self._auth_sessions.delete_expired(self.now())

    async def authenticate(self, raw_token: str) -> CurrentPrincipal | None:
        """Resolve a presented credential into a fresh, server-derived principal.

        Returns ``None`` - meaning one generic authentication failure - for a
        missing/malformed/unknown/revoked/expired session, an inactive or
        missing user/membership/organization, or any inconsistent binding
        between them.  The returned role and organization are read from the
        current Membership/Organization rows, so a role change or deactivation
        takes effect on the very next request.  This path never writes.
        """

        auth_session = await self.get_valid_session(raw_token)
        if auth_session is None:
            return None
        user = await self._require_active_user(auth_session.user_id)
        if user is None:
            return None
        membership = await self._require_active_membership(auth_session)
        if membership is None:
            return None
        organization = await self._require_active_organization(membership.organization_id)
        if organization is None:
            return None
        return build_principal(
            auth_session=auth_session,
            user=user,
            membership=membership,
            organization=organization,
        )

    async def _require_active_user(self, user_id: UUID) -> User | None:
        user = await self._users.get(user_id)
        if user is None or not user.is_active:
            return None
        return user

    async def _require_active_membership(self, auth_session: AuthSession) -> Membership | None:
        membership = await self._memberships.get(auth_session.membership_id)
        if membership is None or not membership.is_active:
            return None
        # Defence in depth: never trust that the session's membership still
        # belongs to the session's user, even though issuance enforced it.
        if membership.user_id != auth_session.user_id:
            return None
        return membership

    async def _require_active_organization(self, organization_id: UUID) -> Organization | None:
        organization = await self._organizations.get(organization_id)
        if organization is None or not organization.is_active:
            return None
        return organization

    async def _resolve_selection(
        self, user_id: UUID, organization_id: UUID | None
    ) -> Membership | OrganizationSelectionRequired:
        """Choose the eligible membership for this login, or ask the user to choose."""

        eligible = await self._memberships.list_eligible_for_user(user_id)
        self._require_owned_memberships(user_id, eligible)
        if organization_id is not None:
            matches = [
                membership
                for membership in eligible
                if membership.organization_id == organization_id
            ]
            if len(matches) == 1:
                return matches[0]
            # No match (unknown, foreign, inactive, or dangling) and an
            # ambiguous match both fail closed without a fallback or a list.
            raise LoginError(LoginFailure.INVALID_CREDENTIAL)
        if not eligible:
            raise LoginError(LoginFailure.INVALID_CREDENTIAL)
        if len(eligible) == 1:
            return eligible[0]
        # Several eligible organizations: never pick first, owner-first, or any
        # other implicit default.  Return only the selectable organization IDs.
        return OrganizationSelectionRequired(
            organization_ids=tuple(
                sorted({membership.organization_id for membership in eligible}, key=str)
            )
        )

    def _require_owned_memberships(self, user_id: UUID, memberships: list[Membership]) -> None:
        """Fail closed unless every consumed membership belongs to this user.

        The repository already scopes its query by ``user_id``, but the service
        must not rely on a method name for tenant ownership: a repository
        regression, adapter bug, or test double could return a foreign row.  Any
        foreign row invalidates the whole result set instead of being filtered
        out, so a broken scope invariant can never be silently downgraded into a
        session for someone else's organization.  The failure is the same
        generic credential error, so no ownership detail is disclosed.
        """

        for membership in memberships:
            if membership.user_id != user_id:
                raise LoginError(LoginFailure.INVALID_CREDENTIAL)

    async def _issue_session(self, user: User, membership: Membership) -> AuthenticatedSession:
        issued_at = self.now()
        expires_at = issued_at + SESSION_TTL
        token = self._token_factory()
        auth_session = AuthSession(
            user_id=user.id,
            membership_id=membership.id,
            token_hash=hash_token(token),
            expires_at=expires_at,
            created_at=issued_at,
            updated_at=issued_at,
        )
        await self._auth_sessions.add(auth_session)
        return AuthenticatedSession(
            token=token,
            session_id=auth_session.id,
            user_id=user.id,
            membership_id=membership.id,
            organization_id=membership.organization_id,
            expires_at=expires_at,
        )
