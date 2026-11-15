# CareLoop security

CareLoop holds health information, so each control below is implemented in code and covered by a
test. File and test names are given so each claim can be checked.

## Authentication

| Control | Implementation | Test |
|---|---|---|
| Per-person passcodes (no shared password) | `services/auth_service.py` | `tests/integration/test_auth.py` |
| Passcodes hashed with scrypt (n=2^14, r=8, p=1), random 16-byte salt, constant-time compare | `security/passcodes.py` | `tests/unit/test_permissions_and_passcodes.py` |
| Weak passcodes rejected (length < 6, repeated or sequential digits) | `domain/validation.py::validate_passcode` | `tests/unit/test_validation.py` |
| Same error for unknown circle, unknown name or wrong passcode; a dummy hash is verified for unknown names so timing is similar | `auth_service.sign_in` | `test_every_failure_gives_the_same_generic_error`, `test_failed_login_does_not_reveal_which_part_was_wrong` |
| Lockout for 15 minutes after 5 failed attempts | `auth_service.py` | `test_lockout_after_repeated_failures_then_expires` |
| Passcodes never echoed back into pages, never logged, never put in emails | routes/auth.py, notification_delivery.py | `test_failed_login_does_not_reveal_which_part_was_wrong`, `test_caretaker_alert_says_unconfirmed_is_not_missed` |

## Sessions and CSRF

* Signed session cookie (Starlette `SessionMiddleware`): `HttpOnly`, `SameSite=Lax`, 8-hour
  lifetime, `Secure` when `CARELOOP_SECURE_COOKIES=true`. It contains only the member id, circle id
  and CSRF token.
* The session is cleared and re-created on sign-in (prevents session fixation) and cleared on
  sign-out (`test_session_is_replaced_on_sign_in`, `test_sign_out_ends_the_session`).
* The member is re-loaded from storage on every request; removed members lose access immediately
  (`test_elder_removes_a_helper_and_their_session_ends`).
* Every POST, including sign-in, needs the session's CSRF token, compared in constant time
  (`web/security.verified_form`; `test_posts_without_a_valid_csrf_token_are_refused`,
  `test_login_form_itself_requires_csrf`).

## Authorization

* All checks are on the server: `domain/permissions.py` defines a role → action matrix and
  `authorize()` also requires the member to belong to the record's circle. Templates only hide
  buttons for convenience; the service still refuses.
* Only the elder can edit records directly, manage members, change timings and name a trusted
  decision-maker.
* Proposals can be decided by the elder or the one helper the elder trusted to decide on their
  behalf, and never by the person who proposed them, so no single helper can change a record alone
  (`authorize_decision`; `test_trusted_helper_cannot_approve_their_own_suggestion`,
  `test_decision_maker_cannot_decide_own_request_in_the_browser`). Such decisions are audited as
  made on the elder's behalf, and the elder can withdraw the trust at any time
  (`test_elder_can_take_the_trust_back`).
  Caretakers can record doses on the elder's behalf (attributed to them) and propose. Family can
  view and propose.
* Records are always looked up and then checked against the member's circle; another circle's id
  returns 404 (`test_records_of_another_circle_are_not_visible`,
  `test_proposals_cannot_target_another_circles_records`,
  `test_cannot_record_a_dose_from_another_circle`).
* Tests: `tests/unit/test_permissions_and_passcodes.py`, `tests/integration/test_proposal_flow.py`,
  `tests/web/test_management_routes.py::test_helpers_cannot_manage_people_or_settings`.

## Injection, XSS and other web risks

| Risk | Control |
|---|---|
| SQL injection | Every SQLite query uses bound parameters. The only formatted SQL builds column lists from code-defined record keys (`sqlite_repository.py`, commented). |
| XSS | Jinja2 autoescaping for all templates (`test_user_input_is_html_escaped`). The site has no JavaScript and the CSP sets `script-src 'none'`. |
| Clickjacking | `X-Frame-Options: DENY` and CSP `frame-ancestors 'none'`. |
| Open redirect | `safe_redirect_target` only allows same-site paths (`test_open_redirects_are_blocked`). |
| Caching of health data | `Cache-Control: no-store` on every page. |
| Information leaks | Generic error pages; unexpected exceptions are logged by type and path only. API docs (`/docs`, `/openapi.json`) are disabled (`test_api_docs_are_not_exposed`). |
| Input abuse | All input validated with length limits and control characters stripped (`domain/validation.py`). |

## Data integrity and audit

* Every important action writes an `AuditEvent` in the same transaction as the change (actor, role,
  action, entity, before/after values).
* SQLite triggers abort any `UPDATE` or `DELETE` on `audit_events`
  (`test_audit_log_is_append_only_in_sqlite`). The repository has no code path to change an event.
  In Firestore, `firestore.rules` denies all client access; the server account only creates events.
* Optimistic concurrency (record versions, proposal status) means concurrent edits are rejected,
  never silently overwritten (`test_stale_proposal_is_refused_after_record_changed`,
  `test_versioned_update_rejects_stale_writes_atomically`).

## Secrets

* All secrets come from environment variables (`config.py`); `.env` is git-ignored and
  `.env.example` has blank values.
* In production mode the app refuses to start without a 32+ character `CARELOOP_SECRET_KEY`
  (`tests/unit/test_config.py`). The Docker image runs as an unprivileged user in production mode.
* Gmail uses an App Password over implicit TLS (port 465). SMTP server replies are not stored,
  because they can contain addresses.

## Demo mode

`CARELOOP_DEMO_MODE=true` is for the public demo only. It creates a sample circle whose sign-ins
are published, so every page shows a "Demo version - do not enter real health information"
banner. The one-click demo sign-in route is only registered in demo mode (it returns 404
otherwise, `test_demo_features_do_not_exist_when_demo_mode_is_off`) and still goes through the
normal passcode check and lockout.

## Rate limiting and transport

* Each person is locked out for 15 minutes after 5 wrong passcodes (`AuthService`).
* On top of that, each network address may make at most 30 sign-in or sign-up attempts per 15
  minutes across all names and circles (`web/rate_limit.py`, configurable with
  `CARELOOP_SIGN_IN_ATTEMPTS_PER_15_MIN`), so circle code and name pairs cannot be sprayed. The
  limiter's memory is bounded. Viewing the sign-in page is never limited.
* Over HTTPS every response carries `Strict-Transport-Security`; pages are `Cache-Control:
  no-store` because they contain health information. Only static files are cached.

## Supply chain

* `pip-audit` checks runtime dependencies for known vulnerabilities on every push (CI job
  `security`); the last local run found none.
* GitHub CodeQL (`security-and-quality` queries) analyses the Python code on every push to `main`,
  every pull request and weekly.
* Dependabot opens weekly pull requests for outdated Python packages and GitHub Actions.
* ruff's bandit (`S`) rules run as part of linting.

## Threat model

| Threat | What an attacker might try | Mitigation | Verified by |
|---|---|---|---|
| Guessing a passcode | Many passcodes for one person | scrypt hashing, 5-try lockout, generic error | `test_auth.py`, `test_failed_login_does_not_reveal_which_part_was_wrong` |
| Spraying accounts | One passcode across many names/circles | Per-address rate limit | `test_sign_in_is_rate_limited_per_address_across_all_names` |
| Helper takes control | A caretaker changes medicines or approves their own request | Helpers can only propose; server-side role checks; nobody approves their own suggestion | `test_only_the_elder_can_decide`, `test_trusted_helper_cannot_approve_their_own_suggestion` |
| Reading another circle | Guessing ids in URLs | Every lookup is scoped to the signed-in member's circle | `test_records_of_another_circle_are_not_visible` |
| Cross-site request forgery | A malicious page submits a form | CSRF token on every POST, SameSite cookies | `test_posts_without_a_valid_csrf_token_are_refused`, `test_login_form_itself_requires_csrf` |
| Script injection | Medicine names with HTML or script | Autoescaping, CSP `script-src 'none'` | `test_user_input_is_html_escaped`, `test_security_headers_and_cookie_flags` |
| Session theft or fixation | Reusing an old session cookie | Signed HttpOnly cookies, new session on sign-in, HSTS over HTTPS | `test_session_is_replaced_on_sign_in`, `test_hsts_is_sent_only_over_https` |
| Tampering with history | Editing or deleting audit entries | Append-only audit table enforced by database triggers | `test_audit_log_is_append_only_in_sqlite` |
| Lost update | Two people change one medicine at once | Optimistic concurrency (version check in one transaction) | `test_versioned_update_rejects_stale_writes_atomically`, `test_stale_proposal_is_refused_after_record_changed` |
| Leaking health data | Logs, caches or error pages | No health data or secrets in logs; `no-store` pages; generic error pages | `test_logs_never_contain_health_data_or_passcodes`, `test_malformed_input_gets_a_friendly_error_not_a_stack_trace` |
| Vulnerable dependency | A known CVE in a library | pip-audit in CI, Dependabot | CI `security` job |

## Known gaps

* The rate limiter keeps its counts in memory, so with several server processes each enforces
  its own limit, and counts reset on restart.
* The circle code plus a member's name identifies the account; the code should be shared only with
  the circle.
* No multi-factor authentication or self-service passcode reset.
* HTTPS is expected to be provided by the hosting platform.
* Emails necessarily include the medicine name so a helper knows what to check; they do not include
  dosage history or passcodes.

## Reporting a problem

Please open a private issue or contact the maintainer rather than filing a public issue.
