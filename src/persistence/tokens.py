"""Opaque session-token generation and at-rest hashing.

The raw token is shown exactly once, at issuance.  Only its SHA-256 digest is
stored, so a database copy cannot be replayed as a credential.
``DUMMY_TOKEN_DIGEST`` is a fixed non-secret digest that callers can use when a
code path must look up a token-shaped value without implying a real session.
"""

import hashlib
import secrets
from base64 import urlsafe_b64encode

TOKEN_BYTES = 32

# Never matches a real session; contains no secret and is safe in diagnostics.
DUMMY_TOKEN_DIGEST = hashlib.sha256(b"taskpilot-placeholder-opaque-session-token").hexdigest()


def generate_token() -> str:
    """Return a fresh unpadded base64url token from 32 cryptographically random bytes."""

    return urlsafe_b64encode(secrets.token_bytes(TOKEN_BYTES)).rstrip(b"=").decode("ascii")


def hash_token(raw_token: str) -> str:
    """Return the lowercase hex SHA-256 digest stored for ``raw_token``."""

    if not isinstance(raw_token, str):
        raise TypeError("token must be a string")
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
