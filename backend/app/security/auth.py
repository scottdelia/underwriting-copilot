"""Shared-secret access gate for the deployed demo.

WHY THIS EXISTS
---------------
This is a portfolio artifact on the public internet that calls a paid API. An
open endpoint is both a cost risk and, in a regulated domain, a bad look. The
gate is deliberately the simplest thing that closes those two holes: one shared
secret, checked on every request that costs money.

WHAT IT IS NOT
--------------
This is not authentication. There are no users, no sessions, no roles, and the
brief puts all three out of scope. Anyone holding the secret is indistinguishable
from anyone else holding it. Saying so here is more useful than implying
otherwise by calling it "login".
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

HEADER_NAME = "X-App-Secret"


def verify_shared_secret(
    x_app_secret: str | None = Header(default=None, alias=HEADER_NAME),
    settings: Settings = Depends(get_settings),
) -> None:
    """FastAPI dependency that rejects requests without the shared secret.

    Settings arrive through `Depends` rather than a direct `get_settings()`
    call so that tests can substitute a configuration with the gate switched on
    without mutating process-wide state.

    Args:
        x_app_secret: The secret supplied by the caller.
        settings: Application settings.

    Raises:
        HTTPException: 401 when the secret is missing or wrong.
    """
    # An empty configured secret disables the gate. main.py refuses to start in
    # that state unless dev_mode is explicitly set, so this branch is only ever
    # reached during local development.
    if not settings.app_shared_secret:
        return

    # Constant-time comparison. A plain == leaks the length of the matching
    # prefix through timing, which is enough to recover a secret given patience.
    if not x_app_secret or not hmac.compare_digest(
        x_app_secret, settings.app_shared_secret
    ):
        # No detail about what was wrong: telling a caller whether the header
        # was missing or merely incorrect is free information for an attacker.
        logger.warning("rejected request with missing or invalid shared secret")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authorized.",
            headers={"WWW-Authenticate": HEADER_NAME},
        )
