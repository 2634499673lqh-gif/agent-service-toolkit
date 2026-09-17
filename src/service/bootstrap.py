"""Controlled administrative bootstrap of the first organization owner.

One business transaction creates the first Organization, owner User, and owner
Membership, or rolls all of it back.  An exact, complete, active owner state is
an idempotent no-op.  Any partial, conflicting, inactive, or mismatched state
fails closed: nothing is repaired, overwritten, reset, or elevated.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import Membership, Organization, Role, User
from persistence.passwords import hash_password
from persistence.repositories import (
    MembershipRepository,
    OrganizationRepository,
    UserRepository,
)

PROMPT_LABEL_ORGANIZATION = "Organization name: "
PROMPT_LABEL_EMAIL = "Owner email: "
PROMPT_LABEL_PASSWORD = "Owner password: "
PROMPT_LABEL_CONFIRM_PASSWORD = "Confirm owner password: "


class BootstrapOutcome(Enum):
    """Observed bootstrap result; both members mean the command succeeded."""

    CREATED = "created"
    ALREADY_INITIALIZED = "already_initialized"


class BootstrapError(Exception):
    """Raised when bootstrap cannot proceed safely; the transaction rolls back."""


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Non-secret summary of a successful bootstrap run."""

    outcome: BootstrapOutcome
    organization_id: UUID
    user_id: UUID
    membership_id: UUID


class BootstrapService:
    """Service-layer owner of the first-owner bootstrap contract."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        password_hasher: Callable[[str], str] = hash_password,
        organization_repository: OrganizationRepository | None = None,
        user_repository: UserRepository | None = None,
        membership_repository: MembershipRepository | None = None,
    ) -> None:
        self.session = session
        self._password_hasher = password_hasher
        self._organizations = organization_repository or OrganizationRepository(session)
        self._users = user_repository or UserRepository(session)
        self._memberships = membership_repository or MembershipRepository(session)

    async def run(
        self,
        *,
        organization_name: str,
        email: str,
        password: str,
    ) -> BootstrapResult:
        """Create the first owner state or confirm an existing exact match."""

        organization = await self._organizations.get_by_name(organization_name)
        user = await self._users.get_by_email(email)

        if organization is not None and user is not None:
            return await self._verify_existing(organization, user)
        if organization is not None or user is not None:
            raise BootstrapError(
                "bootstrap state conflicts with the requested organization/owner; "
                "refusing to repair, overwrite, or elevate"
            )
        return await self._create_owner(
            organization_name=organization_name, email=email, password=password
        )

    async def _verify_existing(self, organization: Organization, user: User) -> BootstrapResult:
        memberships = await self._memberships.list_for_user(user.id)
        if len(memberships) != 1 or not user.is_active or not organization.is_active:
            raise BootstrapError(
                "bootstrap state is partial, inactive, or ambiguous; refusing to repair it"
            )
        membership = memberships[0]
        if (
            membership.organization_id != organization.id
            or not membership.is_active
            or membership.role is not Role.OWNER
        ):
            raise BootstrapError(
                "bootstrap state does not match an active owner membership; "
                "refusing to repair, overwrite, or elevate"
            )
        return BootstrapResult(
            outcome=BootstrapOutcome.ALREADY_INITIALIZED,
            organization_id=organization.id,
            user_id=user.id,
            membership_id=membership.id,
        )

    async def _create_owner(
        self, *, organization_name: str, email: str, password: str
    ) -> BootstrapResult:
        organization = Organization(name=organization_name)
        user = User(email=email, password_hash=self._password_hasher(password))
        await self._organizations.add(organization)
        await self._users.add(user)
        membership = Membership(user_id=user.id, organization_id=organization.id, role=Role.OWNER)
        await self._memberships.add(membership)
        return BootstrapResult(
            outcome=BootstrapOutcome.CREATED,
            organization_id=organization.id,
            user_id=user.id,
            membership_id=membership.id,
        )


async def bootstrap_owner(
    session: AsyncSession,
    *,
    organization_name: str,
    email: str,
    password: str,
) -> BootstrapResult:
    """Run one bootstrap attempt inside a single business transaction.

    The session may not already be in a transaction; commit happens once on
    success and a raised :class:`BootstrapError` rolls everything back.
    """

    service = BootstrapService(session)
    async with session.begin():
        result = await service.run(
            organization_name=organization_name, email=email, password=password
        )
    return result
