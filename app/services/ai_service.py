"""
Draftly – AI draft generation service using Anthropic Claude.

Responsibilities
----------------
- Build a system prompt that encodes the user's tone preference and signature
- Optionally inject the user's recent sent emails for style-matching
- Call the Claude API to generate a reply draft
- Return the generated subject + body
"""
import logging
import re
from typing import Optional

import anthropic

from app.config import get_settings
from app.models.models import TonePreference

logger   = logging.getLogger(__name__)
settings = get_settings()

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


# ── Prompt builders ───────────────────────────────────────────────────────────

_TONE_INSTRUCTIONS: dict[str, str] = {
    TonePreference.FORMAL:   "Use a professional, formal tone. Avoid contractions and colloquialisms.",
    TonePreference.CONCISE:  "Be brief and to the point. Use short sentences. No fluff.",
    TonePreference.FRIENDLY: "Write in a warm, conversational tone. It's okay to be personable.",
    TonePreference.AUTO:     "Match the tone and style demonstrated in the user's sample sent emails below.",
}


def _build_system_prompt(
    tone: str,
    signature: Optional[str],
    sent_email_samples: list[dict],
) -> str:
    tone_instr = _TONE_INSTRUCTIONS.get(tone, _TONE_INSTRUCTIONS[TonePreference.AUTO])

    parts = [
        "You are Draftly, an AI email assistant that writes reply drafts on behalf of the user.",
        "",
        f"TONE INSTRUCTION: {tone_instr}",
        "",
        "RULES:",
        "1. Write ONLY the reply body. Do NOT include greetings like 'Dear ...' – start directly.",
        "2. If the incoming email asks multiple questions, address each one.",
        "3. Keep the draft realistic and human-sounding. Avoid corporate buzzwords.",
        "4. If unsure about a specific fact (e.g. a date), leave a [PLACEHOLDER] instead of guessing.",
        "5. Do NOT explain what you are doing or add meta-commentary.",
        "6. Output format MUST be exactly:",
        "   SUBJECT: <reply subject line>",
        "   BODY:",
        "   <reply body>",
    ]

    if signature:
        parts += [
            "",
            "USER SIGNATURE (append at the end of every reply):",
            signature,
        ]

    if sent_email_samples:
        parts += [
            "",
            "WRITING STYLE SAMPLES (user's recent sent emails – use these to match their style):",
        ]
        for i, sample in enumerate(sent_email_samples, 1):
            subj = sample.get("subject", "")
            body = sample.get("body", "").strip()[:600]
            parts.append(f"--- Sample {i} (Subject: {subj}) ---")
            parts.append(body)

    return "\n".join(parts)


def _build_user_prompt(
    sender: str,
    subject: str,
    body: str,
) -> str:
    return (
        f"Please write a reply to the following email.\n\n"
        f"FROM: {sender}\n"
        f"SUBJECT: {subject}\n"
        f"EMAIL BODY:\n{body}\n\n"
        f"Remember: output ONLY in the format:\n"
        f"SUBJECT: <subject>\nBODY:\n<body>"
    )


# ── Draft generation ──────────────────────────────────────────────────────────

async def generate_draft(
    sender: str,
    subject: str,
    body: str,
    tone: str = TonePreference.AUTO,
    signature: Optional[str] = None,
    sent_email_samples: Optional[list[dict]] = None,
) -> dict[str, str]:
    """
    Generate a reply draft using Claude.

    Returns a dict: {"subject": "...", "body": "..."}
    """
    client = _get_client()

    system_prompt = _build_system_prompt(
        tone=tone,
        signature=signature,
        sent_email_samples=sent_email_samples or [],
    )
    user_prompt = _build_user_prompt(sender=sender, subject=subject, body=body)

    logger.debug("Calling Claude API for draft generation (tone=%s)", tone)

    response = client.messages.create(
        model      = "claude-sonnet-4-20250514",
        max_tokens = 1024,
        system     = system_prompt,
        messages   = [{"role": "user", "content": user_prompt}],
    )

    raw_text = response.content[0].text.strip()
    return _parse_draft_response(raw_text, subject)


def _parse_draft_response(raw: str, original_subject: str) -> dict[str, str]:
    """
    Parse Claude's structured output into subject and body.

    Expected format:
        SUBJECT: Re: Something
        BODY:
        Hello ...
    """
    subject_match = re.search(r"^SUBJECT:\s*(.+)$", raw, re.MULTILINE)
    body_match    = re.search(r"^BODY:\s*\n([\s\S]+)", raw, re.MULTILINE)

    draft_subject = subject_match.group(1).strip() if subject_match else f"Re: {original_subject}"
    draft_body    = body_match.group(1).strip()    if body_match    else raw

    return {"subject": draft_subject, "body": draft_body}
