# CareLoop architecture

## Layers

```
 Browser (HTML forms, no JavaScript)
        |
 careloop/web         FastAPI routes, Jinja templates, CSRF, sessions, security headers
        |
 careloop/services    use cases; every method takes the signed-in Member and checks permission
        |
 careloop/domain      pure rules: permissions, escalation, schedule, adherence, validation, ChangeSet builders
        |
 careloop/storage     Repository contract -> SqliteRepository | FirestoreRepository
        |
 careloop/notifications + integrations   Gmail SMTP sender, email text, Google Calendar links
```

Rules live in `domain/` and depend on nothing else, so they are unit-tested without a server or
database. Services combine rules with storage. Routes only parse forms, call a service and render.

## Data model

| Entity | Key fields | Notes |
|---|---|---|
| `Circle` | id, code, elder_name, timezone, escalation policy, decision_maker_id | One elder's care circle; `decision_maker_id` is the helper the elder trusts to decide (optional) |
| `Member` | id, circle_id, display_name, role, email, passcode_hash, failed_attempts, locked_until, active | Name unique per circle |
| `Medication` | id, circle_id, name, dosage, instructions, times, start/end date, status, **version** | Version used for optimistic concurrency |
| `Dose` | id, medication_id, scheduled_for, status, escalation_level, resolved_at/by/role | One row per dose |
| `Appointment` | id, circle_id, doctor, purpose, location, starts_at, status, **version** | |
| `Proposal` | id, kind, target_id, base_version, proposed details, reason, advice (source, advised_by, advised_on), status, proposed_by, decided_by/at | Never edits a record by itself |
| `Notification` | id = hash(kind, ref_key, recipient), status, attempts, last_error, sent_at | Deterministic id prevents duplicates |
| `AuditEvent` | actor, actor_role, action, entity, before, after, created_at | Append-only |

SQLite constraints (`careloop/storage/sqlite_schema.sql`): `CHECK` constraints on every status and
role, a check that a taken/skipped dose records who and when, a partial unique index allowing one
live dose per medication per time, a unique `(kind, ref_key, recipient)` for notifications, a
unique `(circle_id, name_key)` for members, indexes for each query path, and triggers that abort
any `UPDATE` or `DELETE` on `audit_events`.

Columns added after the first version (`decision_maker_id`, the three advice columns) are added to
existing SQLite databases at start-up (`SqliteRepository.ADDED_COLUMNS`), so an existing
`careloop.db` keeps working (`test_existing_database_is_upgraded_in_place`).

Firestore stores the same flat records (`careloop/storage/records.py`); composite indexes are in
`firestore.indexes.json`, and `firestore.rules` denies all client access.

## Dose lifecycle

```
                 +--------------------------------------------+
                 |  answer recorded (elder, or caretaker)      v
 SCHEDULED --(level 0..3 as time passes)--> TAKEN / SKIPPED
     |                                           ^
     | close_after minutes, no answer            | late answer
     v                                           |
 UNCONFIRMED ------------------------------------+
     ("no answer recorded" - unknown, not missed)

 SCHEDULED --(medication changed or stopped before the dose)--> CANCELLED
```

* Doses are generated for the next 48 hours, idempotently (deterministic ids that include the
  medication version) and never back-dated before the medication was added or changed.
* A change to a medication cancels its future doses and regenerates them from the new details in
  the same request.

## Escalation and the race with confirmation

`plan_escalation(dose, policy, now)` (pure) decides one of: nothing, climb one level, or close.
`CareCycle` then calls `repo.advance_escalation(dose_id, from_level, to_level, notifications, audit)`,
which updates the dose **only if it is still `scheduled` at `from_level`** and queues the emails in the
same transaction. So:

* If the elder confirms after the cycle read the dose, the conditional update matches nothing and no
  email is queued (`test_confirmation_during_escalation_wins`).
* Confirming a dose cancels any of its emails still `pending` or `failed`, and the sender re-checks
  the dose before sending (`test_queued_alert_is_cancelled_if_dose_is_confirmed_before_sending`).
* Running the cycle twice, or in two processes, cannot double-send: level changes are conditional
  and notification ids are deterministic (`test_repeated_cycles_never_send_duplicate_emails`).
* After downtime the ladder climbs one level per cycle, and a dose whose window is over is closed
  instead of triggering a burst of stale alerts.

## Consent-based change management

```
caretaker/family --propose (+ who advised it)--> Proposal(PENDING)
                 + plain-language email to the elder and the trusted decision-maker (if any)
                 + audit
                                   |
   elder (or trusted helper) approves --+-- they decline / proposer withdraws
                 |                                   |
  ChangeSet: proposal->APPROVED                ChangeSet: proposal->REJECTED/CANCELLED
             + record change (version check)
             + cancel future doses
             + audit (before/after)
  applied by repo.apply_changeset() in ONE transaction
```

Who may decide is one rule in `domain/permissions.py::authorize_decision`: the elder, or the single
helper the elder named as trusted decision-maker, and never the person who made the proposal. A
helper's decision is audited with `on_behalf_of_elder: true`. Only the elder can name or remove
the decision-maker, and removing that helper from the circle clears it.

`domain/proposal_summary.py` turns a proposal into one everyday sentence ("Ravi suggests changing
Metformin: the dose from "1 tablet" to "2 tablets"... Advised by Dr. Shah on 14 Nov 2026.") used on
the request page, the request list and the email, so the decider sees what changes and who advised
it before the detailed comparison table.

`domain/changes.py` builds the same ChangeSet whether the elder edits directly or approves a
proposal, so there is one definition of each change. Approval is refused if the record's version
moved since the proposal was made (`ConcurrencyConflict`), so the elder never approves a change
against details that are no longer current.

## Background work

`CareCycleRunner` runs `CareCycle.run_once()` in a daemon thread every `CARELOOP_CYCLE_SECONDS`
(started and stopped with the FastAPI lifespan). Each run: generate doses → escalate/close due
doses → queue appointment reminders (24 h ahead) → deliver queued emails. A lock prevents
overlapping runs in one process; the conditional updates make multiple processes safe.

## Request flow example: "I took it"

1. `POST /doses/{id}/record` with the form's CSRF token (`web/security.verified_form`).
2. `get_viewer` reloads the member from storage (removed members are signed out immediately).
3. `DoseService.record` checks the dose belongs to the member's circle, the role may record, the
   dose is resolvable and not more than an hour early.
4. `repo.resolve_dose` updates it conditionally, cancels unsent alerts and writes the audit event
   in one transaction.
5. The route redirects home with a confirmation message announced by a `role="status"` live region.

## Reports

`/report` is a printable doctor visit summary built from the same dose records: per-medicine
taken / skipped / no-answer counts (`DoseService.adherence_by_medication`), changes approved in the
period with who advised and who approved them, and the next appointments. A print stylesheet hides
navigation so it prints on plain paper.

## Delivery

* `.github/workflows/ci.yml` runs ruff (lint and format), mypy and pytest with coverage on Python
  3.10 and 3.12 for every push and pull request.
* `Dockerfile` packages the app for Cloud Run: unprivileged user, production mode (refuses to start
  without a secret key), Secure cookies, port taken from `$PORT`.
