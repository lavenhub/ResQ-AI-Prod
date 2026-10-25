/**
 * Typed client for the backend emergency EMAIL pipeline:
 *   - emergency contacts CRUD + verification  (/api/contacts)
 *   - SOS trigger + incident status + retry/cancel (/api/emergency/*)
 *   - email provider health        (/api/email/health)
 *
 * All calls go through apiFetch, which attaches the session bearer token.
 * No provider secrets ever live here — the browser only talks to our backend.
 */

import { apiFetch } from "@/lib/api";

export type NotificationStatus =
  | "queued"
  | "processing"
  | "accepted_by_provider"
  | "delivered"
  | "deferred"
  | "bounced"
  | "failed"
  | "unknown"
  | "cancelled"
  | "pending";

export interface Contact {
  id: string;
  name: string;
  email: string;
  relationship: string | null;
  priority: number;
  active: boolean;
  verified: boolean;
  verified_at: string | null;
  suppressed: boolean;
  suppressed_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface NotificationRead {
  id: string;
  incident_id: string;
  recipient_name: string | null;
  recipient_email: string;
  channel: string;
  status: NotificationStatus;
  attempts: number;
  max_attempts: number;
  provider: string | null;
  provider_message_id: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  delivered_at: string | null;
}

export interface SosResponse {
  incident_id: string;
  created: boolean;
  status: string;
  location_available: boolean;
  notifications: NotificationRead[];
  notification_summary: { overall: string; total: number; counts: Record<string, number> };
}

export interface EmailHealth {
  provider: string;
  channel: string;
  ready: boolean;
  isMock: boolean;
  problems: string[];
  realSendingConfigured: boolean;
}

export interface VerificationStartResponse {
  contact_id: string;
  sent: boolean;
  dev_verification_url: string | null;
  detail: string | null;
}

// ── Contacts ──────────────────────────────────────────────────────────────────
export const listContacts = () => apiFetch<Contact[]>("/api/contacts");

export const createContact = (c: {
  name: string;
  email: string;
  relationship?: string;
  priority?: number;
  active?: boolean;
}) => apiFetch<Contact>("/api/contacts", { method: "POST", body: JSON.stringify(c) });

export const updateContact = (
  id: string,
  patch: Partial<{
    name: string;
    email: string;
    relationship: string;
    priority: number;
    active: boolean;
  }>,
) => apiFetch<Contact>(`/api/contacts/${id}`, { method: "PATCH", body: JSON.stringify(patch) });

export const deleteContact = (id: string) =>
  apiFetch<void>(`/api/contacts/${id}`, { method: "DELETE" });

export const sendContactVerification = (id: string) =>
  apiFetch<VerificationStartResponse>(`/api/contacts/${id}/send-verification`, { method: "POST" });

// ── SOS ─────────────────────────────────────────────────────────────────────
export const triggerSos = (body: {
  incident_type?: string;
  note?: string;
  victim_name?: string;
  latitude?: number;
  longitude?: number;
  location_accuracy?: number;
  address?: string;
  idempotency_key?: string;
}) => apiFetch<SosResponse>("/api/emergency/sos", { method: "POST", body: JSON.stringify(body) });

export const getIncidentStatus = (id: string) =>
  apiFetch<SosResponse>(`/api/emergency/incidents/${id}`);

export const retryNotification = (id: string) =>
  apiFetch<NotificationRead>(`/api/emergency/notifications/${id}/retry`, { method: "POST" });

export const cancelIncident = (id: string) =>
  apiFetch(`/api/emergency/incidents/${id}/cancel`, { method: "POST" });

// ── Email health ──────────────────────────────────────────────────────────────
export const emailHealth = () => apiFetch<EmailHealth>("/api/email/health");

// ── Human-readable, HONEST status labels ──────────────────────────────────────
export const STATUS_LABEL: Record<NotificationStatus, string> = {
  queued: "Queued",
  pending: "Queued",
  processing: "Sending…",
  accepted_by_provider: "Accepted by provider",
  delivered: "Delivered",
  deferred: "Delayed",
  bounced: "Bounced",
  failed: "Failed",
  unknown: "Unknown",
  cancelled: "Cancelled",
};

export const STATUS_HINT: Record<NotificationStatus, string> = {
  queued: "Waiting in the send queue.",
  pending: "Waiting in the send queue.",
  processing: "Handing the email to the provider now.",
  accepted_by_provider: "The provider accepted it — not yet confirmed delivered to the inbox.",
  delivered: "The provider confirmed delivery to the recipient's mailbox.",
  deferred: "The provider reported a temporary delay and is still trying.",
  bounced: "The address rejected the email. It has been suppressed.",
  failed: "Could not be sent. You can retry.",
  unknown: "Send result could not be confirmed (e.g. a timeout). You can retry.",
  cancelled: "Cancelled before it was sent.",
};

// Statuses that are finished (no longer in flight) — used to stop polling.
export const TERMINAL_STATUSES: NotificationStatus[] = [
  "delivered",
  "bounced",
  "failed",
  "unknown",
  "cancelled",
];
