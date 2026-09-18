"""Controlled CLI bootstrap for the first TaskPilot organization owner.

The password is collected only from the two hidden interactive prompts required
by the frozen bootstrap contract; there is no environment variable, flag, or
positional argument that can supply or bypass it.  ``--password`` is declared
solely so it can be detected and refused.

Everything printed by this module is a fixed message.  Argument text, database
exception text, SQL, and bind parameters are never echoed, because a user can
put a secret where an argument or a database parameter is expected.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Callable, Sequence
from typing import NoReturn

from sqlalchemy.exc import SQLAlchemyError

from core.settings import settings
from persistence.engine import create_async_engine, create_session_factory
from service.bootstrap import (
    PROMPT_LABEL_CONFIRM_PASSWORD,
    PROMPT_LABEL_EMAIL,
    PROMPT_LABEL_ORGANIZATION,
    PROMPT_LABEL_PASSWORD,
    BootstrapError,
    BootstrapOutcome,
    bootstrap_owner,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

ORGANIZATION_NAME_ENV = "TASKPILOT_BOOTSTRAP_ORGANIZATION_NAME"
EMAIL_ENV = "TASKPILOT_BOOTSTRAP_EMAIL"

PASSWORD_ARGUMENT_ERROR = (
    "bootstrap refuses plaintext password arguments; omit --password and any "
    "positional value, then enter the password twice at the hidden prompt"
)
INVALID_ARGUMENTS_ERROR = "bootstrap failed: invalid bootstrap arguments"
DATABASE_ERROR_MESSAGE = "bootstrap failed: database operation failed; no changes were committed"

ValueReader = Callable[[str], str]
PasswordReader = Callable[[str], str]


class BootstrapArgumentError(argparse.ArgumentError):
    """Raised on unusable arguments without echoing the offending values.

    ``argparse.ArgumentError`` normally renders the argument that caused the
    failure.  A mistyped secret can end up in that position, so callers must
    never print this exception's ``str()``; the CLI prints a fixed message and
    the exception text is sanitized here as defence in depth.
    """

    def __init__(self) -> None:
        self.argument_name = "(arguments)"
        self.message = INVALID_ARGUMENTS_ERROR

    def __str__(self) -> str:
        # argparse.ArgumentError would render ``argument (arguments): message``;
        # a fixed string is safer because the offending value can be a secret.
        return INVALID_ARGUMENTS_ERROR


class _SafeArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that never echoes the supplied argument values."""

    def error(self, message: str) -> NoReturn:  # noqa: ARG002 - message is dropped
        raise BootstrapArgumentError()


def _build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="bootstrap_owner",
        description="Create the first TaskPilot organization owner from hidden prompts.",
    )
    parser.add_argument("--organization-name", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument(
        "--password",
        default=None,
        help=(
            "Rejected. Declared only so that a plaintext password argument can be "
            "detected and refused."
        ),
    )
    return parser


def _resolve_required(
    value: str | None,
    *,
    label: str,
    env_name: str,
    prompt_label: str,
    prompt: ValueReader = input,
) -> str:
    """Return a required non-secret value from the flag, the environment, or a prompt."""

    if value:
        return value
    from_env = os.environ.get(env_name)
    if from_env:
        return from_env
    entered = prompt(prompt_label)
    if not entered:
        raise BootstrapError(f"{label} must not be blank")
    return entered


def _resolve_password(*, pass_reader: PasswordReader = getpass.getpass) -> str:
    """Read the password twice from hidden prompts; mismatches fail closed.

    There is deliberately no environment-variable or argument shortcut here: the
    frozen contract requires the hidden two-entry confirmation for every run.
    """

    first = pass_reader(PROMPT_LABEL_PASSWORD)
    second = pass_reader(PROMPT_LABEL_CONFIRM_PASSWORD)
    if not first:
        raise BootstrapError("password must not be blank")
    if first != second:
        raise BootstrapError("password confirmation did not match")
    return first


def _resolve_inputs(
    args: argparse.Namespace, *, pass_reader: PasswordReader = getpass.getpass
) -> tuple[str, str, str]:
    """Collect organization, email, and password without touching the database."""

    organization_name = _resolve_required(
        args.organization_name,
        label="--organization-name",
        env_name=ORGANIZATION_NAME_ENV,
        prompt_label=PROMPT_LABEL_ORGANIZATION,
    )
    email = _resolve_required(
        args.email,
        label="--email",
        env_name=EMAIL_ENV,
        prompt_label=PROMPT_LABEL_EMAIL,
    )
    password = _resolve_password(pass_reader=pass_reader)
    return organization_name, email, password


async def _run(*, organization_name: str, email: str, password: str) -> int:
    """Run one bootstrap attempt, converting every failure into a fixed message."""

    engine = create_async_engine()
    try:
        session_factory = create_session_factory(engine)
        try:
            async with session_factory() as session:
                result = await bootstrap_owner(
                    session,
                    organization_name=organization_name,
                    email=email,
                    password=password,
                )
        except BootstrapError as error:
            # BootstrapError messages are authored by this repository and are
            # guaranteed not to embed credentials.
            print(f"bootstrap failed: {error}", file=sys.stderr)
            return EXIT_FAILED
        except SQLAlchemyError:
            # Never render the driver exception: it can carry SQL text and bind
            # parameters, and a bind parameter may be a password hash.  The
            # session context manager has already rolled the transaction back.
            print(DATABASE_ERROR_MESSAGE, file=sys.stderr)
            return EXIT_FAILED
        except (OSError, TimeoutError):
            # Connection refused/DNS/timeout carries no secret but its text can
            # include host details; keep the same fixed message.
            print(DATABASE_ERROR_MESSAGE, file=sys.stderr)
            return EXIT_FAILED
    finally:
        await engine.dispose()

    if result.outcome is BootstrapOutcome.CREATED:
        print(
            f"bootstrap created organization {result.organization_id} with owner {result.user_id}"
        )
    else:
        print(
            f"bootstrap already initialized for organization {result.organization_id} "
            f"with owner {result.user_id}; no changes made"
        )
    return EXIT_OK


def _new_event_loop() -> asyncio.AbstractEventLoop:
    """Use psycopg-compatible loops on Windows; keep the native loop elsewhere."""

    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.get_event_loop_policy().new_event_loop()


def main(
    argv: Sequence[str] | None = None,
    *,
    pass_reader: PasswordReader = getpass.getpass,
) -> int:
    """Parse arguments and run bootstrap; returns a process exit code."""

    try:
        args = _build_parser().parse_args(argv)
    except BootstrapArgumentError:
        print(INVALID_ARGUMENTS_ERROR, file=sys.stderr)
        return EXIT_USAGE
    except SystemExit as error:  # already-sanitized help/usage exits
        return int(error.code or EXIT_USAGE)

    # Refuse a plaintext password argument before anything else, including the
    # database check, so a mistyped secret is never forwarded anywhere.
    if args.password is not None:
        print(PASSWORD_ARGUMENT_ERROR, file=sys.stderr)
        return EXIT_USAGE

    try:
        organization_name, email, password = _resolve_inputs(args, pass_reader=pass_reader)
    except BootstrapError as error:
        print(f"bootstrap failed: {error}", file=sys.stderr)
        return EXIT_FAILED
    except (EOFError, KeyboardInterrupt):
        # An aborted prompt must not echo partial input or a traceback.
        print("bootstrap failed: password prompt was interrupted", file=sys.stderr)
        return EXIT_FAILED

    if settings.TASKPILOT_DATABASE_URL is None:
        print("bootstrap failed: TASKPILOT_DATABASE_URL must be configured", file=sys.stderr)
        return EXIT_FAILED

    return asyncio.run(
        _run(organization_name=organization_name, email=email, password=password),
        loop_factory=_new_event_loop,
    )


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
