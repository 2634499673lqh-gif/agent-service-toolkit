"""Focused T118 transaction-boundary tests."""

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from persistence.models import AuthSession, Role
from service.session import AuthenticatedSession, AuthService, CurrentPrincipal


class _Session:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_login_and_commit_commits_only_issued_credentials() -> None:
    session = _Session()
    service = AuthService(cast(object, session))
    issued = AuthenticatedSession(
        token="opaque-token",
        session_id=uuid4(),
        user_id=uuid4(),
        membership_id=uuid4(),
        organization_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service.login = AsyncMock(return_value=issued)  # type: ignore[method-assign]

    result = await service.login_and_commit("user@example.com", "password")

    assert result is issued
    assert session.commits == 1
    assert session.rollbacks == 0


@pytest.mark.asyncio
async def test_login_and_commit_rolls_back_service_failure() -> None:
    session = _Session()
    service = AuthService(cast(object, session))
    service.login = AsyncMock(side_effect=RuntimeError("database unavailable"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        await service.login_and_commit("user@example.com", "password")

    assert session.commits == 0
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_revoke_principal_rechecks_binding_before_commit() -> None:
    session = _Session()
    auth_session = AuthSession(
        user_id=uuid4(),
        membership_id=uuid4(),
        token_hash="digest",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    auth_session.id = uuid4()
    principal = CurrentPrincipal(
        user_id=auth_session.user_id,
        membership_id=auth_session.membership_id,
        organization_id=uuid4(),
        role=Role.OWNER,
        session_id=auth_session.id,
    )
    repository = AsyncMock()
    repository.get.return_value = auth_session
    service = AuthService(cast(object, session), auth_session_repository=repository)

    assert await service.revoke_principal_and_commit(principal) is True
    repository.revoke.assert_awaited_once()
    assert session.commits == 1
