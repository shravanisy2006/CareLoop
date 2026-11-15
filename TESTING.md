# Testing CareLoop

```bash
pip install -r requirements-dev.txt
python -m pytest                    # all tests
python -m pytest --cov              # with branch coverage; fails below 90%
ruff check . && ruff format --check .   # lint and formatting (incl. security rules)
mypy                                # strict type check (mypy --strict) of careloop/
```

The same four checks run in GitHub Actions on Python 3.10 and 3.12 for every push
(`.github/workflows/ci.yml`). The same workflow also runs a dependency vulnerability audit
(`pip-audit`) and the browser accessibility audit below; `codeql.yml` runs GitHub CodeQL static
analysis, and Dependabot (`.github/dependabot.yml`) opens weekly update pull requests.

Tests use a real SQLite database in a temporary folder, a `FixedClock` that only moves when the
test tells it to, and a `RecordingSender` in place of Gmail (`tests/conftest.py`). No network or
credentials are needed.

## Results of the last run

Recorded on 15 Nov 2026 on Linux with Python 3.13 (FastAPI 0.163.0) and again with Python 3.10
(FastAPI 0.162.5), with identical results. It has not yet been run on Windows itself.

| Suite | Tests | Result |
|---|---|---|
| `tests/unit` | 106 | 106 passed (10 of them property-based, each running hundreds of generated cases) |
| `tests/integration` | 69 | 63 passed, 6 skipped (Firestore contract; emulator not available) |
| `tests/web` | 100 | 100 passed |
| **Total** | **275** | **269 passed, 6 skipped, 0 failed** |

The suite also passes with `-W error` (no warnings, no unclosed resources).

Branch coverage (`pytest --cov`): **94%**, and CI fails if it drops below 90%
(`[tool.coverage]` in `pyproject.toml`). `firestore_repository.py` is left out of that figure
because its only tests need the Firestore emulator (see below); it is not hidden - it is named in
the config with that reason.

`ruff check .` (26 rule families, see `pyproject.toml`), `ruff format --check .` and `mypy --strict` (67 files): no issues. Any rule that is switched off is listed in `pyproject.toml` with the reason next to it.

## Property-based tests (`tests/unit/test_properties.py`)

Hypothesis generates hundreds of schedules, timezones (including daylight-saving zones), policies
and clock readings per run and checks rules that must hold for all of them:

* every generated dose lies inside the window, is on a scheduled day and time, with no duplicates;
* splitting a time window in two never loses or duplicates a dose (so cycles can run at any pace);
* dose ids are deterministic and change with the medication version;
* every ordered policy within 12 hours is accepted; the target level never decreases over time;
* escalation climbs at most one level per cycle, never touches resolved doses, and a dose nobody
  answers walks elder -> caretaker -> family and is then closed as "no answer recorded";
* adherence counts always add up to the due doses and percentages stay between 0 and 100.

## Efficiency tests (`tests/integration/test_query_efficiency.py`)

* Every SQL statement the app runs during a realistic workload (sign-in, all pages for two roles,
  four care cycles) is captured and checked with `EXPLAIN QUERY PLAN`: **no query scans a whole
  table**, except the care cycle's deliberate read of all circles (listed in the test with why).
* The home page runs **the same number of queries with 1 medicine as with 10** (no N+1 queries).

## What each suite proves

### Unit (`tests/unit`) - pure rules, no database
* **Escalation** (`test_escalation_rules.py`): reminder at due time, caretaker after the grace
  period, family after the next interval, one level per cycle, close instead of stale alerts,
  resolved doses never escalate, policy ordering and the 12-hour cap, each level's audience,
  removed members excluded.
* **Schedule and adherence** (`test_schedule_and_adherence.py`): timezone conversion (08:00 IST =
  02:30 UTC), inclusive end date, nothing before start, invalid times rejected, deterministic dose
  ids, unconfirmed counted as unknown (not missed), future/cancelled/open doses excluded.
* **Validation** (`test_validation.py`): required fields, length limits, time and date formats,
  appointments in the future and within two years, weak passcodes, email normalisation, circle code
  format.
* **Permissions and hashing** (`test_permissions_and_passcodes.py`): the full role matrix,
  cross-circle denial, removed members, salted scrypt hashes, malformed hashes never verify.
* **Messages and calendar** (`test_messages_and_calendar.py`): duplicate-proof notification ids,
  alert wording ("does not mean it was missed"), no-email recipients, Google Calendar URL format.
* **Request summaries and decision rights** (`test_proposal_summary.py`): exact plain-language
  sentences for changes, stops, new and cancelled appointments (in local time), doctor /
  pharmacist / own-idea wording, advice validation (doctor's name required, no future dates), only
  the elder or the trusted helper may decide, and nobody on their own suggestion.
* **Configuration** (`test_config.py`): production refuses to start without a secret key,
  development uses a temporary one, email needs both values, unknown storage rejected, minimum
  cycle interval, `.env` read but real environment wins.
* **Email sender** (`test_email_sender.py`): Gmail host/port and login, auth failure and refused
  recipient become `EmailDeliveryError` without server text, missing credentials never pretend to
  send. `smtplib.SMTP_SSL` is replaced by a fake; no real email is sent.

### Integration (`tests/integration`) - services with a real SQLite database
* **Escalation flow** (`test_escalation_flow.py`): elder → caretaker → family emails, confirmation
  stops escalation, five cycles send one email, window closes as unconfirmed, late answers recorded,
  duplicate confirmation rejected, failed email retried then sent.
* **Doses and reminders** (`test_doses_and_reminders.py`): idempotent generation, no back-dated
  doses, stopping cancels future doses, caretaker records on behalf (attributed), too-early and
  invalid outcomes refused, cross-circle refused, **confirmation during escalation wins**, queued
  alert cancelled after confirmation, **four simultaneous confirmations from threads record exactly
  one**, appointment reminder queued once per member 24 h ahead, unconfigured email recorded as
  `not_configured`.
* **Proposals** (`test_proposal_flow.py`): proposal leaves the record untouched, elder emailed,
  approval applies change with before/after audit, approved schedule change replaces future doses,
  rejection changes nothing, only the elder decides, helpers cannot edit directly, no double
  decision, stale proposal refused, only the proposer withdraws, appointment add/change/cancel via
  proposals, cross-circle targets refused, missing details refused.
* **Trusted decision-maker** (`test_decision_maker.py`): the helper approves on the elder's behalf
  (audited as such), cannot approve their own suggestion, other helpers cannot decide, the elder
  takes the trust back, removing the helper clears it, only the elder chooses and only from their
  own circle, both the elder and the helper get a plain-language email that names the doctor, the
  proposer is not emailed about their own request, advice is stored, and an existing database is
  upgraded in place with the new columns.
* **Auth** (`test_auth.py`): valid sign-in, case-insensitive name, dashed code, identical error for
  four kinds of failure, one person's passcode doesn't work for another, lockout and expiry,
  removed member and foreign-circle sessions rejected.
* **Repository contract** (`test_repository_contract.py`): the same tests run against SQLite and
  Firestore: lookups, unique names, atomic rejection of stale writes, insert-once doses, conditional
  resolve/escalate/close, notification de-duplication. Plus SQLite-only checks that the database
  refuses edits or deletes of audit events and refuses a "taken" dose with no recorder.

### Web (`tests/web`) - the real FastAPI app through HTTP
* **Flows** (`test_web_flows.py`): start a circle, form errors, elder presses "I took it", caretaker
  change waits for elder approval (and the caretaker's own approval attempt gets 403), family cannot
  record doses, every page renders for every role, appointment with Google Calendar link.
* **Management** (`test_management_routes.py`): add/remove helpers, validation, duplicate names,
  removal ends the session, helpers cannot manage people or settings, timing changes, decline,
  withdraw, direct edits by the elder, suggestions by the caretaker, 404s.
* **Security** (`test_security.py`): every page and action needs a session, CSRF on every POST
  including sign-in, other circles' records are 404, HTML escaping, CSP and other headers, cookie
  flags, session renewal on sign-in, sign-out, open-redirect block, identical failed-login pages,
  API docs disabled, friendly errors.
* **Accessibility** (`test_accessibility.py`): for 3 public pages and 16 pages × 3 roles
  (including the forms with "who advised this", the cancel confirmation and the doctor summary): `lang`,
  title, exactly one `h1`, no skipped heading levels, every control labelled, every button and link
  has text, skip link first, one `main` landmark, labelled `nav`, `th` with `scope`. Error summary
  with `role="alert"` linked to fields, `aria-invalid` and `aria-describedby`, success messages in a
  `role="status"` live region. All 13 text/background colour pairs meet WCAG AA (4.5:1); controls
  are at least 48px tall; body text is 20px.
* **Decisions and advice** (`test_decisions_and_advice.py`): "doctor" without a name is refused
  with a field error, the request page shows the "In short" summary and who advised it, the elder
  names a decision-maker who then sees and approves the request, the decision-maker cannot decide
  their own request (button hidden and 403), helpers cannot choose a decision-maker, cancelling an
  appointment asks for confirmation, and the doctor visit summary shows real dose counts and
  approved changes with their advice.
* **Demo mode** (`test_demo_mode.py`): the demo circle is created once, with three people, three
  medicines, a pending doctor-advised request and taken / skipped / unanswered sample history;
  each "Sign in as ..." button signs in the right person; unknown roles are refused; and with
  demo mode off there is no demo panel, no banner, no demo route (404) and no demo circle.
* **Text size** (`test_text_size.py`): the Normal / Large / Extra large control is on the sign-in
  page, applies the choice, keeps it after signing in, ignores unknown values, needs CSRF, and the
  elder's own pages are larger by default.

## Browser accessibility audit (axe-core)

`scripts/accessibility_audit.py` starts the app with demo data, signs in as each role in headless
Chrome, runs axe-core with the WCAG 2.0/2.1/2.2 A and AA rule sets on 12 pages per role, then
repeats every page **at 320 CSS px wide with the Extra large text size** (WCAG 1.4.10 Reflow: the
page must not scroll sideways, and axe runs again), and checks with the keyboard that the first
Tab reaches the skip link and that it moves focus to the main content. It can save screenshots.
CI runs it on every push (`accessibility` job, bundled Chromium).

```bash
pip install playwright
npm install axe-core
python scripts/accessibility_audit.py --axe node_modules/axe-core/axe.min.js --screenshots shots/
```

Last result (15 Nov 2026, axe-core 4.x, Google Chrome): **0 problems across 72 page views**
(12 pages per role, at desktop width and at 320 px with Extra large text, including the request
page with its summary and the doctor visit summary). The first run of the 320 px check found 26
pages that scrolled sideways (wide tables and grid columns); they were fixed by putting each table
in its own labelled, keyboard-scrollable region and letting grid columns shrink, and
`test_every_table_is_in_a_named_keyboard_scrollable_region` keeps it that way.
A separate run in demo mode (landing page with the demo panel, elder home, doctor summary and
history) also found 0 violations.
Automated tools find only part of accessibility problems; the app has not yet been tested with a
screen reader by a person.

## What has NOT been verified

* **Live Gmail delivery.** The sender is tested against a fake SMTP server. To check a real send:
  put your Gmail address and App Password in `.env`, run `python scripts/seed_demo.py`, give a
  member your email address on the People page, start the app and wait for the next dose time; the
  Emails page shows "Sent" only after Gmail accepts the message.
* **The Firestore backend.** The Firestore emulator could not be downloaded in the build
  environment (network policy), so the 6 Firestore contract tests were skipped. To run them:

  ```bash
  npm install -g firebase-tools        # needs Java 11+
  firebase emulators:start --only firestore
  # in another terminal:
  set FIRESTORE_EMULATOR_HOST=127.0.0.1:8080      (Windows)
  export FIRESTORE_EMULATOR_HOST=127.0.0.1:8080   (macOS/Linux)
  python -m pytest tests/integration/test_repository_contract.py -o addopts= -v
  ```

* **The Docker image.** The build environment could not reach Docker Hub, so `docker build` was not
  run. The container's start command was run directly in production mode: it refused to start
  without a secret key, and with one it served `/healthz` and set a `Secure; HttpOnly; SameSite=Lax`
  session cookie.
* Screen-reader testing by a person, and testing on real phones.
