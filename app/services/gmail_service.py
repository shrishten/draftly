"""
Draftly – Gmail service layer.

Responsibilities
----------------
- OAuth2 authorisation URL generation and callback handling
- Credential refresh and storage
- Fetching unread / recent emails with full metadata
- Fetching the user's sent emails (for style inference)
- Sending reply emails with correct threading headers
"""
import base64
import email as email_lib
import logging
import uuid
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import get_settings
from app.models.models import User
from app.models.schemas import IncomingEmailSummary
from app.utils.crypto import encrypt_token, decrypt_token

logger = logging.getLogger(__name__)
settings = get_settings()


# ── OAuth2 helpers ────────────────────────────────────────────────────────────

def _build_flow() -> Flow:
    client_config = {
        "web": {
            "client_id":     settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uris": [settings.google_redirect_uri],
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=settings.gmail_scopes,
        redirect_uri=settings.google_redirect_uri,
    )
    return flow


def get_auth_url() -> tuple[str, str]:
    """Return (auth_url, state) for the OAuth2 consent screen."""
    flow = _build_flow()
    state = str(uuid.uuid4())
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return auth_url, state


async def handle_oauth_callback(
    code: str,
    db: AsyncSession,
) -> User:
    """Exchange auth code for tokens, upsert User in DB, return User."""
    flow = _build_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials

    # Get user's email from Google
    service  = build("oauth2", "v2", credentials=creds)
    userinfo = service.userinfo().get().execute()
    email    = userinfo["email"]

    # Upsert user
    result = await db.execute(select(User).where(User.email == email))
    user   = result.scalar_one_or_none()
    if user is None:
        user = User(email=email)
        db.add(user)

    user.encrypted_access_token  = encrypt_token(creds.token)
    user.encrypted_refresh_token = encrypt_token(creds.refresh_token) if creds.refresh_token else user.encrypted_refresh_token
    user.token_expiry             = creds.expiry

    await db.flush()
    await db.refresh(user)
    logger.info("OAuth callback succeeded for user %s (id=%s)", email, user.id)
    return user


# ── Credential builder ────────────────────────────────────────────────────────

def _build_credentials(user: User) -> Credentials:
    access_token  = decrypt_token(user.encrypted_access_token)
    refresh_token = decrypt_token(user.encrypted_refresh_token) if user.encrypted_refresh_token else None
    return Credentials(
        token         = access_token,
        refresh_token = refresh_token,
        token_uri     = "https://oauth2.googleapis.com/token",
        client_id     = settings.google_client_id,
        client_secret = settings.google_client_secret,
        scopes        = settings.gmail_scopes,
    )


def _gmail_service(user: User):
    creds = _build_credentials(user)
    return build("gmail", "v1", credentials=creds)


# ── Email parsing helper ──────────────────────────────────────────────────────

def _decode_body(payload: dict) -> str:
    """Recursively extract plain-text body from Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    if mime_type.startswith("multipart/"):
        for part in payload.get("parts", []):
            result = _decode_body(part)
            if result:
                return result
    return ""


def _parse_headers(headers: list[dict]) -> dict:
    return {h["name"].lower(): h["value"] for h in headers}


def _parse_datetime(internal_date_ms: str) -> Optional[datetime]:
    try:
        ts = int(internal_date_ms) / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return None


# ── Fetch emails ──────────────────────────────────────────────────────────────

async def fetch_unread_emails(user: User, max_results: int | None = None) -> list[IncomingEmailSummary]:
    """
    Fetch unread emails from the user's inbox.
    Returns a list of IncomingEmailSummary (lightweight metadata + snippet).
    """
    max_results = max_results or settings.max_emails_to_fetch
    service     = _gmail_service(user)
    try:
        response = service.users().messages().list(
            userId  = "me",
            q       = "is:unread in:inbox",
            maxResults = max_results,
        ).execute()
    except HttpError as exc:
        logger.error("Gmail list error for user %s: %s", user.id, exc)
        raise

    messages = response.get("messages", [])
    summaries: list[IncomingEmailSummary] = []

    for msg_ref in messages:
        try:
            msg = service.users().messages().get(
                userId  = "me",
                id      = msg_ref["id"],
                format  = "full",
            ).execute()
            headers = _parse_headers(msg["payload"].get("headers", []))
            summaries.append(IncomingEmailSummary(
                message_id  = msg["id"],
                thread_id   = msg.get("threadId", ""),
                sender      = headers.get("from", ""),
                subject     = headers.get("subject", "(no subject)"),
                snippet     = msg.get("snippet", ""),
                received_at = _parse_datetime(msg.get("internalDate", "0")),
            ))
        except Exception as exc:
            logger.warning("Could not parse message %s: %s", msg_ref["id"], exc)

    logger.info("Fetched %d unread emails for user %s", len(summaries), user.id)
    return summaries


async def fetch_full_email(user: User, message_id: str) -> dict:
    """
    Fetch complete email content (headers + decoded body) for a given message ID.
    Returns a dict with keys: message_id, thread_id, sender, subject, body, received_at.
    """
    service = _gmail_service(user)
    try:
        msg = service.users().messages().get(
            userId = "me",
            id     = message_id,
            format = "full",
        ).execute()
    except HttpError as exc:
        logger.error("Gmail get message error %s for user %s: %s", message_id, user.id, exc)
        raise

    headers    = _parse_headers(msg["payload"].get("headers", []))
    body       = _decode_body(msg["payload"])
    return {
        "message_id":  msg["id"],
        "thread_id":   msg.get("threadId", ""),
        "sender":      headers.get("from", ""),
        "subject":     headers.get("subject", "(no subject)"),
        "body":        body,
        "received_at": _parse_datetime(msg.get("internalDate", "0")),
        "in_reply_to": headers.get("message-id", ""),   # used for threading
    }


async def fetch_sent_emails(user: User, max_results: int | None = None) -> list[dict]:
    """
    Fetch the user's recent sent emails.
    Used to infer writing style, tone, and phrasing patterns.
    """
    max_results = max_results or settings.max_sent_emails_for_style
    service     = _gmail_service(user)
    try:
        response = service.users().messages().list(
            userId     = "me",
            q          = "in:sent",
            maxResults = max_results,
        ).execute()
    except HttpError as exc:
        logger.error("Gmail sent list error for user %s: %s", user.id, exc)
        return []

    sent = []
    for msg_ref in response.get("messages", []):
        try:
            msg     = service.users().messages().get(userId="me", id=msg_ref["id"], format="full").execute()
            headers = _parse_headers(msg["payload"].get("headers", []))
            body    = _decode_body(msg["payload"])
            sent.append({
                "subject": headers.get("subject", ""),
                "body":    body[:800],     # truncate for prompt efficiency
            })
        except Exception:
            pass
    return sent


# ── Send email ────────────────────────────────────────────────────────────────

async def send_reply(
    user: User,
    to_address: str,
    subject: str,
    body: str,
    thread_id: str,
    in_reply_to_message_id: str,
) -> dict:
    """
    Send an email reply via Gmail API.
    Maintains thread integrity using correct In-Reply-To and References headers.
    Returns dict with 'id' (Gmail message ID) and 'threadId'.
    """
    service = _gmail_service(user)

    mime_msg = MIMEText(body, "plain", "utf-8")
    mime_msg["To"]          = to_address
    mime_msg["From"]        = user.email
    mime_msg["Subject"]     = subject
    mime_msg["In-Reply-To"] = in_reply_to_message_id
    mime_msg["References"]  = in_reply_to_message_id

    raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()

    try:
        result = service.users().messages().send(
            userId = "me",
            body   = {"raw": raw, "threadId": thread_id},
        ).execute()
        logger.info(
            "Email sent for user %s – Gmail msg ID: %s",
            user.id, result["id"]
        )
        return result
    except HttpError as exc:
        logger.error("Gmail send error for user %s: %s", user.id, exc)
        raise


# ── Token revocation ─────────────────────────────────────────────────────────

async def revoke_tokens(user: User, db: AsyncSession) -> None:
    """Revoke the user's Google tokens and clear stored credentials."""
    if user.encrypted_access_token:
        try:
            import requests
            token = decrypt_token(user.encrypted_access_token)
            requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
        except Exception as exc:
            logger.warning("Token revocation request failed: %s", exc)

    user.encrypted_access_token  = None
    user.encrypted_refresh_token = None
    user.token_expiry             = None
    logger.info("Cleared stored credentials for user %s", user.id)
