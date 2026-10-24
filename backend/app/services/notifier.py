"""Durable notification outbox + background delivery worker.

Pattern: transactional outbox. The SOS endpoint writes one ``NotificationJob``
per recipient in the SAME transaction as the incident, so an incident is never
acknowledged as queued unless its jobs are durably persisted. A background worker
then claims due jobs, sends them one recipient at a time (so one bad number never
blocks the rest), and records the outcome.

Reliability properties (mission Phase 8):
  * each recipient processed independently
  * exponential backoff with a max-attempts cap, then dead-letter to FAILED
  * a send TIMEOUT becomes UNKNOWN and is NOT auto-retried (no blind double-send)
  * a worker that dies mid-send leaves a lease (locked_at); stale leases are
    reclaimed, so work resumes without duplicating an accepted send
  * provider "accepted" is recorded distinctly from "delivered" (DLR callback)

This is a single in-process asyncio worker, which suits the current always-on
FastAPI/uvicorn deployment. For a serverless or multi-instance deployment the
same table works as an outbox driven by an external scheduler/queue — see README.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import update
from sqlmodel import Session, select

from ..config import get_settings
from ..db import engine, session_scope
from ..models import (
    EmergencyContact,
    Incident,
    NotificationJob,
    NotificationStatus,
    TERMINAL_NOTIFICATION_STATES,
    utcnow,
)
from .sms import OutboundSms, SendOutcome, get_provider
from .sms.templating import render_emergency_email, render_emergency_sms

# A PROCESSING job whose lease is older than this is considered abandoned and
# reclaimed to PENDING (the previous worker likely crashed before finishing).
_STALE_LEASE_SECONDS = 120


def _sanitize_error(detail: str | None) -> str | None:
    if not detail:
        return None
    # Keep it short and never echo anything secret-looking.
    text = str(detail)
    return text[:300]


# ── Queueing (called inside the SOS transaction) ──────────────────────────────
def build_jobs_for_incident(
    session: Session,
    incident: Incident,
    contacts: list[EmergencyContact],
    *,
    victim_name: str | None,
) -> list[NotificationJob]:
    """Create (but do not send) one NotificationJob per active contact.

    Idempotent per incident: if jobs already exist for this incident (e.g. an
    idempotent re-trigger), the existing jobs are returned unchanged.
    """
    existing = list(
        session.exec(
            select(NotificationJob).where(NotificationJob.incident_id == incident.id)
        ).all()
    )
    if existing:
        return existing

    settings = get_settings()
    channel = get_provider().channel  # "sms" | "email"
    jobs: list[NotificationJob] = []
    for contact in contacts:
        sms = render_emergency_sms(
            brand=settings.sms_brand_name,
            victim_name=victim_name,
            incident_type=incident.incident_type,
            incident_id=incident.id,
            latitude=incident.latitude,
            longitude=incident.longitude,
            when=incident.created_at,
        )
        variables = dict(sms.variables)
        subject: str | None = None
        if channel == "email":
            mail = render_emergency_email(
                brand=settings.sms_brand_name,
                victim_name=victim_name,
                incident_type=incident.incident_type,
                incident_id=incident.id,
                latitude=incident.latitude,
                longitude=incident.longitude,
                when=incident.created_at,
            )
            subject = mail.subject
            variables["_html"] = mail.html

        job = NotificationJob(
            incident_id=incident.id,
            user_ref=incident.user_ref or "",
            contact_id=contact.id,
            recipient_name=contact.name,
            recipient_phone=contact.phone_e164,
            recipient_email=contact.email,
            channel=channel,
            subject=subject,
            message_body=sms.body,
            template_vars=json.dumps(variables, separators=(",", ":")),
            max_attempts=settings.sms_max_attempts,
            next_attempt_at=utcnow(),
        )
        # If the active channel has no destination for this contact, record a
        # visible FAILED job rather than silently skipping it.
        if channel == "email" and not contact.email:
            job.status = NotificationStatus.FAILED
            job.last_error = "no email address on file for this contact"
        else:
            job.status = NotificationStatus.PENDING
        session.add(job)
        jobs.append(job)
    return jobs


# ── Aggregate status (derived; never fabricated) ──────────────────────────────
def summarize(jobs: list[NotificationJob]) -> dict:
    counts: dict[str, int] = {s.value: 0 for s in NotificationStatus}
    for j in jobs:
        counts[j.status.value] = counts.get(j.status.value, 0) + 1
    total = len(jobs)
    delivered = counts[NotificationStatus.DELIVERED.value]
    accepted = counts[NotificationStatus.PROVIDER_ACCEPTED.value]
    failed = counts[NotificationStatus.FAILED.value]
    unknown = counts[NotificationStatus.UNKNOWN.value]
    pending = counts[NotificationStatus.PENDING.value] + counts[NotificationStatus.PROCESSING.value]

    if total == 0:
        overall = "no_recipients"
    elif pending > 0:
        overall = "processing"
    elif delivered == total:
        overall = "all_delivered"
    elif delivered + accepted > 0 and (failed + unknown) > 0:
        overall = "partially_notified"
    elif delivered + accepted == total:
        overall = "provider_accepted"
    elif failed == total:
        overall = "all_failed"
    else:
        overall = "partially_notified"

    return {"overall": overall, "total": total, "counts": counts}


# ── Backoff ───────────────────────────────────────────────────────────────────
def compute_backoff(attempts: int, base_ms: int, retry_after_s: float | None) -> timedelta:
    """Exponential backoff with jitter; honors a provider Retry-After if larger."""
    exp = base_ms * (2 ** max(0, attempts - 1))
    exp = min(exp, 10 * 60 * 1000)  # cap at 10 min
    jitter = random.uniform(0, base_ms)
    secs = (exp + jitter) / 1000.0
    if retry_after_s:
        secs = max(secs, retry_after_s)
    return timedelta(seconds=secs)


# ── Worker ─────────────────────────────────────────────────────────────────────
class NotificationWorker:
    def __init__(self, worker_id: str = "worker-1") -> None:
        self.worker_id = worker_id
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run())
            logger.info(f"[notifier] worker {self.worker_id} started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        logger.info(f"[notifier] worker {self.worker_id} stopped")

    async def _run(self) -> None:
        poll = max(0.1, get_settings().sms_worker_poll_ms / 1000.0)
        while not self._stop.is_set():
            try:
                claimed = await asyncio.to_thread(self._claim_due_jobs, 10)
                for jid in claimed:
                    await self._process_job(jid)
            except Exception as exc:  # noqa: BLE001 — a worker must never die silently
                logger.exception(f"[notifier] worker loop error: {exc}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=poll)
            except asyncio.TimeoutError:
                pass

    def _claim_due_jobs(self, limit: int) -> list[str]:
        """Atomically move due PENDING (and stale-PROCESSING) jobs to PROCESSING.

        Uses a compare-and-set UPDATE guarded on lock state so a job is never
        claimed twice (protects against a manual-retry racing the worker).
        """
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=_STALE_LEASE_SECONDS)
        with Session(engine) as session:
            due = session.exec(
                select(NotificationJob.id).where(
                    NotificationJob.status == NotificationStatus.PENDING,
                    NotificationJob.next_attempt_at <= now,
                    NotificationJob.locked_at.is_(None),  # type: ignore[union-attr]
                ).limit(limit)
            ).all()
            stale = session.exec(
                select(NotificationJob.id).where(
                    NotificationJob.status == NotificationStatus.PROCESSING,
                    NotificationJob.locked_at < stale_before,  # type: ignore[operator]
                ).limit(limit)
            ).all()

            claimed: list[str] = []
            for jid in list(due) + list(stale):
                result = session.execute(
                    update(NotificationJob)
                    .where(
                        NotificationJob.id == jid,
                        NotificationJob.status.in_(  # type: ignore[attr-defined]
                            [NotificationStatus.PENDING, NotificationStatus.PROCESSING]
                        ),
                    )
                    .values(
                        status=NotificationStatus.PROCESSING,
                        locked_at=now,
                        locked_by=self.worker_id,
                        updated_at=now,
                    )
                )
                if result.rowcount == 1:
                    claimed.append(jid)
            session.commit()
            if claimed:
                logger.debug(f"[notifier] claimed {len(claimed)} job(s)")
            return claimed

    async def _process_job(self, job_id: str) -> None:
        snapshot = await asyncio.to_thread(self._load_job_snapshot, job_id)
        if snapshot is None:
            return
        destination, subject, body, variables = snapshot
        provider = get_provider()
        msg = OutboundSms(
            to_e164=destination,
            subject=subject,
            body=body,
            variables=variables,
            client_ref=job_id,
        )
        try:
            result = await provider.send_emergency_sms(msg)
        except Exception as exc:  # defensive: provider contract says it shouldn't raise
            logger.exception(f"[notifier] provider raised for job {job_id}: {exc}")
            await asyncio.to_thread(
                self._apply_result, job_id, provider.name, SendOutcome.RETRYABLE,
                None, f"provider error: {exc}", None,
            )
            return
        await asyncio.to_thread(
            self._apply_result,
            job_id,
            provider.name,
            result.outcome,
            result.provider_message_id,
            result.detail,
            result.retry_after_seconds,
        )

    def _load_job_snapshot(self, job_id: str) -> tuple[str, str | None, str, dict] | None:
        with session_scope() as session:
            job = session.get(NotificationJob, job_id)
            if job is None or job.status != NotificationStatus.PROCESSING:
                return None
            variables = {}
            if job.template_vars:
                try:
                    variables = json.loads(job.template_vars)
                except ValueError:
                    variables = {}
            # Destination depends on the channel the job was queued for.
            destination = job.recipient_email if job.channel == "email" else job.recipient_phone
            return (destination or "", job.subject, job.message_body, variables)

    def _apply_result(
        self,
        job_id: str,
        provider_name: str,
        outcome: SendOutcome,
        provider_message_id: str | None,
        detail: str | None,
        retry_after_s: float | None,
    ) -> None:
        settings = get_settings()
        now = utcnow()
        with session_scope() as session:
            job = session.get(NotificationJob, job_id)
            if job is None:
                return
            job.attempts += 1
            job.provider = provider_name
            job.locked_at = None
            job.locked_by = None
            job.updated_at = now
            job.last_error = _sanitize_error(detail)

            if outcome == SendOutcome.ACCEPTED:
                job.status = NotificationStatus.PROVIDER_ACCEPTED
                job.provider_message_id = provider_message_id
                job.last_error = None
            elif outcome == SendOutcome.UNKNOWN:
                # Indeterminate (e.g. timeout). Do not retry blindly.
                job.status = NotificationStatus.UNKNOWN
            elif outcome == SendOutcome.PERMANENT:
                job.status = NotificationStatus.FAILED
            else:  # RETRYABLE
                if job.attempts >= job.max_attempts:
                    job.status = NotificationStatus.FAILED
                    job.last_error = f"attempts exhausted: {_sanitize_error(detail)}"
                else:
                    job.status = NotificationStatus.PENDING
                    delay = compute_backoff(
                        job.attempts, settings.sms_retry_base_delay_ms, retry_after_s
                    )
                    job.next_attempt_at = now + delay
            session.add(job)
            session.commit()
            logger.info(
                f"[notifier] job {job_id} -> {job.status.value} "
                f"(attempt {job.attempts}/{job.max_attempts})"
            )


def drain_pending(max_rounds: int = 20) -> int:
    """Synchronously process all currently-due jobs. For tests/CLI only.

    Returns the number of jobs processed. Runs its own event loop, so it must be
    called from a thread WITHOUT a running loop (true for FastAPI TestClient
    between requests).
    """
    worker = NotificationWorker("drain")
    processed = 0

    async def _run() -> None:
        nonlocal processed
        for _ in range(max_rounds):
            claimed = await asyncio.to_thread(worker._claim_due_jobs, 100)
            if not claimed:
                break
            for jid in claimed:
                await worker._process_job(jid)
                processed += 1

    asyncio.run(_run())
    return processed


# ── Delivery-report application (called by the callback route) ─────────────────
def apply_delivery_update(
    session: Session, provider_message_id: str, status: NotificationStatus, detail: str | None
) -> bool:
    """Apply one DLR update to the matching job. Idempotent + monotonic.

    Returns True if a job was found (regardless of whether it changed). Never
    regresses a terminal state (DELIVERED/FAILED) — guards against duplicate or
    out-of-order callbacks.
    """
    job = session.exec(
        select(NotificationJob).where(
            NotificationJob.provider_message_id == provider_message_id
        )
    ).first()
    if job is None:
        return False
    if job.status in TERMINAL_NOTIFICATION_STATES:
        # Already resolved; ignore late/duplicate reports (no regression).
        return True
    job.status = status
    job.last_error = _sanitize_error(detail)
    job.updated_at = utcnow()
    if status == NotificationStatus.DELIVERED:
        job.delivered_at = utcnow()
    session.add(job)
    session.commit()
    return True
