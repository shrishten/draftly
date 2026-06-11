"""
Draftly – Pydantic schemas for API request/response validation.
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field
from app.models.models import DraftStatus, TonePreference


# ── Auth ─────────────────────────────────────────────────────────────────────

class AuthURLResponse(BaseModel):
    auth_url: str
    state: str


class TokenResponse(BaseModel):
    user_id: int
    email: str
    message: str = "Authentication successful"


# ── User / Preferences ────────────────────────────────────────────────────────

class UserPreferencesUpdate(BaseModel):
    tone_preference: Optional[TonePreference] = None
    signature:       Optional[str]            = Field(None, max_length=500)


class UserResponse(BaseModel):
    id:              int
    email:           str
    tone_preference: TonePreference
    signature:       Optional[str]
    created_at:      datetime

    model_config = {"from_attributes": True}


# ── Email Fetch ───────────────────────────────────────────────────────────────

class IncomingEmailSummary(BaseModel):
    message_id: str
    thread_id:  str
    sender:     str
    subject:    str
    snippet:    str
    received_at: Optional[datetime]


class FetchEmailsResponse(BaseModel):
    emails: list[IncomingEmailSummary]
    count:  int


# ── Draft Generation ──────────────────────────────────────────────────────────

class GenerateDraftRequest(BaseModel):
    message_id: str = Field(..., description="Gmail message ID to reply to")
    tone:       Optional[TonePreference] = Field(
        None,
        description="Override tone for this draft. Omit to use user preference."
    )


class DraftResponse(BaseModel):
    id:                   int
    original_message_id:  str
    original_thread_id:   str
    original_sender:      str
    original_subject:     str
    draft_subject:        str
    draft_body:           str
    edited_body:          Optional[str]
    tone_used:            str
    status:               DraftStatus
    send_attempts:        int
    last_error:           Optional[str]
    sent_message_id:      Optional[str]
    created_at:           datetime
    approved_at:          Optional[datetime]
    sent_at:              Optional[datetime]

    model_config = {"from_attributes": True}


class DraftListResponse(BaseModel):
    drafts: list[DraftResponse]
    count:  int


# ── Draft Review Actions ──────────────────────────────────────────────────────

class ApproveDraftRequest(BaseModel):
    edited_body: Optional[str] = Field(
        None,
        description="If provided, replaces the AI draft before sending."
    )


class RejectDraftRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


# ── Send ──────────────────────────────────────────────────────────────────────

class SendDraftResponse(BaseModel):
    draft_id:        int
    gmail_message_id: str
    thread_id:       str
    status:          DraftStatus
    sent_at:         datetime


# ── Logs ──────────────────────────────────────────────────────────────────────

class AuditLogResponse(BaseModel):
    id:         int
    event:      str
    detail:     Optional[str]
    level:      str
    draft_id:   Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogListResponse(BaseModel):
    logs:  list[AuditLogResponse]
    count: int


# ── Health ────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status:   str = "ok"
    version:  str = "1.0.0"
    env:      str
