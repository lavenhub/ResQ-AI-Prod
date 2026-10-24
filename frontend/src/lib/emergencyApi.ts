/**
 * Typed client for the backend emergency pipeline:
 *   - emergency contacts CRUD   (/api/contacts)
 *   - SOS trigger + incident status + retry/cancel (/api/emergency/*)
 *   - SMS provider health        (/api/sms/health)
 *
 * All calls go through apiFetch, which attaches the session bearer token.
 * No provider secrets ever live here — the browser only talks to our backend.
 */

import { apiFetch } from "@/lib/api";

export type NotificationStatus =
  | "pending"
  | "processing"
  | "provider_accepted"
  | "delivered"
  | "failed"
  | "unknown";

export interface Contact {
  id: string;
  name: string;
  phone_e164: string;
  email: string | null;
  country: string;
  relationship: string | null;
  priority: number;
  active: boolean;
  verified: boolean;
  created_at: string;
  updated_at: string;
}

export interface NotificationRead {
  id: string;
  incident_id: string;
  recipient_name: string | null;
  recipient_phone: string;
  recipient_email: string | null;
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

export interface SmsHealth {
  provider: string;
  channel: string;
  ready: boolean;
  isMock: boolean;
  problems: string[];
  realSendingConfigured: boolean;
}

// ── Contacts ──────────────────────────────────────────────────────────────────
export const listContacts = () => apiFetch<Contact[]>("/api/contacts");

export const createContact = (c: {
  name: string;
  phone: string;
  email?: string;
  country?: string;
  relationship?: string;
  priority?: number;
  active?: boolean;
}) => apiFetch<Contact>("/api/contacts", { method: "POST", body: JSON.stringify(c) });

export const updateContact = (id: string, patch: Partial<{
  name: string;
  phone: string;
  email: string;
  country: string;
  relationship: string;
  priority: number;
  active: boolean;
}>) => apiFetch<Contact>(`/api/contacts/${id}`, { method: "PATCH", body: JSON.stringify(patch) });

export const deleteContact = (id: string) =>
  apiFetch<void>(`/api/contacts/${id}`, { method: "DELETE" });

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

// ── SMS health ────────────────────────────────────────────────────────────────
export const smsHealth = () => apiFetch<SmsHealth>("/api/sms/health");

// ── Human-readable, HONEST status labels ──────────────────────────────────────
export const STATUS_LABEL: Record<NotificationStatus, string> = {
  pending: "Queued",
  processing: "Sending…",
  provider_accepted: "Accepted by carrier",
  delivered: "Delivered",
  failed: "Failed",
  unknown: "Unknown",
};

export const STATUS_HINT: Record<NotificationStatus, string> = {
  pending: "Waiting in the send queue.",
  processing: "Handing to the SMS provider now.",
  provider_accepted: "The carrier accepted it — not yet confirmed delivered.",
  delivered: "The carrier confirmed delivery to the handset.",
  failed: "Could not be sent. You can retry.",
  unknown: "Send result could not be confirmed (e.g. a timeout). You can retry.",
};
