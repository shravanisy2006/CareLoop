# Requirements mapping

How each part of the challenge and each evaluation criterion is met, where the code is, and how it
is verified. Test names are in `tests/`; run `python -m pytest` to verify all of them.

## Official challenge

| Challenge requirement | Implementation | Relevant files | Verification |
|---|---|---|---|
| Shared profile for elders, caretakers and family | A care circle per elder with three roles | `domain/models.py` (`Circle`, `Member`, `Role`), `services/circle_service.py` | `test_auth.py`, `test_management_routes.py` |
| Secure, passcode-based access | Per-person scrypt-hashed passcodes, lockout, signed sessions, CSRF | `services/auth_service.py`, `security/passcodes.py`, `web/security.py` | `test_auth.py`, `test_security.py` |
| View, add, edit, manage medication schedules | Elder edits directly; helpers propose; doses regenerated on change | `services/record_service.py`, `web/routes/medications.py` | `test_proposal_flow.py`, `test_web_flows.py` |
| Track medication adherence | One record per dose; honest summary | `services/dose_service.py`, `domain/adherence.py` | `test_escalation_flow.py`, `test_schedule_and_adherence.py` |
| Monitor upcoming doctor appointments | Appointments with history, reminders, calendar links | `web/routes/appointments.py`, `services/care_cycle.py` | `test_doses_and_reminders.py::test_appointment_reminder_is_queued_once_within_24_hours` |
| Email reminders for upcoming doses, missed doses, appointments | Gmail SMTP sender, escalation emails, appointment reminders, delivery status | `notifications/`, `services/notification_delivery.py` | `test_escalation_flow.py`, `test_email_sender.py` |
| Beyond a basic reminder app | Escalation ladder, consent-based changes, append-only audit | `domain/escalation.py`, `domain/changes.py`, `storage/sqlite_schema.sql` | `test_escalation_rules.py`, `test_proposal_flow.py`, `test_repository_contract.py` |
| Elder at the centre of decisions | Only the elder can approve changes, manage people and set timings - or one trusted helper the elder names, never on their own suggestion. Every request says who advised it and is explained in plain words. | `domain/permissions.py`, `services/proposal_service.py`, `domain/proposal_summary.py` | `test_only_the_elder_can_decide`, `test_trusted_helper_cannot_approve_their_own_suggestion`, `test_request_page_shows_plain_summary_and_who_advised` |
| Adherence records for the doctor | Printable doctor visit summary from real dose records | `web/routes/history.py`, `templates/history/report.html` | `test_doctor_visit_summary_reports_real_doses_and_approved_changes` |

## Evaluation criteria

| Evaluation criterion | Implementation | Relevant files | Verification |
|---|---|---|---|
| **Code quality** | Layered design: pure domain rules, services, repository contract with two backends, thin routes. Typed dataclasses and enums, one ChangeSet builder per change type, docstrings explaining intent. | `careloop/domain/`, `careloop/services/`, `careloop/storage/repository.py`, `ARCHITECTURE.md` | `ruff check .` and `ruff format --check .` pass (pyflakes, bugbear, isort, pyupgrade, bandit rules); `mypy` passes on 66 files; CI runs all of it on Python 3.10 and 3.12; 233 of 239 tests pass, 6 Firestore tests skipped |
| **Security** | scrypt hashing, no one can approve their own proposal, generic errors and lockout, server-side role checks plus circle isolation, CSRF on every POST, signed HttpOnly SameSite cookies with renewal, CSP with no scripts, escaping, parameterised SQL, append-only audit, secrets from environment | `security/`, `web/security.py`, `domain/permissions.py`, `storage/sqlite_schema.sql`, `SECURITY.md` | `tests/web/test_security.py` (25), `test_auth.py` (10), `test_permissions_and_passcodes.py` (19) |
| **Efficiency** | Indexed queries for every access path; deterministic ids and conditional updates instead of locks; doses generated only 48 h ahead; one cycle handles all circles; no JavaScript or frameworks on the client; per-request work is a handful of indexed reads | `storage/sqlite_schema.sql` (indexes), `firestore.indexes.json`, `services/care_cycle.py` | `test_dose_generation_is_idempotent`, `test_repeated_cycles_never_send_duplicate_emails` |
| **Testing** | 239 tests across unit, integration (real SQLite, threads) and HTTP levels; repository contract shared by both backends; 87% line coverage (about 95% excluding the untested Firestore module) | `tests/`, `TESTING.md` | `python -m pytest --cov=careloop` |
| **Accessibility** | 20px text (larger on the elder's pages), a text-size control (Normal / Large / Extra large) usable before sign-in, 48px+ controls, WCAG AA colours, visible focus, skip link, landmarks, labels and hints tied to inputs, error summary, live regions, plain language, no JavaScript, works without a mouse | `web/templates/`, `web/static/careloop.css` | `tests/web/test_accessibility.py` (22 tests over 42 page renders), `tests/web/test_text_size.py` (6); axe-core: 0 violations on 36 page views; `scripts/accessibility_audit.py` |
| **Problem statement alignment** | Closed loop (every dose resolved or honestly closed), escalation to caretaker then family, elder approval of every change, history visible to the whole circle | `domain/escalation.py`, `services/proposal_service.py`, `web/templates/dashboard_elder.html` | `test_escalation_climbs_elder_then_caretaker_then_family`, `test_caretaker_change_waits_for_elder_approval` |
| **Google services** | Gmail SMTP for all notifications; Cloud Firestore backend with transactions, indexes and rules; Google Calendar event links; Dockerfile for Cloud Run (not yet built or deployed) | `notifications/email_sender.py`, `storage/firestore_repository.py`, `firestore.indexes.json`, `firestore.rules`, `integrations/google_calendar.py` | `test_email_sender.py`, `test_google_calendar_link_has_utc_times_and_title`; Firestore contract tests run with the emulator (not yet run, see `TESTING.md`) |

## Safety constraints

| Constraint | How it is met | Verification |
|---|---|---|
| No prescribing or changing dosages | Dosage is free text stored exactly as entered; no code computes or alters it | `test_valid_medication_is_cleaned_and_times_sorted` |
| Schedules only from authorised people | Only the elder writes directly; others' changes need approval | `test_caregivers_cannot_edit_records_directly` |
| Unknown adherence shown honestly | `UNCONFIRMED` is "no answer recorded", counted separately from taken and skipped | `test_unconfirmed_doses_are_reported_as_unknown_not_missed`, `test_window_closes_as_unconfirmed_not_missed` |
| No simulated email success | Missing credentials → `not_configured`; `sent` only after Gmail accepts | `test_unconfigured_email_is_recorded_honestly`, `test_missing_credentials_never_pretend_to_send` |
