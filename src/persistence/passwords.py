"""Argon2id password hashing helpers.

Passwords are verified with a constant-time library API (``pwdlib``); no
password hashing primitive is implemented here.  Plaintext passwords exist only
in memory for the duration of a call and are never persisted or logged.
"""

from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError

_password_hash = PasswordHash.recommended()

# Guard only against pathological input; the frozen ADR does not mandate a
# policy length, so this is a sanity bound rather than a password policy.
_MAX_PASSWORD_LENGTH = 4096


def hash_password(password: str) -> str:
    """Return an Argon2id password hash suitable for ``users.password_hash``."""

    if not isinstance(password, str):
        raise TypeError("password must be a string")
    if not password or len(password) > _MAX_PASSWORD_LENGTH:
        raise ValueError("password must be a non-empty string of reasonable length")
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Return whether ``password`` matches ``password_hash``.

    A malformed stored hash, unsupported password, or wrong password all return
    ``False`` so callers cannot distinguish failure modes.
    """

    if not isinstance(password, str) or not isinstance(password_hash, str):
        return False
    try:
        return _password_hash.verify(password, password_hash)
    except (PwdlibError, ValueError, TypeError):
        return False
