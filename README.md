# CareLoop

**Care that never loses track.**

CareLoop is a medication and appointment platform for elderly people and the people who help them.
It differs from a reminder app in two ways:

1. **Every dose is followed until someone answers.** Each scheduled dose is its own database record.
   If nobody confirms it, email alerts escalate step by step: first the elder, then the caretaker,
   then family, at times the care circle chooses.
2. **The elder stays in charge.** Caretakers and family can *suggest* changes to medicines or
   appointments, but nothing changes until the elder approves. Every action is written to an
   append-only activity log.

> CareLoop records what the care circle enters. It never calculates, suggests or changes a dose,
> and an unconfirmed dose is reported as "no answer recorded", never as "missed".

## The problem

Reminder apps send a notification and stop, so a missed dose goes unnoticed. When family or
caretakers step in, they often change schedules without the elder's knowledge, and the apps use
small text and complex menus that older adults struggle with.

## What is implemented

| Feature | Where to look |
|---|---|
| Passcode sign-in per person, three roles (elder, caretaker, family), lockout after 5 wrong tries | `careloop/services/auth_service.py`, `careloop/domain/permissions.py` |
| Medicines: add, view, change, stop, with start/end dates and up to 8 times a day | `careloop/services/record_service.py`, `careloop/web/routes/medications.py` |
| One record per dose: `scheduled → taken / skipped / unconfirmed / cancelled` | `careloop/domain/models.py` (`DoseStatus`), `careloop/services/dose_service.py` |
| Escalation ladder: elder → caretaker → family → close as "no answer recorded" | `careloop/domain/escalation.py`, `careloop/services/care_cycle.py` |
| Email via Gmail SMTP with delivery status, retries and duplicate prevention | `careloop/notifications/`, `careloop/services/notification_delivery.py` |
| Consent-based changes: propose → elder approves / declines → applied in one transaction | `careloop/domain/changes.py`, `careloop/services/proposal_service.py` |
| Doctor appointments with 24-hour reminders and "Add to Google Calendar" links | `careloop/integrations/google_calendar.py`, `careloop/web/routes/appointments.py` |
| Append-only audit log with before/after values | `careloop/storage/sqlite_schema.sql` (triggers), `careloop/web/templates/history/activity.html` |
| Honest adherence summary (taken / skipped / no answer / waiting) | `careloop/domain/adherence.py` |
| Elder-first screens: 20px text (larger on the elder's own pages), a Normal / Large / Extra large text-size control on every page, 48px+ buttons, no JavaScript, WCAG AA contrast | `careloop/web/templates/`, `careloop/web/static/careloop.css` |
| Two storage backends: SQLite (default) and Google Cloud Firestore | `careloop/storage/` |

See [REQUIREMENTS_MAPPING.md](REQUIREMENTS_MAPPING.md) for a criterion-by-criterion map and
[ARCHITECTURE.md](ARCHITECTURE.md) for how the parts fit together.

## How a dose is followed up

```
08:00  dose due        -> email to the elder                ("I took it" button on the home page)
08:30  still no answer -> email to caretakers               (default 30 min, configurable)
09:00  still no answer -> email to family                   (default 60 min, configurable)
11:00  still no answer -> dose closed as "no answer recorded" (default 180 min, max 12 h)
       any answer at any point stops the ladder; a late answer is still recorded
```

## Quick start (Windows, macOS or Linux)

Requires Python 3.10 or newer.

```bash
cd CareLoop
python -m venv .venv
# Windows:   .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

copy .env.example .env        # Windows (use `cp` on macOS/Linux), then edit .env
python scripts/seed_demo.py   # optional: creates a demo circle and prints its sign-ins
uvicorn careloop.web.app:create_app --factory --reload
```

Open http://127.0.0.1:8000. Either sign in with the demo details printed by `seed_demo.py`, or
choose **Start a care circle**.

Run the tests:

```bash
python -m pytest                 # 201 tests; the 6 Firestore ones skip without the emulator
python -m pytest --cov=careloop  # with coverage
ruff check .                     # lint
```

## Configuration

All settings are environment variables; copy `.env.example` to `.env`. Nothing secret is in the
repository.

| Variable | Purpose | Default |
|---|---|---|
| `CARELOOP_SECRET_KEY` | Signs session cookies (32+ characters). Required when `CARELOOP_ENV=production`. | temporary key in development |
| `CARELOOP_STORAGE` | `sqlite` or `firestore` | `sqlite` |
| `CARELOOP_SQLITE_PATH` | SQLite file | `careloop.db` |
| `GOOGLE_CLOUD_PROJECT`, `GOOGLE_APPLICATION_CREDENTIALS` | Firestore project and service-account key | - |
| `FIRESTORE_EMULATOR_HOST` | Use the local Firestore emulator | - |
| `CARELOOP_GMAIL_ADDRESS`, `CARELOOP_GMAIL_APP_PASSWORD` | Gmail account and App Password for sending | email disabled |
| `CARELOOP_PUBLIC_URL` | Base URL used in email links | `http://127.0.0.1:8000` |
| `CARELOOP_SECURE_COOKIES` | `true` when served over HTTPS | `false` |
| `CARELOOP_SCHEDULER_ENABLED`, `CARELOOP_CYCLE_SECONDS` | Background care cycle and its interval | `true`, `60` |

### Google services used

| Service | What it does in CareLoop | Status |
|---|---|---|
| **Gmail (SMTP with App Password)** | Sends dose reminders, escalation alerts, appointment reminders and approval requests. | Sender logic tested with a fake SMTP server. A live send needs your Gmail credentials (see below). |
| **Cloud Firestore** | Alternative storage backend with transactions for every conditional update; indexes and security rules included. | Implemented. Its contract tests run only against the Firestore emulator; see [TESTING.md](TESTING.md) for whether that has been done. |
| **Google Calendar** | "Add to Google Calendar" link on every appointment and in reminder emails. | Tested (URL format). No OAuth or calendar access is needed. |

**Gmail setup:** turn on 2-Step Verification on the sending Google account, create an App Password
(Google Account → Security → App passwords), and put the address and the 16-character password in
`.env`. Without them, every email is recorded as "Not sent - email not set up" on the Emails page;
CareLoop never reports an email as sent unless Gmail accepted it.

**Firestore setup:** create a Firebase project with Firestore, download a service-account key, set
`CARELOOP_STORAGE=firestore`, `GOOGLE_CLOUD_PROJECT` and `GOOGLE_APPLICATION_CREDENTIALS`, then
deploy the indexes and rules with `firebase deploy --only firestore`.

## Known limitations

- The care cycle runs inside the web process. It only escalates while the app is running; after
  downtime it catches up one step per minute and closes doses whose window has passed rather than
  sending stale alerts.
- Email is the only notification channel (no SMS or push).
- The Firestore backend has not been run against live Firestore in this repository's test runs
  unless TESTING.md says otherwise.
- Members are identified by name inside a circle; the circle code is therefore a shared secret.
  Passcodes are per person, hashed with scrypt, and rate-limited.
- No password-reset flow: the elder removes and re-adds a helper to reset their passcode.

## Project layout

```
careloop/
  domain/          pure business rules (no framework, no database)
  services/        use cases: sign-in, records, proposals, doses, the care cycle
  storage/         Repository contract + SQLite and Firestore implementations
  notifications/   Gmail sender and email text
  integrations/    Google Calendar links
  security/        passcode hashing, identifiers
  web/             FastAPI routes, templates, CSS, CSRF and security headers
tests/
  unit/            domain rules, validation, permissions, hashing, email sender
  integration/     services with a real SQLite database, races, repository contract
  web/             HTTP flows, security, accessibility
scripts/           seed_demo.py, accessibility_audit.py (axe-core in Chrome)
```
