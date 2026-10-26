/**
 * Typed client for the ambulance coordination backend:
 *   - directory (hospital/ambulance services) CRUD + verify  (/api/ambulance/services)
 *   - bookings: create, place call, poll status, retry, cancel (/api/ambulance/bookings)
 *
 * All calls go through apiFetch (attaches the session bearer token). No telephony
 * provider secrets ever live here — the browser only talks to our backend, and
 * the target phone number is only ever returned MASKED.
 */

import { apiFetch } from "@/lib/api";

export type CallStatus =
  | "PENDING"
  | "INITIATING"
  | "RINGING"
  | "CONNECTED"
  | "COMPLETED"
  | "BUSY"
  | "NO_ANSWER"
  | "FAILED"
  | "CANCELLED";

export type BookingState =
  | "PREPARING_REQUEST"
  | "VALIDATING_DETAILS"
  | "INITIATING_CALL"
  | "RINGING"
  | "CONNECTED"
  | "IDENTIFYING_OPERATOR"
  | "EXPLAINING_EMERGENCY"
  | "SHARING_LOCATION"
  | "DISCUSSING_AVAILABILITY"
  | "COLLECTING_DETAILS"
  | "REQUESTING_CONFIRMATION"
  | "BOOKING_CONFIRMED"
  | "BOOKING_DECLINED"
  | "CALL_FAILED"
  | "CALL_DISCONNECTED"
  | "PENDING_FOLLOW_UP"
  | "COMPLETED";

export type Availability = "UNKNOWN" | "AVAILABLE" | "UNAVAILABLE";

export interface AmbulanceServiceItem {
  id: string;
  name: string;
  phone: string; // masked by the backend
  address: string | null;
  city: string | null;
  service_area: string | null;
  ambulance_types: string | null;
  capabilities: string | null;
  contact_person: string | null;
  operating_hours: string | null;
  verified: boolean;
  is_demo: boolean;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Booking {
  id: string;
  incident_id: string;
  service_id: string;
  service_name: string | null;
  service_phone_masked: string | null;
  service_is_demo: boolean;
  patient_name: string | null;
  patient_age: number | null;
  patient_condition: string | null;
  emergency_type: string | null;
  num_patients: number;
  pickup_address: string | null;
  latitude: number | null;
  longitude: number | null;
  destination_preference: string | null;
  required_ambulance_type: string | null;
  special_requirements: string | null;
  authorized_contact: string | null;
  consent: boolean;
  call_status: CallStatus;
  booking_state: BookingState;
  availability: Availability;
  booking_accepted: boolean;
  receptionist_response: string | null;
  eta_text: string | null;
  charges_text: string | null;
  booking_reference: string | null;
  situation_code: string | null;
  provider: string | null;
  attempts: number;
  last_error: string | null;
  call_initiated_at: string | null;
  call_connected_at: string | null;
  call_completed_at: string | null;
  created_at: string;
  updated_at: string;
}

// ── Directory ──────────────────────────────────────────────────────────────────
export const listServices = (includeInactive = false) =>
  apiFetch<AmbulanceServiceItem[]>(
    `/api/ambulance/services${includeInactive ? "?include_inactive=true" : ""}`,
  );

export const createService = (s: {
  name: string;
  phone: string;
  address?: string;
  city?: string;
  service_area?: string;
  ambulance_types?: string;
  capabilities?: string;
  contact_person?: string;
  operating_hours?: string;
  is_demo?: boolean;
}) => apiFetch<AmbulanceServiceItem>("/api/ambulance/services", {
  method: "POST",
  body: JSON.stringify(s),
});

export const verifyService = (id: string) =>
  apiFetch<AmbulanceServiceItem>(`/api/ambulance/services/${id}/verify`, { method: "POST" });

export const updateService = (id: string, patch: Partial<AmbulanceServiceItem>) =>
  apiFetch<AmbulanceServiceItem>(`/api/ambulance/services/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const deleteService = (id: string) =>
  apiFetch<void>(`/api/ambulance/services/${id}`, { method: "DELETE" });

// ── Bookings ─────────────────────────────────────────────────────────────────
export const createBooking = (b: {
  // Provide to tie to an existing incident; omit to let the backend create one
  // (owned by the caller) from the location + emergency fields below.
  incident_id?: string;
  service_id: string;
  patient_name?: string;
  patient_age?: number;
  patient_condition?: string;
  emergency_type?: string;
  num_patients?: number;
  destination_preference?: string;
  required_ambulance_type?: string;
  special_requirements?: string;
  authorized_contact?: string;
  consent: boolean;
  // Used only when incident_id is absent (seed the auto-created incident).
  latitude?: number;
  longitude?: number;
  location_accuracy?: number;
  address?: string;
}) => apiFetch<Booking>("/api/ambulance/bookings", { method: "POST", body: JSON.stringify(b) });

export const getBooking = (id: string) => apiFetch<Booking>(`/api/ambulance/bookings/${id}`);

export const listBookings = (incidentId?: string) =>
  apiFetch<Booking[]>(
    `/api/ambulance/bookings${incidentId ? `?incident_id=${encodeURIComponent(incidentId)}` : ""}`,
  );

export const placeCall = (id: string) =>
  apiFetch<Booking>(`/api/ambulance/bookings/${id}/call`, { method: "POST" });

export const retryCall = (id: string) =>
  apiFetch<Booking>(`/api/ambulance/bookings/${id}/retry`, { method: "POST" });

export const cancelCall = (id: string) =>
  apiFetch<Booking>(`/api/ambulance/bookings/${id}/cancel`, { method: "POST" });

// ── WebRTC reception (free, no-PSTN path) ───────────────────────────────────────
export interface ReceptionBookingInfo {
  booking_id: string;
  service_name: string | null;
  is_demo: boolean;
  emergency_type: string | null;
  patient_condition: string | null;
  num_patients: number;
  pickup_address: string | null;
  call_status: CallStatus;
  booking_state: BookingState;
  answerable: boolean;
}

// Unauthenticated: the receptionist (teammate) need not be logged in; the booking
// id is the capability.
export const receptionBookingInfo = (bookingId: string) =>
  apiFetch<ReceptionBookingInfo>(`/api/ambulance/reception/booking/${bookingId}`);

/** The link the caller shares with the teammate to answer the call. */
export const receptionUrl = (bookingId: string) =>
  `${typeof window !== "undefined" ? window.location.origin : ""}/reception?booking=${bookingId}`;

// ── Honest, user-facing status mapping ─────────────────────────────────────────
// The label says only what the backend has recorded — never a fabricated ETA or
// a dispatch. ETA / charges / reference are shown elsewhere ONLY when present.
export const BOOKING_STATE_LABEL: Record<BookingState, string> = {
  PREPARING_REQUEST: "Preparing ambulance request",
  VALIDATING_DETAILS: "Validating emergency details",
  INITIATING_CALL: "Initiating call",
  RINGING: "Calling reception…",
  CONNECTED: "Receptionist connected",
  IDENTIFYING_OPERATOR: "Confirming the right desk",
  EXPLAINING_EMERGENCY: "Explaining the emergency",
  SHARING_LOCATION: "Sharing pickup location",
  DISCUSSING_AVAILABILITY: "Discussing ambulance availability",
  COLLECTING_DETAILS: "Sharing details with reception",
  REQUESTING_CONFIRMATION: "Booking confirmation requested",
  BOOKING_CONFIRMED: "Booking confirmed by reception",
  BOOKING_DECLINED: "Booking declined",
  CALL_FAILED: "Call failed",
  CALL_DISCONNECTED: "Call disconnected",
  PENDING_FOLLOW_UP: "Follow-up required",
  COMPLETED: "Call completed",
};

// States where a call is actively in flight (poll + disable the call button).
export const IN_FLIGHT_STATES: BookingState[] = [
  "INITIATING_CALL",
  "RINGING",
  "CONNECTED",
  "IDENTIFYING_OPERATOR",
  "EXPLAINING_EMERGENCY",
  "SHARING_LOCATION",
  "DISCUSSING_AVAILABILITY",
  "COLLECTING_DETAILS",
  "REQUESTING_CONFIRMATION",
];

export const TERMINAL_STATES: BookingState[] = [
  "BOOKING_CONFIRMED",
  "BOOKING_DECLINED",
  "CALL_FAILED",
  "CALL_DISCONNECTED",
  "PENDING_FOLLOW_UP",
  "COMPLETED",
];

export const isInFlight = (s: BookingState) => IN_FLIGHT_STATES.includes(s);
export const isTerminal = (s: BookingState) => TERMINAL_STATES.includes(s);
export const isRetryable = (s: BookingState) =>
  s === "CALL_FAILED" || s === "CALL_DISCONNECTED" || s === "PENDING_FOLLOW_UP";
