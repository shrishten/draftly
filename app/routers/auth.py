"""
Draftly – Authentication router.

Endpoints
---------
GET  /auth/login      → redirect to Google consent screen
GET  /auth/callback   → exchange code for tokens, set session cookie
POST /auth/logout     → revoke tokens + clear cookie
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.services import gmail_service
from app.models.schemas import TokenResponse
from app.utils.auth_deps import get_current_user
from app.models.models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.get("/login", summary="Start Gmail OAuth2 login flow")
async def login():
    """
    Redirects the user to Google's OAuth2 consent screen.
    After authorisation, Google redirects back to /auth/callback.
    """
    auth_url, state = gmail_service.get_auth_url()
    response = RedirectResponse(url=auth_url)
    # Store state in cookie for CSRF validation (simple implementation)
    response.set_cookie("oauth_state", state, httponly=True, samesite="lax", max_age=300)
    return response


@router.get("/callback", response_model=TokenResponse, summary="OAuth2 callback")
async def oauth_callback(
    request:  Request,
    response: Response,
    code:     str,
    state:    str | None = None,
    db:       AsyncSession = Depends(get_db),
):
    """
    Handles Google's OAuth2 redirect.
    Exchanges the authorisation code for tokens, upserts the user,
    and sets a secure session cookie.
    """
    # Optional CSRF check
    stored_state = request.cookies.get("oauth_state")
    if stored_state and state and stored_state != state:
        raise HTTPException(status_code=400, detail="OAuth state mismatch (possible CSRF).")

    try:
        user = await gmail_service.handle_oauth_callback(code=code, db=db)
    except Exception as exc:
        logger.error("OAuth callback error: %s", exc)
        raise HTTPException(status_code=400, detail=f"Authentication failed: {exc}")

    # Set session cookie
    response.set_cookie(
        key      = "user_id",
        value    = str(user.id),
        httponly = True,
        samesite = "lax",
        max_age  = 60 * 60 * 24 * 7,   # 1 week
    )
    response.delete_cookie("oauth_state")

    logger.info("User %s logged in (id=%d)", user.email, user.id)
    return TokenResponse(user_id=user.id, email=user.email)


@router.post("/logout", summary="Revoke Gmail tokens and logout")
async def logout(
    response:     Response,
    current_user: User        = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
):
    """
    Revokes the user's Google OAuth2 tokens and clears the session cookie.
    """
    await gmail_service.revoke_tokens(user=current_user, db=db)
    response.delete_cookie("user_id")
    return {"message": f"User {current_user.email} logged out and tokens revoked."}
