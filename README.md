# Draftly – Gmail AI Reply Agent

> An AI-powered backend that reads your Gmail inbox, generates smart reply drafts using Claude, and sends approved emails — all while keeping you fully in control.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Architecture Overview](#architecture-overview)
3. [Project Structure](#project-structure)
4. [Configuration](#configuration)
5. [API Reference](#api-reference)
6. [Design Decisions](#design-decisions)
7. [Running Tests](#running-tests)

---

## Quick Start

### Prerequisites

| Requirement | Version |
|-------------|---------|
| Python | 3.11+ |
| Google Cloud project with Gmail API enabled | — |
| Anthropic API key | — |

### 1. Clone & install

```bash
git clone <your-repo-url>
cd draftly
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
ANTHROPIC_API_KEY=...
SECRET_KEY=<random 32-char string>
ENCRYPTION_KEY=<Fernet key from: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
```

### 3. Set up Google OAuth2

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → enable the **Gmail API**
3. Create **OAuth 2.0 credentials** (Web Application)
4. Add `http://localhost:8000/auth/callback` as an authorised redirect URI
5. Copy Client ID and Client Secret into `.env`

### 4. Run

```bash
uvicorn app.main:app --reload --port 8000
```

Visit **http://localhost:8000/docs** for the interactive Swagger UI.

---

## Architecture Overview

```
User / Client
     │
     ▼
┌─────────────┐    OAuth2     ┌──────────────┐
│  FastAPI    │◄─────────────►│  Google      │
│  REST API   │   Gmail API   │  Gmail API   │
│             │◄─────────────►│              │
└──────┬──────┘               └──────────────┘
       │
       │  generates draft
       ▼
┌─────────────┐
│  Anthropic  │
│  Claude API │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  SQLite DB  │  (swap to Postgres for production)
│  - users    │
│  - drafts   │
│  - sent     │
│  - logs     │
└─────────────┘
```

### Request lifecycle for draft generation

```
POST /drafts/generate
        │
        ├─► Authenticate user (cookie → User record)
        ├─► Fetch full email from Gmail API
        ├─► Fetch user's recent sent emails (style samples)
        ├─► Build prompt with tone + signature + style samples
        ├─► Call Claude API → receive subject + body
        ├─► Save EmailDraft (status: pending)
        └─► Return DraftResponse
```

---

## Project Structure

```
draftly/
├── app/
│   ├── main.py               # FastAPI app, middleware, routers
│   ├── config.py             # Settings from environment (pydantic-settings)
│   ├── db/
│   │   └── database.py       # Async SQLAlchemy engine + session factory
│   ├── models/
│   │   ├── models.py         # ORM models (User, EmailDraft, SentEmail, AuditLog)
│   │   └── schemas.py        # Pydantic request/response schemas
│   ├── routers/
│   │   ├── auth.py           # /auth/* – OAuth2 login/callback/logout
│   │   ├── emails.py         # /emails/* – inbox fetch
│   │   ├── drafts.py         # /drafts/* – generate/approve/reject/send
│   │   └── users.py          # /users/* – preferences & audit logs
│   ├── services/
│   │   ├── gmail_service.py  # All Gmail API interactions
│   │   ├── ai_service.py     # Claude draft generation
│   │   └── draft_service.py  # Draft lifecycle + send-with-retry
│   └── utils/
│       ├── auth_deps.py      # FastAPI dependency: get_current_user
│       └── crypto.py         # Fernet token encryption/decryption
├── tests/
│   └── test_draftly.py       # 16 unit tests
├── requirements.txt
├── .env.example
└── README.md
```

---

## Configuration

All settings are loaded from `.env` via `pydantic-settings`.

| Variable | Description | Default |
|----------|-------------|---------|
| `GOOGLE_CLIENT_ID` | Google OAuth2 client ID | — |
| `GOOGLE_CLIENT_SECRET` | Google OAuth2 client secret | — |
| `GOOGLE_REDIRECT_URI` | OAuth2 callback URL | `http://localhost:8000/auth/callback` |
| `ANTHROPIC_API_KEY` | Anthropic API key | — |
| `SECRET_KEY` | App secret (session signing) | dev default |
| `ENCRYPTION_KEY` | Fernet key for token encryption | — |
| `DATABASE_URL` | SQLAlchemy async DB URL | `sqlite+aiosqlite:///./draftly.db` |
| `MAX_EMAILS_TO_FETCH` | Max inbox emails per fetch | `10` |
| `MAX_SENT_EMAILS_FOR_STYLE` | Sent emails used for AI style | `5` |
| `LOG_LEVEL` | Logging level | `INFO` |

---

## API Reference

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/auth/login` | Redirect to Google OAuth2 consent |
| `GET` | `/auth/callback` | Exchange code for tokens, set session cookie |
| `POST` | `/auth/logout` | Revoke tokens, clear cookie |

### Emails

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/emails/inbox` | Fetch unread inbox emails (`?max_results=10`) |
| `GET` | `/emails/{message_id}` | Fetch a single email in full |

### Drafts

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/drafts/generate` | Generate an AI draft for a Gmail message |
| `GET` | `/drafts` | List all drafts (`?status=pending&limit=20&offset=0`) |
| `GET` | `/drafts/{id}` | Get a single draft |
| `POST` | `/drafts/{id}/approve` | Approve (optionally pass `edited_body`) |
| `POST` | `/drafts/{id}/reject` | Reject a draft |
| `POST` | `/drafts/{id}/send` | Send an approved draft via Gmail |
| `DELETE` | `/drafts/{id}` | Delete a pending/rejected draft |

### Users

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/users/me` | Get current user profile |
| `PATCH` | `/users/me/preferences` | Update `tone_preference` and/or `signature` |
| `GET` | `/users/me/logs` | Get audit log for current user |

### System

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Swagger UI |
| `GET` | `/redoc` | ReDoc UI |

#### Example: Generate a draft

```bash
curl -X POST http://localhost:8000/drafts/generate \
  -H "Content-Type: application/json" \
  -b "user_id=1" \
  -d '{"message_id": "18f3a2b4c1d5e6f7", "tone": "formal"}'
```

Response:
```json
{
  "id": 42,
  "original_sender": "boss@company.com",
  "original_subject": "Q3 Report",
  "draft_subject": "Re: Q3 Report",
  "draft_body": "Hi,\n\nPlease find the Q3 report attached...\n\nBest,\nYour Name",
  "status": "pending",
  "tone_used": "formal",
  "created_at": "2025-06-10T10:00:00Z"
}
```

#### Example: Approve with edits and send

```bash
# Approve with an edited body
curl -X POST http://localhost:8000/drafts/42/approve \
  -H "Content-Type: application/json" \
  -b "user_id=1" \
  -d '{"edited_body": "Hi,\n\nReport is attached. Let me know if you need anything else.\n\nBest"}'

# Send it
curl -X POST http://localhost:8000/drafts/42/send -b "user_id=1"
```

---

## Design Decisions

### Why async throughout?
FastAPI + SQLAlchemy async + aiosqlite gives non-blocking I/O for Gmail API calls, which are the primary latency source. Under load, this avoids thread exhaustion.

### Why SQLite for default storage?
Zero-config for local development and the assessment environment. The `DATABASE_URL` environment variable makes it trivially swappable to PostgreSQL for production (just change the URL and remove the `check_same_thread` connect arg).

### How style learning works
Before calling Claude, Draftly fetches the user's last N sent emails via the Gmail API. These are injected into the system prompt as labelled writing samples. Claude is instructed to match the phrasing, sentence length, and formality it observes in those samples when `tone = "auto"`. This is prompt-based style inference — no fine-tuning required.

### Idempotency for sends
Every draft gets a UUID `idempotency_key` at creation time. Before sending, the service queries `SentEmail` for an existing record with that `draft_id`. If found, it raises an error rather than double-sending. This guards against duplicate sends caused by network retries or user double-clicks.

### Retry logic
`tenacity` wraps the Gmail send call with exponential back-off (3 attempts, 2–30s wait), retrying only on `HttpError` (transient Gmail errors). Non-retryable errors (e.g. invalid address) propagate immediately.

### Token security
OAuth2 tokens are encrypted at rest using Fernet (symmetric AES-128 in CBC mode with HMAC). The encryption key is never stored in the database — only in the environment. Without `ENCRYPTION_KEY`, a base64 fallback is used in development with a loud warning.

### Draft state machine

```
         generate
PENDING ──────────────► APPROVED ──► SENT
   │                        │
   │  (with edits)          └──► EDITED ──► SENT
   ▼
REJECTED                            FAILED (after retries)
```

Only `APPROVED` and `EDITED` drafts can be sent. `SENT` and `FAILED` are terminal states (except manual retry via re-approval).

---

## Running Tests

```bash
pytest tests/ -v
```

The 16-test suite covers:
- AI prompt building and response parsing
- Token encryption/decryption round-trips  
- Draft approval, editing, and rejection state transitions
- Idempotency and guard conditions
- Pydantic schema validation
