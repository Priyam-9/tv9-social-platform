# TV9 Multi-Platform Publisher

Internal content publishing platform for managing and publishing TV9 content across multiple social media accounts from a single dashboard.

## Overview

The application provides a single workflow for creating a post, selecting target channels, setting channel-specific content, and publishing or scheduling the content.

The backend is built with FastAPI and PostgreSQL. Platform-specific publishing is handled through adapters so that each channel can maintain its own publishing logic and status.

## Current Status

### Implemented

- FastAPI backend
- PostgreSQL database
- SQLAlchemy ORM
- Alembic migrations
- Docker Compose local environment
- Web-based publishing dashboard
- Email/password authentication
- Database-backed sessions with HttpOnly cookies
- Role-based access control
- Admin and Content Manager roles
- User-to-account access control
- Account-scoped post access
- Post scheduling
- Per-target publishing status
- Retry/attempt tracking
- YouTube publishing
- YouTube OAuth connection
- YouTube OAuth account ownership binding
- YouTube tags
- YouTube privacy and category settings
- Custom YouTube thumbnails
- Telegram publishing
- Telegram to YouTube target linking
- Telegram account connection and user grant
- Stable account identity handling for reconnects
- Language validation
- Channel default language
- Per-post language override
- Local media uploads
- Platform content-limit validation
- Rate limiting
- Audit logging
- Local secrets storage
- Secrets Manager abstraction for non-local environments
- Publish request error handling
- Duplicate publish-submission protection
- Session-expiry handling in the dashboard
- Visible publishing error/status feedback
- Visibility-aware dashboard polling
- Dashboard UI hierarchy and status-indicator cleanup

### In progress

- Instagram publishing
- Facebook publishing
- X publishing
- Production infrastructure and deployment

The local application baseline is being prepared for manager review. Final end-to-end smoke testing should be completed in the target environment before production deployment.

---

# Project Structure

```text
tv9-publisher/
├── app/
│   ├── adapters/
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── telegram_adapter.py
│   │   └── youtube_adapter.py
│   │
│   ├── routers/
│   │   ├── accounts.py
│   │   ├── auth.py
│   │   ├── auth_session.py
│   │   ├── demo.py
│   │   ├── media.py
│   │   ├── meta_auth.py
│   │   ├── posts.py
│   │   └── users.py
│   │
│   ├── scripts/
│   │   └── set_user_password.py
│   │
│   ├── services/
│   │   ├── content_limits.py
│   │   ├── password_service.py
│   │   ├── scheduler.py
│   │   └── secrets_service.py
│   │
│   ├── static/
│   │   └── dashboard.html
│   │
│   ├── config.py
│   ├── database.py
│   ├── dependencies.py
│   ├── main.py
│   ├── models.py
│   ├── rate_limit.py
│   └── schemas.py
│
├── alembic/
│   └── versions/
├── docs/
├── infrastructure/
├── media/
├── docker/
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

---

# Local Setup

These steps are for running the project locally.

## 1. Requirements

Install:

- Git
- Docker Desktop

Verify:

```powershell
git --version
docker --version
docker compose version
```

Make sure Docker Desktop is running.

## 2. Clone the repository

```powershell
git clone <REPOSITORY_URL>
cd tv9-publisher
```

Check the project files:

```powershell
Get-ChildItem
```

The repository should contain the `app`, `alembic`, `docker-compose.yml`, `.env.example`, and `requirements.txt` files.

## 3. Create the environment file

Create the local environment file from the example:

```powershell
Copy-Item .env.example .env
```

Open `.env`:

```powershell
code .env
```

Use the variable names already present in `.env.example`.

Typical local settings include:

```text
ENVIRONMENT
DATABASE_URL
API_ACCESS_KEY
LOG_LEVEL
YOUTUBE_CLIENT_ID
YOUTUBE_CLIENT_SECRET
YOUTUBE_REDIRECT_URI
META_APP_ID
META_APP_SECRET
META_REDIRECT_URI
LOCAL_SECRETS_PATH
```

The exact values depend on the accounts and integrations being tested.

For the Docker setup, PostgreSQL is available to the API as:

```text
host: db
port: 5432
user: tv9
database: tv9_publisher
```

A typical local database URL is:

```text
postgresql+psycopg://tv9:tv9dev@db:5432/tv9_publisher
```

Use the value required by the current `.env.example`.

## 4. Start the containers

From the project root:

```powershell
docker compose up --build -d
```

Check the services:

```powershell
docker compose ps
```

Expected:

```text
api   Up
db    Up (healthy)
```

## 5. Check the logs

API:

```powershell
docker compose logs api --tail 100
```

Database:

```powershell
docker compose logs db --tail 100
```

## 6. Run database migrations

```powershell
docker compose exec api alembic upgrade head
```

Check the current revision:

```powershell
docker compose exec api alembic current
```

Check the available heads:

```powershell
docker compose exec api alembic heads
```

## 7. Check the application

Compile the application:

```powershell
docker compose exec api python -m compileall -q app
```

Check the application import:

```powershell
docker compose exec api python -c "from app.main import app; print('FULL APPLICATION IMPORT OK')"
```

Check the health endpoint:

```powershell
Invoke-WebRequest `
  -Uri "http://localhost:8000/health" `
  -Method GET `
  -UseBasicParsing
```

Expected:

```json
{"status":"healthy","environment":"local"}
```

## 8. Open the dashboard

Open:

```text
http://localhost:8000/dashboard
```

For frontend changes, use:

```text
Ctrl + Shift + R
```

to force a full refresh.

---

# User Setup

The project uses email/password authentication.

A password can be created or reset with:

```powershell
docker compose exec api python -m app.scripts.set_user_password
```

The script asks for:

```text
User email:
New password:
Confirm password:
```

Passwords are stored as derived password hashes and are not stored in plaintext.

---

# Roles and Access

There are currently two application roles.

## Admin

Administrators can:

- manage users
- change user roles
- create connected social accounts
- grant account access
- revoke account access
- view all connected accounts

## Content Manager

Content Managers can:

- view accounts that have been assigned to them
- create posts for permitted accounts
- schedule posts
- publish through permitted targets

Account access is controlled through `UserAccountAccess`.

```text
User
  |
  v
UserAccountAccess
  |
  v
SocialAccount
```

The backend enforces the access rules. Dashboard visibility is not used as an authorization mechanism.

---

# Authentication

Authentication is session-based.

```text
email + password
       |
       v
verify password hash
       |
       v
create session
       |
       v
HttpOnly session cookie
```

Protected requests use the session cookie to resolve the authenticated user.

The application does not rely on a client-supplied user email header for authentication.

---

# Security and Access Hardening

The recent hardening pass tightened the boundaries between authentication, authorization, connected accounts, and publishing.

## Session security

- Authentication is resolved from the server-side session cookie.
- The database stores only a hash of the session token.
- Invalid or expired sessions are rejected.
- Logout invalidates the server-side session.

## Role and account authorization

- `User.role` is the authoritative role field.
- Administrators have unrestricted account visibility.
- Content Managers receive explicit `UserAccountAccess` grants.
- Protected publishing actions are authorized server-side; hiding an account in the dashboard is not treated as security.

## OAuth ownership

A YouTube OAuth flow is associated with the authenticated dashboard user. When a channel is connected successfully, the resulting `SocialAccount` is granted to the user who completed the connection.

OAuth credentials remain outside the relational database. The database stores only the configured secrets-store pointer.

## Publishing safeguards

The dashboard now checks publish responses, surfaces failures to the user, prevents rapid duplicate submissions, and avoids overlapping rundown polling requests.

---

# Publishing Model

The application separates a post from its platform targets.

```text
Post
 ├── YouTube target
 ├── Telegram target
 ├── Instagram target
 └── other targets
```

Each target has its own publishing state.

Typical states are:

```text
pending
publishing
published
failed
```

This allows individual targets to succeed or fail independently.

---

# YouTube

YouTube is connected through the browser-based OAuth flow.

Routes:

```text
/auth/youtube/login
/auth/youtube/callback
```

The current YouTube implementation supports:

- OAuth connection
- video publishing
- thumbnails
- tags
- privacy status
- category
- target-specific media
- target-specific thumbnail

---

# Telegram

Telegram is linked to a YouTube target.

The dashboard does not require a second copy of the video for Telegram.

The publishing flow is:

```text
YouTube publishes
      |
      v
YouTube result / URL
      |
      v
Telegram posts the resulting link
```

Telegram title and caption fields are linked to the selected YouTube target in the dashboard.

Telegram language can still be selected independently.

---

# Meta / Instagram

The current Meta OAuth flow is used to discover Instagram Business accounts through the Facebook Pages they are linked to.

Routes:

```text
/auth/meta/login
/auth/meta/callback
```

Flow:

```text
Meta OAuth
   |
   v
Facebook Pages
   |
   v
Linked Instagram Business account
   |
   v
SocialAccount
```

The Instagram publishing implementation is still being completed.

---

# Languages

Supported language codes:

```text
en  English
hi  Hindi
te  Telugu
bn  Bengali
mr  Marathi
ta  Tamil
kn  Kannada
ml  Malayalam
```

A social account can have a default language.

A post target can override it for an individual post.

```text
SocialAccount.default_language
             |
             v
PostTarget.language
```

The dashboard displays the account default and allows a post-specific override.

---

# Media Uploads

Local development stores uploaded media in the mounted media directory.

Video uploads:

```text
POST /media/upload
```

Thumbnail uploads:

```text
POST /media/upload-thumbnail
```

The upload layer:

- generates a new stored filename
- validates file extensions
- checks upload size
- validates thumbnail MIME types
- streams uploads instead of reading the whole file into memory

Current local limits:

```text
Video thumbnail/media limits are enforced in the upload router.
```

Production object storage is outside the local setup described in this README.

---

# Content Limits

Platform content limits are checked before publishing.

They are maintained in:

```text
app/services/content_limits.py
```

Current configured limits include:

```text
YouTube     title 100     caption 5000
Instagram                caption 2200
Facebook                 caption 63206
X                          caption 280
Telegram                 caption 4096
```

These values should be reviewed whenever a platform integration is updated.

---

# Secrets

Local development uses the local secrets store.

The application accesses platform credentials through:

```python
save_secret(...)
get_secret(...)
```

This keeps credential storage separate from the platform adapters.

The same interface is used for non-local environments where the configured secrets backend can use AWS Secrets Manager.

Do not commit:

```text
.env
.local_secrets.json
OAuth tokens
API keys
platform credentials
```

---

# Database

The project uses PostgreSQL and Alembic.

Normal migration:

```powershell
docker compose exec api alembic upgrade head
```

Current revision:

```powershell
docker compose exec api alembic current
```

Migration history:

```powershell
docker compose exec api alembic history --verbose
```

For schema changes, verify the migration against a fresh temporary database before merging.

Example:

```powershell
docker compose exec db psql -U tv9 -d postgres -c "DROP DATABASE IF EXISTS tv9_migration_test;"
docker compose exec db psql -U tv9 -d postgres -c "CREATE DATABASE tv9_migration_test;"
docker compose exec api sh -c "DATABASE_URL='postgresql+psycopg://tv9:tv9dev@db:5432/tv9_migration_test' alembic upgrade head"
docker compose exec api sh -c "DATABASE_URL='postgresql+psycopg://tv9:tv9dev@db:5432/tv9_migration_test' alembic current"
docker compose exec db psql -U tv9 -d postgres -c "DROP DATABASE tv9_migration_test;"
```

Do not run the drop commands against the main `tv9_publisher` database.

---

# Useful Commands

Start:

```powershell
docker compose up --build -d
```

Stop:

```powershell
docker compose down
```

Restart API:

```powershell
docker compose restart api
```

API logs:

```powershell
docker compose logs -f api
```

Database logs:

```powershell
docker compose logs -f db
```

Container status:

```powershell
docker compose ps
```

Open API shell:

```powershell
docker compose exec api sh
```

Open PostgreSQL:

```powershell
docker compose exec db psql -U tv9 -d tv9_publisher
```

---

# Development Checks

After changes, run the following baseline checks:

```powershell
docker compose config
docker compose ps
docker compose exec api python -m compileall -q app
docker compose exec api python -c "from app.main import app; print('FULL APPLICATION IMPORT OK')"
docker compose exec api alembic current
docker compose exec api alembic heads
```

Check the health endpoint:

```powershell
curl.exe -i http://localhost:8000/health
```

Expected:

```json
{"status":"healthy","environment":"local"}
```

For authentication or authorization changes, also check:

- admin login
- content-manager login
- admin-only endpoints
- account scoping
- unauthenticated OAuth protection
- OAuth-created account ownership
- logout/session invalidation

For dashboard changes, check:

- hard refresh behavior
- duplicate-submit protection
- publish error visibility
- rundown polling
- tab visibility pause/resume
- light/dark mode
- browser console errors

For publishing changes, test the affected platform and confirm unrelated targets retain their own state.

For schema changes, verify the migration chain against a fresh temporary database before merging.

---

# Local Demo

The local environment also includes demo endpoints for development and walkthroughs.

```text
POST /demo/seed
POST /demo/simulate
POST /demo/reset
```

These are available only in the local environment.

They are not the production publishing workflow.

---

# Troubleshooting

## API does not start

Check:

```powershell
docker compose logs api --tail 200
```

Then:

```powershell
docker compose ps
```

## Database connection error

Check:

```powershell
docker compose logs db --tail 200
docker compose exec db pg_isready -U tv9 -d tv9_publisher
```

## Migration error

Check:

```powershell
docker compose exec api alembic current
docker compose exec api alembic heads
docker compose exec api alembic history --verbose
```

## Login fails

Check that the user has a password hash:

```powershell
docker compose exec api python -c "from app.database import SessionLocal; from app import models; db=SessionLocal(); rows=db.query(models.User.email,models.User.role,models.User.password_hash).all(); print([(r[0],r[1],bool(r[2])) for r in rows]); db.close()"
```

Reset the password if required:

```powershell
docker compose exec api python -m app.scripts.set_user_password
```

## Content Manager cannot see a channel

Check the user's `UserAccountAccess` records and confirm the social account has been granted to that user.

## Dashboard changes are not visible

Use:

```text
Ctrl + Shift + R
```

and check:

```powershell
docker compose logs -f api
```

---

# Git Workflow

Before committing:

```powershell
git status --short
git diff --check
```

Make sure local configuration and credentials are not staged. In particular, never commit:

```text
.env
.local_secrets.json
OAuth tokens
refresh tokens
API keys
platform credentials
```

Stage only the intended application files:

```powershell
git add <files>
```

Review exactly what will be committed:

```powershell
git diff --cached --stat
git diff --cached
```

Commit with a descriptive message:

```powershell
git commit -m "describe the change"
```

Push the current branch:

```powershell
git push -u origin HEAD
```

---

# Current Development Baseline

The current local baseline has been checked for:

- Docker Compose configuration
- application startup
- PostgreSQL connectivity
- Alembic migrations
- fresh database migration
- Python compilation
- application import
- authentication
- session handling
- roles and permissions
- account scoping
- YouTube OAuth protection
- YouTube OAuth account ownership
- account reconnect/deduplication handling
- language validation
- default language
- Telegram language behavior
- YouTube publishing
- Telegram publishing
- platform content-limit validation
- dashboard publish error handling
- duplicate submission protection
- rundown polling behavior
- health endpoint

Latest verified local Alembic revision:

```text
c4f2a7d91e63
```

The application is being developed incrementally. This repository represents the current local application baseline; Instagram, Facebook, X, and production infrastructure remain separate workstreams.


---

# Review Notes

The current checkpoint focuses on the core publishing workflow and its security/access boundaries.

The main completed work in this checkpoint is:

- authenticated, role-aware dashboard access
- explicit account-level permissions
- protected YouTube OAuth with user ownership
- safer connected-account handling
- reliable publish/error feedback
- duplicate submission protection
- improved dashboard refresh/polling behavior
- final dashboard hierarchy and status styling cleanup

The remaining major feature work is platform expansion for Instagram, Facebook, and X, followed by production deployment/infrastructure work.

Platform credentials and local environment configuration must be supplied separately and must not be committed to the repository.
