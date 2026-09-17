"""T023 bootstrap contract tests for the service layer and CLI."""

import sys
from typing import cast
from uuid import UUID, uuid4

import pytest

from persistence import (
    MembershipRepository,
    OrganizationRepository,
    UserRepository,
)
from persistence.models import AuthSession, Membership, Organization, Role, User
from persistence.tokens import generate_token, hash_token
from service import bootstrap_cli
from service.bootstrap import (
    PROMPT_LABEL_CONFIRM_PASSWORD,
    PROMPT_LABEL_EMAIL,
    PROMPT_LABEL_ORGANIZATION,
    PROMPT_LABEL_PASSWORD,
    BootstrapError,
    BootstrapOutcome,
    BootstrapService,
)

_SYNTHETIC_SECRET = "SuperSecret123!"


@pytest.fixture
def stub_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the database-bound runner so CLI tests never open a connection."""

    async def fake_run(*, organization_name: str, email: str, password: str) -> int:
        del organization_name, email, password
        return bootstrap_cli.EXIT_OK

    monkeypatch.setattr(bootstrap_cli, "_run", fake_run)


class _RecordingSession:
    """AsyncSession stand-in that records writes without a database."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_calls = 0
        self.commit_calls = 0

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        self.flush_calls += 1
        for instance in self.added:
            if getattr(instance, "id", None) is None:
                instance.id = uuid4()

    async def commit(self) -> None:  # pragma: no cover - must never be called
        self.commit_calls += 1


class _BootstrapOrganizationRepository:
    def __init__(self, organizations: list[Organization] | None = None) -> None:
        self.organizations = organizations or []
        self.recorder: _RecordingSession | None = None

    async def add(self, organization: Organization) -> Organization:
        if organization.id is None:
            organization.id = uuid4()
        self.organizations.append(organization)
        if self.recorder is not None:
            self.recorder.added.append(organization)
        return organization

    async def get(self, organization_id: UUID) -> Organization | None:
        return next((item for item in self.organizations if item.id == organization_id), None)

    async def get_by_name(self, name: str) -> Organization | None:
        return next((item for item in self.organizations if item.name == name), None)


class _FakeUserRepository:
    def __init__(self, users: list[User] | None = None) -> None:
        self.users = users or []
        self.recorder: _RecordingSession | None = None

    async def add(self, user: User) -> User:
        if user.id is None:
            user.id = uuid4()
        self.users.append(user)
        if self.recorder is not None:
            self.recorder.added.append(user)
        return user

    async def get(self, user_id: UUID) -> User | None:
        return next((item for item in self.users if item.id == user_id), None)

    async def get_by_email(self, email: str) -> User | None:
        from persistence.identity import canonicalize_email

        _, normalized = canonicalize_email(email)
        return next((user for user in self.users if user.normalized_email == normalized), None)


class _FakeMembershipRepository:
    def __init__(self, memberships: list[Membership] | None = None) -> None:
        self.memberships = memberships or []
        self.recorder: _RecordingSession | None = None

    async def add(self, membership: Membership) -> Membership:
        if membership.id is None:
            membership.id = uuid4()
        self.memberships.append(membership)
        if self.recorder is not None:
            self.recorder.added.append(membership)
        return membership

    async def list_for_user(self, user_id: UUID) -> list[Membership]:
        return [item for item in self.memberships if item.user_id == user_id]


def _bootstrap_service(
    *,
    organizations: list[Organization] | None = None,
    users: list[User] | None = None,
    memberships: list[Membership] | None = None,
) -> tuple[BootstrapService, _RecordingSession]:
    session = _RecordingSession()
    organization_repository = _BootstrapOrganizationRepository(organizations or [])
    user_repository = _FakeUserRepository(users or [])
    membership_repository = _FakeMembershipRepository(memberships or [])
    # Mirror every staged write onto the recording session so tests can assert
    # what bootstrap attempted to persist.
    organization_repository.recorder = session
    user_repository.recorder = session
    membership_repository.recorder = session
    service = BootstrapService(
        cast("object", session),  # type: ignore[arg-type]
        organization_repository=cast(OrganizationRepository, organization_repository),
        user_repository=cast(UserRepository, user_repository),
        membership_repository=cast(MembershipRepository, membership_repository),
    )
    return service, session


# --------------------------------------------------------------------------- #
# CLI: argument and password handling (BLOCKER 1 and BLOCKER 3)
# --------------------------------------------------------------------------- #


def test_positional_plaintext_password_never_appears_in_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        bootstrap_cli.settings, "TASKPILOT_DATABASE_URL", "postgresql://user:pw@host/db"
    )

    exit_code = bootstrap_cli.main([_SYNTHETIC_SECRET])

    captured = capsys.readouterr()
    assert exit_code == bootstrap_cli.EXIT_USAGE
    assert _SYNTHETIC_SECRET not in captured.out
    assert _SYNTHETIC_SECRET not in captured.err
    assert captured.err.strip() == bootstrap_cli.INVALID_ARGUMENTS_ERROR


def test_unrecognized_argument_never_appears_in_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        bootstrap_cli.settings, "TASKPILOT_DATABASE_URL", "postgresql://user:pw@host/db"
    )

    exit_code = bootstrap_cli.main(["--organization-name", "Acme", "--bogus", _SYNTHETIC_SECRET])

    captured = capsys.readouterr()
    assert exit_code == bootstrap_cli.EXIT_USAGE
    assert _SYNTHETIC_SECRET not in captured.out
    assert _SYNTHETIC_SECRET not in captured.err


def test_password_flag_is_rejected_without_echoing_the_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        bootstrap_cli.settings, "TASKPILOT_DATABASE_URL", "postgresql://user:pw@host/db"
    )

    exit_code = bootstrap_cli.main(["--password", _SYNTHETIC_SECRET])

    captured = capsys.readouterr()
    assert exit_code == bootstrap_cli.EXIT_USAGE
    assert _SYNTHETIC_SECRET not in captured.out
    assert _SYNTHETIC_SECRET not in captured.err
    assert "plaintext password" in captured.err


def test_argument_error_text_is_sanitized() -> None:
    parser = bootstrap_cli._build_parser()  # noqa: SLF001 - direct contract test

    with pytest.raises(bootstrap_cli.BootstrapArgumentError) as raised:
        parser.error(f"unrecognized arguments: {_SYNTHETIC_SECRET}")

    assert _SYNTHETIC_SECRET not in str(raised.value)
    assert _SYNTHETIC_SECRET not in repr(raised.value)
    assert str(raised.value) == bootstrap_cli.INVALID_ARGUMENTS_ERROR


def test_no_input_flag_does_not_exist_anymore() -> None:
    help_text = bootstrap_cli._build_parser().format_help()  # noqa: SLF001 - contract test

    assert "--no-input" not in help_text
    assert bootstrap_cli.main(["--no-input"]) == bootstrap_cli.EXIT_USAGE


def test_password_environment_variable_cannot_supply_a_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stub_run: None
) -> None:
    monkeypatch.setenv("TASKPILOT_BOOTSTRAP_PASSWORD", _SYNTHETIC_SECRET)
    monkeypatch.setenv(bootstrap_cli.ORGANIZATION_NAME_ENV, "Acme")
    monkeypatch.setenv(bootstrap_cli.EMAIL_ENV, "owner@example.com")
    monkeypatch.setattr(
        bootstrap_cli.settings, "TASKPILOT_DATABASE_URL", "postgresql://user:pw@host/db"
    )
    prompts: list[str] = []

    # The environment variable must not be consulted, and the run must still
    # require the hidden two-entry confirmation.
    exit_code = bootstrap_cli.main(
        [],
        pass_reader=lambda label: prompts.append(label) or "prompted-secret",
    )

    captured = capsys.readouterr()
    assert prompts == [
        bootstrap_cli.PROMPT_LABEL_PASSWORD,
        bootstrap_cli.PROMPT_LABEL_CONFIRM_PASSWORD,
    ]
    assert _SYNTHETIC_SECRET not in captured.out
    assert _SYNTHETIC_SECRET not in captured.err
    assert exit_code == bootstrap_cli.EXIT_OK


def test_interactive_prompt_uses_the_hidden_reader_for_both_entries() -> None:
    prompts: list[str] = []

    password = bootstrap_cli._resolve_password(  # noqa: SLF001 - direct contract test
        pass_reader=lambda label: prompts.append(label) or "shared-secret"
    )

    assert password == "shared-secret"
    assert prompts == [
        bootstrap_cli.PROMPT_LABEL_PASSWORD,
        bootstrap_cli.PROMPT_LABEL_CONFIRM_PASSWORD,
    ]


def test_password_confirmation_must_match() -> None:
    responses = iter(["first-secret", "second-secret"])

    with pytest.raises(BootstrapError, match="did not match") as raised:
        bootstrap_cli._resolve_password(  # noqa: SLF001 - direct contract test
            pass_reader=lambda _label: next(responses)
        )
    assert "first-secret" not in str(raised.value)
    assert "second-secret" not in str(raised.value)


def test_blank_password_is_rejected() -> None:
    with pytest.raises(BootstrapError, match="must not be blank"):
        bootstrap_cli._resolve_password(  # noqa: SLF001 - direct contract test
            pass_reader=lambda _label: ""
        )


def test_password_prompts_use_the_hidden_getpass_reader() -> None:
    assert bootstrap_cli.getpass.getpass is sys.modules["getpass"].getpass
    assert bootstrap_cli.PROMPT_LABEL_PASSWORD
    assert bootstrap_cli.PROMPT_LABEL_CONFIRM_PASSWORD


def test_help_lists_the_non_secret_options_only() -> None:
    help_text = bootstrap_cli._build_parser().format_help()  # noqa: SLF001 - contract test

    assert "--organization-name" in help_text
    assert "--email" in help_text
    assert "--password" in help_text
    assert "--no-input" not in help_text


def test_password_prompt_interrupt_is_reported_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(bootstrap_cli.ORGANIZATION_NAME_ENV, "Acme")
    monkeypatch.setenv(bootstrap_cli.EMAIL_ENV, "owner@example.com")

    def interrupting_reader(_label: str) -> str:
        raise KeyboardInterrupt

    exit_code = bootstrap_cli.main([], pass_reader=interrupting_reader)

    captured = capsys.readouterr()
    assert exit_code == bootstrap_cli.EXIT_FAILED
    assert "Traceback" not in captured.err
    assert captured.err.strip()


def test_missing_database_url_fails_after_inputs_are_collected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stub_run: None
) -> None:
    monkeypatch.setenv(bootstrap_cli.ORGANIZATION_NAME_ENV, "Acme")
    monkeypatch.setenv(bootstrap_cli.EMAIL_ENV, "owner@example.com")
    monkeypatch.setattr(bootstrap_cli.settings, "TASKPILOT_DATABASE_URL", None)

    exit_code = bootstrap_cli.main([], pass_reader=lambda _label: "shared-secret")

    captured = capsys.readouterr()
    assert exit_code == bootstrap_cli.EXIT_FAILED
    assert "TASKPILOT_DATABASE_URL" in captured.err
    assert "shared-secret" not in captured.err


def test_required_value_reads_from_environment_before_prompting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(bootstrap_cli.EMAIL_ENV, "env-owner@example.com")
    prompts: list[str] = []

    value = bootstrap_cli._resolve_required(  # noqa: SLF001 - direct contract test
        None,
        label="--email",
        env_name=bootstrap_cli.EMAIL_ENV,
        prompt_label="Owner email: ",
        prompt=lambda label: prompts.append(label) or "prompted@example.com",
    )

    assert value == "env-owner@example.com"
    assert prompts == []


def test_required_value_rejects_blank_interactive_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(bootstrap_cli.EMAIL_ENV, raising=False)

    with pytest.raises(BootstrapError, match="must not be blank"):
        bootstrap_cli._resolve_required(  # noqa: SLF001 - direct contract test
            None,
            label="--email",
            env_name=bootstrap_cli.EMAIL_ENV,
            prompt_label="Owner email: ",
            prompt=lambda _label: "",
        )


def test_parser_defaults_block_plaintext_password() -> None:
    args = bootstrap_cli._build_parser().parse_args([])  # noqa: SLF001 - direct test

    assert args.password is None
    assert not hasattr(args, "no_input")


def test_real_cli_process_never_echoes_a_positional_secret() -> None:
    """End-to-end check of the shipped entry point with a synthetic secret."""

    import os
    import subprocess
    from pathlib import Path

    environment = dict(os.environ)
    # A deliberately unreachable URL: argument rejection must happen before any
    # connection attempt, so this value is never dialled.
    environment["TASKPILOT_DATABASE_URL"] = "postgresql://user:pw@127.0.0.1:1/none"
    script = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_owner.py"

    completed = subprocess.run(
        [sys.executable, str(script), _SYNTHETIC_SECRET],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=environment,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode == bootstrap_cli.EXIT_USAGE
    assert _SYNTHETIC_SECRET not in combined
    assert "Traceback" not in combined
    assert "unrecognized arguments" not in combined


# --------------------------------------------------------------------------- #
# CLI: database failure conversion (BLOCKER 2)
# --------------------------------------------------------------------------- #


def test_database_exception_is_converted_to_a_fixed_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from sqlalchemy.exc import IntegrityError

    synthetic_hash = "$argon2id$v=19$synthetic-password-hash-value"
    leaky = IntegrityError(
        "INSERT INTO taskpilot.users (password_hash) VALUES (%(password_hash)s)",
        {"password_hash": synthetic_hash},
        Exception(f"duplicate key value for hash {synthetic_hash}"),
    )

    # Drive the real _run so its database-failure conversion is exercised;
    # only the engine and the bootstrap call itself are replaced.
    class _StubEngine:
        async def dispose(self) -> None:
            return None

    class _StubSession:
        async def __aenter__(self) -> "_StubSession":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

    async def failing_bootstrap(session: object, **kwargs: object) -> None:
        del session, kwargs
        raise leaky

    monkeypatch.setattr(bootstrap_cli, "create_async_engine", lambda: _StubEngine())
    monkeypatch.setattr(
        bootstrap_cli, "create_session_factory", lambda engine: lambda: _StubSession()
    )
    monkeypatch.setattr(bootstrap_cli, "bootstrap_owner", failing_bootstrap)
    monkeypatch.setenv(bootstrap_cli.ORGANIZATION_NAME_ENV, "Acme")
    monkeypatch.setenv(bootstrap_cli.EMAIL_ENV, "owner@example.com")
    monkeypatch.setattr(
        bootstrap_cli.settings, "TASKPILOT_DATABASE_URL", "postgresql://user:pw@host/db"
    )

    exit_code = bootstrap_cli.main([], pass_reader=lambda _label: _SYNTHETIC_SECRET)

    captured = capsys.readouterr()
    assert exit_code == bootstrap_cli.EXIT_FAILED
    assert captured.err.strip() == bootstrap_cli.DATABASE_ERROR_MESSAGE
    for secret in (_SYNTHETIC_SECRET, synthetic_hash, "password_hash", "INSERT INTO"):
        assert secret not in captured.out
        assert secret not in captured.err


# --------------------------------------------------------------------------- #
# Bootstrap service contract
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_bootstrap_creates_owner_membership_with_a_hashed_password() -> None:
    service, session = _bootstrap_service()

    result = await service.run(
        organization_name="Acme",
        email="owner@example.com",
        password="correct horse battery staple",
    )

    assert result.outcome is BootstrapOutcome.CREATED
    assert result.organization_id is not None
    assert len(session.added) == 3
    user = next(item for item in session.added if isinstance(item, User))
    membership = next(item for item in session.added if isinstance(item, Membership))
    assert user.password_hash.startswith("$argon2id$")
    assert "correct horse battery staple" not in user.password_hash
    assert membership.role is Role.OWNER
    assert membership.is_active is True
    assert membership.user_id == user.id


@pytest.mark.asyncio
async def test_bootstrap_repeat_is_a_no_op_without_resetting_the_password() -> None:
    user = User(email="owner@example.com", password_hash="$argon2id$unchanged")
    user.id = uuid4()
    user.is_active = True  # a persisted row carries the server default
    organization = Organization(name="Acme")
    organization.id = uuid4()
    organization.is_active = True
    membership = Membership(user_id=user.id, organization_id=organization.id, role=Role.OWNER)
    membership.id = uuid4()
    service, session = _bootstrap_service(
        organizations=[organization], users=[user], memberships=[membership]
    )

    result = await service.run(
        organization_name="Acme",
        email="owner@example.com",
        password="a-different-password",
    )

    assert result.outcome is BootstrapOutcome.ALREADY_INITIALIZED
    assert session.added == []
    assert user.password_hash == "$argon2id$unchanged"


@pytest.mark.parametrize(
    "organizations, users, memberships",
    [
        ("only-org", None, None),
        (None, "only-user", None),
        ("org", "user", "member-role"),
        ("org", "user", "inactive"),
    ],
)
@pytest.mark.asyncio
async def test_bootstrap_fails_closed_on_partial_or_conflicting_state(
    organizations: str | None,
    users: str | None,
    memberships: str | None,
) -> None:
    user = User(email="owner@example.com", password_hash="$argon2id$unchanged")
    user.id = uuid4()
    organization = Organization(name="Acme")
    organization.id = uuid4()
    membership = Membership(
        user_id=user.id,
        organization_id=organization.id,
        role=Role.ADMIN if memberships == "member-role" else Role.OWNER,
        is_active=memberships != "inactive",
    )
    membership.id = uuid4()
    service, session = _bootstrap_service(
        organizations=[organization] if organizations is not None else None,
        users=[user] if users is not None else None,
        memberships=[membership] if memberships is not None else None,
    )

    with pytest.raises(BootstrapError):
        await service.run(
            organization_name="Acme",
            email="owner@example.com",
            password="correct horse battery staple",
        )
    assert session.added == []


def test_bootstrap_helper_labels_and_outcomes() -> None:
    assert PROMPT_LABEL_ORGANIZATION.strip().endswith(":")
    assert PROMPT_LABEL_EMAIL.strip().endswith(":")
    assert PROMPT_LABEL_PASSWORD.strip().endswith(":")
    assert PROMPT_LABEL_CONFIRM_PASSWORD.strip().endswith(":")
    assert {outcome.name for outcome in BootstrapOutcome} == {"CREATED", "ALREADY_INITIALIZED"}


def test_bootstrap_error_carries_a_non_secret_message() -> None:
    error = BootstrapError("bootstrap state conflicts")

    assert "conflicts" in str(error)
    assert not hasattr(error, "password")


def test_auth_session_repr_never_exposes_token_material() -> None:
    digest = hash_token(generate_token())
    auth_session = AuthSession(
        user_id=uuid4(),
        membership_id=uuid4(),
        token_hash=digest,
        expires_at=None,  # type: ignore[arg-type]
    )

    rendered = repr(auth_session)
    assert digest not in rendered
    assert "token_hash" not in rendered
