# CareLoop

**Care that never loses track.**

[![CI](https://github.com/shravanisy2006/CareLoop/actions/workflows/ci.yml/badge.svg)](https://github.com/shravanisy2006/CareLoop/actions/workflows/ci.yml)

CareLoop is a medication and appointment platform for elderly people and the people who help them.
It differs from a reminder app in three ways:

1. **Every dose is followed until someone answers.** Each scheduled dose is its own database record.
   If nobody confirms it, email alerts escalate step by step: first the elder, then the caretaker,
   then family, at times the care circle chooses.
2. **The elder stays in charge.** Caretakers and family can only *suggest* changes to medicines or
   appointments. Each suggestion says who advised it (a doctor, a pharmacist, or nobody medical)
   and is explained in one plain sentence. Nothing changes until the elder approves - or, if the
   elder chooses, one trusted helper approves on their behalf. Nobody can approve their own
   suggestion.
3. **Everything is on the record.** An append-only activity log, an honest adherence history and a
   printable doctor visit summary show the whole circle the same facts.

> CareLoop records what the care circle enters. It never calculates, suggests or changes a dose,
> and an unconfirmed dose is reported as "no answer recorded", never as "missed".

## Try it

**Live demo:** _add your Render link here after deploying (see "Public demo link" below)._
On the demo, press **Sign in as Asha Rao** (the elder), **Sign in as Ravi** (caretaker) or
**Sign in as Meera** (family) - no typing needed. The free host sleeps when idle, so the first
page can take about a minute to load.

Demo sign-ins (also shown on the demo's sign-in page): care circle code **DEMO-CARE**, then

| Person | Role | Passcode |
|---|---|---|
| Asha Rao | Elder | `demo-elder-2468` |
| Ravi | Caretaker | `demo-care-1357` |
| Meera | Family | `demo-family-8642` |

A 3-minute tour: as **Asha**, open the waiting request (plain-language summary, "Advised by
Dr. Shah") and approve or decline it; open **History -> Doctor visit summary**. As **Ravi**,
suggest a change to a medicine and notice he cannot approve it himself. As **Asha**, open
**People & settings** and make Ravi the trusted decision-maker, then see what changes for him.

## The problem

Reminder apps send a notification and stop, so a missed dose goes unnoticed. When family or
caretakers step in, they often change schedules without the elder's knowledge, and the apps use
small text and complex menus that older adults struggle with.

## What is implemented

| Feature | Where to look |
|---|---|
| Passcode sign-in per person, three roles (elder, caretaker, family), lockout after 5 wrong tries | `careloop/services/auth_service.py`, `careloop/domain/permissions.py` |
| Medicines: add, view, change, stop, with start/end dates and up to 8 times a day | `careloop/services/record_service.py`, `careloop/web/routes/medications.py` |
| One record per dose: `scheduled -> taken / skipped / unconfirmed / cancelled` | `careloop/domain/models.py` (`DoseStatus`), `careloop/services/dose_service.py` |
| Escalation ladder: elder -> caretaker -> family -> close as "no answer recorded" | `careloop/domain/escalation.py`, `careloop/services/care_cycle.py` |
| Email via Gmail SMTP with delivery status, retries and duplicate prevention | `careloop/notifications/`, `careloop/services/notification_delivery.py` |
| Consent-based changes: propose -> approve / decline -> applied in one transaction | `careloop/domain/changes.py`, `careloop/services/proposal_service.py` |
| **Trusted decision-maker**: the elder may let one helper decide on their behalf; never on that helper's own suggestion; revocable; recorded as "on behalf of" | `careloop/domain/permissions.py` (`authorize_decision`), `careloop/services/circle_service.py` |
| **"Who advised this?"** on every suggestion (doctor / pharmacist / own idea, name, date) | `careloop/domain/validation.py` (`validate_advice`), `careloop/web/templates/partials/macros.html` |
| **Plain-language summaries** of each request, on screen and in the email | `careloop/domain/proposal_summary.py` |
| **Doctor visit summary**: printable per-medicine adherence, approved changes, next visits | `careloop/web/routes/history.py`, `careloop/web/templates/history/report.html` |
| Doctor appointments with 24-hour reminders and "Add to Google Calendar" links | `careloop/integrations/google_calendar.py`, `careloop/web/routes/appointments.py` |
| Append-only audit log with before/after values | `careloop/storage/sqlite_schema.sql` (triggers), `careloop/web/templates/history/activity.html` |
| Elder-first screens: 20px text (larger on the elder's pages), Normal / Large / Extra large text control on every page, 48px+ buttons, no JavaScript, WCAG AA contrast | `careloop/web/templates/`, `careloop/web/static/careloop.css` |
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

## How a change is decided

```
Ravi (caretaker) suggests "Metformin 2 tablets at 9 AM", advised by Dr. Shah on 14 Nov
   -> Asha (elder) and, if she chose one, her trusted decision-maker are emailed:
      "Ravi suggests changing Metformin: the dose from "1 tablet" to "2 tablets" and the
       time from 8:00 AM to 9:00 AM. Advised by Dr. Shah on 14 Nov 2026."
   -> Asha approves (or her decision-maker does, recorded as on her behalf; never Ravi himself)
   -> the medicine, its future doses and the audit log change together, in one transaction
```

## Quick start (Windows, macOS or Linux)

Requires Python 3.10 or newer.

```bash
cd CareLoop
python -m venv .venv
# Windows:     .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

copy .env.example .env        # Windows (use `cp` on macOS/Linux), then edit .env
python scripts/seed_demo.py   # optional: creates a demo circle and prints its sign-ins
uvicorn careloop.web.app:create_app --factory --reload
```

Open http://127.0.0.1:8000. Either sign in with the demo details printed by `seed_demo.py`, or
choose **Start a care circle**.

Quality checks (the same ones CI runs on every push):

```bash
python -m pytest --cov=careloop  # 239 tests; the 6 Firestore ones skip without the emulator
ruff check . && ruff format --check .
mypy                             # strict-ish type check of careloop/
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
| `CARELOOP_DEMO_MODE` | Public demo: sample circle, one-click sign-in buttons, demo banner. Never for real data. | `false` |

### Public demo link (Render, free)

`render.yaml` describes a free Render web service in demo mode (`CARELOOP_DEMO_MODE=true`):

1. Push the repository to GitHub.
2. Sign in at https://render.com with GitHub (no credit card needed for the free plan).
3. **New -> Blueprint**, choose the CareLoop repository, then **Apply**. Render generates the
   secret key itself.
4. When the deploy finishes, copy the `https://careloop-demo-....onrender.com` link into the
   "Try it" section above and into your submission.

Demo mode creates the demo care circle on start-up, shows "Sign in as ..." buttons and a
"Demo version" banner on every page, and must never be used for real people because its
sign-ins are public. Render's free disk is temporary, so the demo data is recreated after each
restart; the free service sleeps after 15 minutes without visitors.

### Google services used

| Service | What it does in CareLoop | Status |
|---|---|---|
| **Gmail (SMTP with App Password)** | Sends dose reminders, escalation alerts, appointment reminders and approval requests. | Sender logic tested with a fake SMTP server; see [TESTING.md](TESTING.md) for live-send status. |
| **Cloud Firestore** | Alternative storage backend with transactions for every conditional update; indexes and security rules included. | Implemented. Its contract tests run only against the Firestore emulator; see [TESTING.md](TESTING.md). |
| **Google Calendar** | "Add to Google Calendar" link on every appointment and in reminder emails. | Tested (URL format). No OAuth or calendar access is needed. |
| **Cloud Run** | `Dockerfile` for deploying the app as a container. | Not built or deployed yet; see below. |

**Gmail setup:** turn on 2-Step Verification on the sending Google account, create an App Password
(Google Account -> Security -> App passwords), and put the address and the 16-character password in
`.env`. Without them, every email is recorded as "Not sent - email not set up" on the Emails page;
CareLoop never reports an email as sent unless Gmail accepted it.

**Firestore setup:** create a Firebase project with Firestore, download a service-account key, set
`CARELOOP_STORAGE=firestore`, `GOOGLE_CLOUD_PROJECT` and `GOOGLE_APPLICATION_CREDENTIALS`, then
deploy the indexes and rules with `firebase deploy --only firestore`.

**Cloud Run (optional):**
```bash
gcloud run deploy careloop --source . --region asia-south1 \
  --set-env-vars CARELOOP_STORAGE=firestore,GOOGLE_CLOUD_PROJECT=<project>,CARELOOP_SECRET_KEY=<key> \
  --min-instances 1 --no-cpu-throttling
```
Use Firestore on Cloud Run (container disks are temporary). `--min-instances 1` and
`--no-cpu-throttling` keep the background care cycle running. The image has not been built in this
repository's checks because the build environment could not reach Docker Hub; its start command
(production mode, refuses to start without a secret key, Secure cookies) was run and checked
directly.

## Known limitations

- The care cycle runs inside the web process. It only escalates while the app is running; after
  downtime it catches up one step per minute and closes doses whose window has passed rather than
  sending stale alerts.
- Email is the only notification channel (no SMS or push).
- The Firestore backend has not been run against live Firestore or the emulator yet.
- Members are identified by name inside a circle; the circle code is therefore a shared secret.
  Passcodes are per person, hashed with scrypt, and rate-limited.
- No password-reset flow: the elder removes and re-adds a helper to reset their passcode.
- Choosing a trusted decision-maker is the elder's own choice inside the app; it is not a legal
  power of attorney.

## Project layout

```
careloop/
  domain/          pure business rules (no framework, no database)
  services/        use cases: sign-in, records, proposals, doses, the care cycle
  storage/         Repository contract + SQLite and Firestore implementations
  notifications/   Gmail sender and email text
  integrations/    Google Calendar links
  demo.py          the public demo care circle (only used when demo mode is on)
  security/        passcode hashing, identifiers
  web/             FastAPI routes, templates, CSS, CSRF and security headers
tests/
  unit/            domain rules, summaries, validation, permissions, hashing, config, email sender
  integration/     services with a real SQLite database, races, decision-maker, repository contract
  web/             HTTP flows, security, accessibility, text size, demo mode
scripts/           seed_demo.py, accessibility_audit.py (axe-core in Chrome)
render.yaml        free public demo on Render (demo mode)
.github/workflows/ CI: ruff, mypy and pytest on Python 3.10 and 3.12
```
