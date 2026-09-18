"""Application-level identity canonicalization helpers."""


def canonicalize_email(email: str) -> tuple[str, str]:
    """Return the trimmed display email and its Unicode-canonical identity.

    Python's ``str.strip`` trims Unicode whitespace.  ``casefold`` is used
    deliberately instead of ``lower`` because it defines the application
    identity contract for all future controlled identity creation and lookup.
    """

    if not isinstance(email, str):
        raise TypeError("email must be a string")
    display_email = email.strip()
    if not display_email:
        raise ValueError("email must not be blank")
    return display_email, display_email.casefold()
