"""
Draftly – Unit & integration tests.

Run with:
    pytest tests/ -v
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = 1
    user.email = "test@example.com"
    user.tone_preference = "auto"
    user.signature = "Best,\nTest User"
    user.encrypted_access_token = "encrypted_token"
    user.encrypted_refresh_token = "encrypted_refresh"
    return user


@pytest.fixture
def sample_email_data():
    return {
        "message_id":  "msg_abc123",
        "thread_id":   "thread_xyz",
        "sender":      "boss@company.com",
        "subject":     "Project Update",
        "body":        "Hi, can you send me the Q3 report by Friday? Thanks.",
        "received_at": datetime.now(timezone.utc),
        "in_reply_to": "<original@mail.gmail.com>",
    }


@pytest.fixture
def sample_sent_emails():
    return [
        {"subject": "Re: Meeting", "body": "Hi John,\n\nSure, I'll be there at 3pm.\n\nBest,\nTest User"},
        {"subject": "Re: Invoice", "body": "Hi Sarah,\n\nPlease find the invoice attached.\n\nBest,\nTest User"},
    ]


# ── AI Service Tests ──────────────────────────────────────────────────────────

class TestAIService:
    """Tests for the AI draft generation service."""

    def test_parse_draft_response_valid(self):
        from app.services.ai_service import _parse_draft_response
        raw = "SUBJECT: Re: Project Update\nBODY:\nHi,\n\nI'll send the report by Friday.\n\nBest,\nTest User"
        result = _parse_draft_response(raw, "Project Update")
        assert result["subject"] == "Re: Project Update"
        assert "Friday" in result["body"]

    def test_parse_draft_response_fallback(self):
        from app.services.ai_service import _parse_draft_response
        raw = "Here is a reply without the expected format."
        result = _parse_draft_response(raw, "Some Subject")
        assert result["subject"] == "Re: Some Subject"
        assert result["body"] == raw

    def test_build_system_prompt_with_signature(self):
        from app.services.ai_service import _build_system_prompt
        prompt = _build_system_prompt(
            tone="formal",
            signature="Regards,\nJane Doe",
            sent_email_samples=[],
        )
        assert "formal" in prompt.lower()
        assert "Regards,\nJane Doe" in prompt

    def test_build_system_prompt_with_samples(self):
        from app.services.ai_service import _build_system_prompt
        samples = [{"subject": "Re: Test", "body": "Hello there."}]
        prompt = _build_system_prompt(tone="auto", signature=None, sent_email_samples=samples)
        assert "Hello there." in prompt
        assert "WRITING STYLE SAMPLES" in prompt

    @pytest.mark.asyncio
    async def test_generate_draft_calls_anthropic(self, sample_sent_emails):
        from app.services import ai_service

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="SUBJECT: Re: Test\nBODY:\nHi, sure thing!")]

        with patch.object(ai_service, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_client_factory.return_value = mock_client

            result = await ai_service.generate_draft(
                sender="boss@co.com",
                subject="Test",
                body="Can you confirm?",
                tone="formal",
                signature="Best,\nMe",
                sent_email_samples=sample_sent_emails,
            )

        assert result["subject"] == "Re: Test"
        assert "sure thing" in result["body"]
        mock_client.messages.create.assert_called_once()


# ── Crypto Tests ──────────────────────────────────────────────────────────────

class TestCrypto:
    """Tests for token encryption/decryption."""

    def test_encrypt_decrypt_roundtrip(self):
        from cryptography.fernet import Fernet
        from app.utils.crypto import encrypt_token, decrypt_token
        import app.utils.crypto as crypto_module

        key = Fernet.generate_key().decode()
        with patch.object(crypto_module, "_get_fernet", return_value=Fernet(key.encode())):
            cipher = encrypt_token("my_secret_token")
            plain  = decrypt_token(cipher)
        assert plain == "my_secret_token"

    def test_encrypt_without_key_uses_base64_fallback(self):
        """Without an encryption key, tokens should still round-trip via base64."""
        from app.utils.crypto import encrypt_token, decrypt_token
        import app.utils.crypto as crypto_module

        with patch.object(crypto_module, "_get_fernet", return_value=None):
            cipher = encrypt_token("fallback_token")
            plain  = decrypt_token(cipher)
        assert plain == "fallback_token"


# ── Draft Service Tests ───────────────────────────────────────────────────────

class TestDraftService:
    """Tests for the draft management service."""

    @pytest.mark.asyncio
    async def test_approve_draft_sets_status(self):
        from app.services.draft_service import approve_draft
        from app.models.models import DraftStatus

        draft = MagicMock()
        draft.status = DraftStatus.PENDING
        draft.edited_body = None

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()

        with patch("app.services.draft_service._log", new_callable=AsyncMock):
            result = await approve_draft(draft=draft, edited_body=None, db=mock_db)

        assert result.status == DraftStatus.APPROVED

    @pytest.mark.asyncio
    async def test_approve_draft_with_edit(self):
        from app.services.draft_service import approve_draft
        from app.models.models import DraftStatus

        draft = MagicMock()
        draft.status = DraftStatus.PENDING

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()

        with patch("app.services.draft_service._log", new_callable=AsyncMock):
            result = await approve_draft(draft=draft, edited_body="My edited body", db=mock_db)

        assert result.status == DraftStatus.EDITED
        assert result.edited_body == "My edited body"

    @pytest.mark.asyncio
    async def test_approve_sent_draft_raises(self):
        from app.services.draft_service import approve_draft
        from app.models.models import DraftStatus

        draft = MagicMock()
        draft.status = DraftStatus.SENT

        with pytest.raises(ValueError, match="Cannot approve"):
            await approve_draft(draft=draft, edited_body=None, db=AsyncMock())

    @pytest.mark.asyncio
    async def test_reject_sent_draft_raises(self):
        from app.services.draft_service import reject_draft
        from app.models.models import DraftStatus

        draft = MagicMock()
        draft.status = DraftStatus.SENT

        with pytest.raises(ValueError, match="Cannot reject"):
            await reject_draft(draft=draft, reason="Changed mind", db=AsyncMock())

    @pytest.mark.asyncio
    async def test_send_non_approved_raises(self):
        from app.services.draft_service import send_draft
        from app.models.models import DraftStatus

        draft = MagicMock()
        draft.status = DraftStatus.PENDING
        draft.id = 1

        with pytest.raises(ValueError, match="must be approved"):
            await send_draft(user=MagicMock(), draft=draft, db=AsyncMock())


# ── Email Schema Tests ────────────────────────────────────────────────────────

class TestSchemas:
    """Pydantic schema validation tests."""

    def test_generate_draft_request_valid(self):
        from app.models.schemas import GenerateDraftRequest
        req = GenerateDraftRequest(message_id="abc123", tone="formal")
        assert req.message_id == "abc123"

    def test_generate_draft_request_no_tone(self):
        from app.models.schemas import GenerateDraftRequest
        req = GenerateDraftRequest(message_id="abc123")
        assert req.tone is None

    def test_user_preferences_update_partial(self):
        from app.models.schemas import UserPreferencesUpdate
        update = UserPreferencesUpdate(signature="Best, Me")
        assert update.tone_preference is None
        assert update.signature == "Best, Me"

    def test_approve_draft_request_empty(self):
        from app.models.schemas import ApproveDraftRequest
        req = ApproveDraftRequest()
        assert req.edited_body is None
