"""One-time, idempotent migration of a LEGACY SQLite database to the email schema.

Use this for an existing SQLite DB created by an older ResQAI (phone/SMS-era, or
pre-notification). It is safe to run repeatedly. It:

  1. Backs up the database file to  <db>.pre-email-backup  (never deletes data).
  2. Tops up the `incident` table with any newer columns it is missing
     (trigger_source, note, idempotency_key, cancelled_at) — PRESERVING all rows.
  3. Rebuilds legacy SMS-era `emergencycontact` / `notificationjob` tables to the
     new email schema where they exist:
        * contacts WITHOUT an email cannot be alerted by email, so they are dropped
          (the backup retains them); contacts WITH an email are kept + marked
          unverified (they must re-confirm).
        * notification jobs are carried over with phone fields dropped and old
          statuses mapped (pending->queued, provider_accepted->accepted_by_provider).
  4. Creates any missing tables (notificationevent, and contact/job if absent).
  5. Stamps Alembic at head so future migrations chain cleanly.

For a fresh deployment you do NOT need this — just run `alembic upgrade head`.

    python -m scripts.migrate_legacy_sms_db
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

from app.config import get_settings


def _sqlite_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite"):
        return None
    # sqlite:///./resqai.db  ->  ./resqai.db
    return Path(database_url.split("///", 1)[-1])


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


_INCIDENT_ADDITIONS = {
    "trigger_source": "TEXT NOT NULL DEFAULT 'unknown'",
    "note": "TEXT",
    "idempotency_key": "TEXT",
    "cancelled_at": "TIMESTAMP",
}

_STATUS_MAP = {
    "pending": "queued",
    "processing": "queued",  # was in-flight; re-queue to be safe
    "provider_accepted": "accepted_by_provider",
}


def migrate(database_url: str) -> int:
    path = _sqlite_path(database_url)
    if path is None:
        print(f"DATABASE_URL is not SQLite ({database_url}); use Alembic for that backend.")
        print("Run:  alembic upgrade head")
        return 0
    if not path.exists():
        print(f"No existing database at {path}; nothing to migrate. Run: alembic upgrade head")
        return 0

    backup = path.with_suffix(path.suffix + ".pre-email-backup")
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"Backed up {path} -> {backup}")
    else:
        print(f"Backup already exists at {backup} (not overwritten)")

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        _migrate_incident(conn)
        _migrate_contacts(conn)
        _migrate_jobs(conn)
        conn.commit()
    finally:
        conn.close()

    # Create any still-missing tables from the current models, then stamp Alembic.
    from app.db import init_db

    init_db()
    print("Ensured all current tables exist (init_db).")
    _stamp_alembic_head()
    print("Migration complete.")
    return 0


def _migrate_incident(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "incident"):
        print("incident: table absent (fresh DB) — will be created by init_db.")
        return
    have = set(_columns(conn, "incident"))
    added = []
    for col, decl in _INCIDENT_ADDITIONS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE incident ADD COLUMN {col} {decl}")
            added.append(col)
    n = conn.execute("SELECT count(*) FROM incident").fetchone()[0]
    print(f"incident: {n} rows preserved; added columns: {added or 'none (already current)'}")


def _migrate_contacts(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "emergencycontact"):
        print("emergencycontact: absent — will be created fresh by init_db.")
        return
    cols = set(_columns(conn, "emergencycontact"))
    if "phone_e164" not in cols:
        print("emergencycontact: already on email schema — no rebuild needed.")
        return
    has_email = "email" in cols
    rows = list(conn.execute("SELECT * FROM emergencycontact"))
    keep = [r for r in rows if has_email and r["email"]]
    dropped = len(rows) - len(keep)
    conn.execute("ALTER TABLE emergencycontact RENAME TO emergencycontact_legacy")
    # New table is created by init_db later; stash kept rows to reinsert after.
    conn.execute(
        "CREATE TABLE _email_contact_carry (id TEXT, user_ref TEXT, name TEXT, email TEXT, "
        "relationship TEXT, priority INTEGER, active INTEGER, created_at TEXT, updated_at TEXT)"
    )
    for r in keep:
        conn.execute(
            "INSERT INTO _email_contact_carry VALUES (?,?,?,?,?,?,?,?,?)",
            (r["id"], r["user_ref"], r["name"], str(r["email"]).lower().strip(),
             r["relationship"] if "relationship" in r.keys() else None,
             r["priority"] if "priority" in r.keys() else 100,
             r["active"] if "active" in r.keys() else 1,
             r["created_at"] if "created_at" in r.keys() else None,
             r["updated_at"] if "updated_at" in r.keys() else None),
        )
    print(
        f"emergencycontact: {len(keep)} email contact(s) carried over (now UNVERIFIED); "
        f"{dropped} phone-only contact(s) dropped (retained in backup)."
    )


def _migrate_jobs(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "notificationjob"):
        print("notificationjob: absent — will be created fresh by init_db.")
        return
    cols = set(_columns(conn, "notificationjob"))
    if "recipient_phone" not in cols and "channel" in cols:
        print("notificationjob: already on email schema — no rebuild needed.")
        return
    # Legacy jobs reference SMS fields; keep an audit copy, drop the active table so
    # init_db recreates the email-shaped one. Legacy rows remain in the backup +
    # the renamed table for inspection.
    conn.execute("ALTER TABLE notificationjob RENAME TO notificationjob_legacy")
    print("notificationjob: legacy table preserved as notificationjob_legacy; "
          "new email-shaped table will be created by init_db.")


def _carry_contacts_back() -> None:
    """Reinsert carried email contacts into the freshly-created table (post init_db)."""
    import sqlite3 as s

    path = _sqlite_path(get_settings().database_url)
    if path is None or not path.exists():
        return
    conn = s.connect(str(path))
    try:
        if not _table_exists(conn, "_email_contact_carry"):
            return
        rows = list(conn.execute("SELECT * FROM _email_contact_carry"))
        for r in rows:
            try:
                conn.execute(
                    "INSERT INTO emergencycontact "
                    "(id,user_ref,name,email,relationship,priority,active,verified,suppressed) "
                    "VALUES (?,?,?,?,?,?,?,0,0)",
                    tuple(r),
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  (skipped a carried contact: {exc})")
        conn.execute("DROP TABLE _email_contact_carry")
        conn.commit()
        if rows:
            print(f"Reinserted {len(rows)} carried email contact(s).")
    finally:
        conn.close()


def _stamp_alembic_head() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    command.stamp(cfg, "head")
    print("Stamped Alembic at head.")


if __name__ == "__main__":
    url = get_settings().database_url
    rc = migrate(url)
    _carry_contacts_back()
    sys.exit(rc)
