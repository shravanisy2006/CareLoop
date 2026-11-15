-- CareLoop SQLite schema. Applied idempotently at start-up.
-- All timestamps are UTC ISO-8601 strings with microseconds, so text order
-- equals time order.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS circles (
    id                       TEXT PRIMARY KEY,
    code                     TEXT NOT NULL UNIQUE,
    elder_name               TEXT NOT NULL,
    timezone                 TEXT NOT NULL,
    caretaker_after_minutes  INTEGER NOT NULL CHECK (caretaker_after_minutes >= 5),
    family_after_minutes     INTEGER NOT NULL,
    close_after_minutes      INTEGER NOT NULL CHECK (close_after_minutes <= 720),
    created_at               TEXT NOT NULL,
    CHECK (family_after_minutes > caretaker_after_minutes),
    CHECK (close_after_minutes > family_after_minutes)
);

CREATE TABLE IF NOT EXISTS members (
    id               TEXT PRIMARY KEY,
    circle_id        TEXT NOT NULL REFERENCES circles(id),
    display_name     TEXT NOT NULL,
    name_key         TEXT NOT NULL,
    role             TEXT NOT NULL CHECK (role IN ('elder', 'caretaker', 'family')),
    email            TEXT,
    passcode_hash    TEXT NOT NULL,
    failed_attempts  INTEGER NOT NULL DEFAULT 0,
    locked_until     TEXT,
    active           INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at       TEXT NOT NULL,
    UNIQUE (circle_id, name_key)
);
CREATE INDEX IF NOT EXISTS idx_members_circle ON members(circle_id);

CREATE TABLE IF NOT EXISTS medications (
    id            TEXT PRIMARY KEY,
    circle_id     TEXT NOT NULL REFERENCES circles(id),
    name          TEXT NOT NULL,
    dosage        TEXT NOT NULL,
    instructions  TEXT NOT NULL DEFAULT '',
    times_json    TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    status        TEXT NOT NULL CHECK (status IN ('active', 'stopped')),
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    CHECK (end_date IS NULL OR end_date >= start_date)
);
CREATE INDEX IF NOT EXISTS idx_medications_circle ON medications(circle_id, status);

CREATE TABLE IF NOT EXISTS appointments (
    id           TEXT PRIMARY KEY,
    circle_id    TEXT NOT NULL REFERENCES circles(id),
    doctor_name  TEXT NOT NULL,
    purpose      TEXT NOT NULL,
    location     TEXT NOT NULL DEFAULT '',
    starts_at    TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL CHECK (status IN ('scheduled', 'cancelled')),
    version      INTEGER NOT NULL CHECK (version >= 1),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_appointments_circle ON appointments(circle_id, starts_at);
CREATE INDEX IF NOT EXISTS idx_appointments_start ON appointments(status, starts_at);

CREATE TABLE IF NOT EXISTS doses (
    id                TEXT PRIMARY KEY,
    circle_id         TEXT NOT NULL REFERENCES circles(id),
    medication_id     TEXT NOT NULL REFERENCES medications(id),
    scheduled_for     TEXT NOT NULL,
    status            TEXT NOT NULL
                      CHECK (status IN ('scheduled', 'taken', 'skipped', 'unconfirmed', 'cancelled')),
    escalation_level  INTEGER NOT NULL DEFAULT 0 CHECK (escalation_level BETWEEN 0 AND 3),
    resolved_at       TEXT,
    resolved_by       TEXT REFERENCES members(id),
    resolved_by_role  TEXT CHECK (resolved_by_role IS NULL
                                  OR resolved_by_role IN ('elder', 'caretaker', 'family')),
    created_at        TEXT NOT NULL,
    -- A dose is only "taken" or "skipped" if someone recorded it.
    CHECK (status NOT IN ('taken', 'skipped') OR (resolved_at IS NOT NULL AND resolved_by IS NOT NULL))
);
-- At most one live (not cancelled) dose per medication per time.
CREATE UNIQUE INDEX IF NOT EXISTS uq_doses_live
    ON doses(medication_id, scheduled_for) WHERE status != 'cancelled';
CREATE INDEX IF NOT EXISTS idx_doses_circle_time ON doses(circle_id, scheduled_for);
CREATE INDEX IF NOT EXISTS idx_doses_open ON doses(status, scheduled_for);

CREATE TABLE IF NOT EXISTS proposals (
    id                TEXT PRIMARY KEY,
    circle_id         TEXT NOT NULL REFERENCES circles(id),
    kind              TEXT NOT NULL CHECK (kind IN (
                          'add_medication', 'change_medication', 'stop_medication',
                          'add_appointment', 'change_appointment', 'cancel_appointment')),
    target_id         TEXT,
    base_version      INTEGER,
    payload_json      TEXT NOT NULL,
    reason            TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    proposed_by       TEXT NOT NULL REFERENCES members(id),
    proposed_by_role  TEXT NOT NULL CHECK (proposed_by_role IN ('caretaker', 'family')),
    created_at        TEXT NOT NULL,
    decided_by        TEXT REFERENCES members(id),
    decided_at        TEXT,
    decision_note     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_proposals_circle ON proposals(circle_id, status, created_at);

CREATE TABLE IF NOT EXISTS notifications (
    id                   TEXT PRIMARY KEY,  -- hash of (kind, ref_key, recipient)
    circle_id            TEXT NOT NULL REFERENCES circles(id),
    kind                 TEXT NOT NULL,
    ref_key              TEXT NOT NULL,
    recipient_member_id  TEXT NOT NULL REFERENCES members(id),
    recipient_email      TEXT,
    subject              TEXT NOT NULL,
    body                 TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN (
                             'pending', 'sent', 'failed', 'gave_up',
                             'not_configured', 'no_email', 'cancelled')),
    attempts             INTEGER NOT NULL DEFAULT 0,
    last_error           TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL,
    sent_at              TEXT,
    UNIQUE (kind, ref_key, recipient_member_id)
);
CREATE INDEX IF NOT EXISTS idx_notifications_delivery ON notifications(status, created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_circle ON notifications(circle_id, created_at);

CREATE TABLE IF NOT EXISTS audit_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    circle_id        TEXT NOT NULL REFERENCES circles(id),
    actor_member_id  TEXT REFERENCES members(id),
    actor_role       TEXT NOT NULL CHECK (actor_role IN ('elder', 'caretaker', 'family', 'system')),
    action           TEXT NOT NULL,
    entity_type      TEXT NOT NULL,
    entity_id        TEXT NOT NULL,
    before_json      TEXT,
    after_json       TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_circle ON audit_events(circle_id, id);

-- The audit log is append-only: the database itself refuses edits and deletes.
CREATE TRIGGER IF NOT EXISTS audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;
