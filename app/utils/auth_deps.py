"""
Draftly – FastAPI authentication dependency.

For simplicity, user identity is carried as a signed user_id cookie.
In production you would swap this for a proper JWT / session token.
"""
import logging
from fastapi import Cookie, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.models.models import User

logger = logging.getLogger(__name__)


async def get_current_user(
    user_id: int | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    FastAPI dependency – resolves the current authenticated user.

    Reads `user_id` from a secure HTTP-only cookie set after OAuth callback.
    Raises 401 if the cookie is missing or the user doesn't exist.
    """
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated. Please login via /auth/login.")

    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=401, detail="User not found. Please re-authenticate.")

    if user.encrypted_access_token is None:
        raise HTTPException(
            status_code=401,
            detail="Gmail credentials missing or revoked. Please re-authenticate at /auth/login."
        )

    return user
