"""The JWT contract of the authenticated cases: PyJWT HS256 and one user."""

import jwt

# Benchmark-only key; HS256 requires 32 bytes or more.
KEY = "benchmark-only-jwt-key-not-for-deployment"
ALGORITHM = "HS256"
USER = {"id": 1, "username": "bench"}
# A fixed expiry keeps the token identical between runs. Tokens carry one in
# practice, and Django Bolt's validator requires it.
EXPIRES = 4_102_444_800  # 2100-01-01T00:00:00Z


def token(user_id: int = USER["id"]) -> str:
    return jwt.encode({"sub": str(user_id), "exp": EXPIRES}, KEY, algorithm=ALGORITHM)


def bearer(header: str) -> str | None:
    """The token of an ``Authorization: Bearer <token>`` header."""
    scheme, _, value = header.partition(" ")
    return value if scheme.lower() == "bearer" and value else None


def user_id(value: str) -> int | None:
    """The user id a valid token names; None for any invalid token."""
    try:
        return int(jwt.decode(value, KEY, algorithms=[ALGORITHM])["sub"])
    except jwt.InvalidTokenError, KeyError, ValueError:
        return None


async def auser(value: str):
    """The token's user, loaded from the database; None if there is none."""
    # Imported here: the runner imports this module before Django is set up.
    from django.contrib.auth.models import User

    identifier = user_id(value)
    if identifier is None:
        return None
    return await User.objects.filter(pk=identifier).afirst()


def user(value: str):
    """:func:`auser` for synchronous frameworks."""
    from django.contrib.auth.models import User

    identifier = user_id(value)
    if identifier is None:
        return None
    return User.objects.filter(pk=identifier).first()
