/**
 * Typed client for the LOCAL ambulance coordination workflow (simulated reception
 * call). All calls go through apiFetch (attaches the session token). No telephony.
 */
import { apiFetch } from "@/lib/api";

export interface AmbulanceServiceItem {
  id: string;
  name: string;
  phone: string; // masked by the backend
  city: string | null;
  ambulance_types: string | null;
  capabilities: string | null;
  verified: boolean;
  is_demo: boolean;
  active: boolean;
}

export type BookingState =
  | "PREPARING_REQUEST"
  | "CONNECTED"
  | "DISCUSSING_AVAILABILITY"
  | "BOOKING_CONFIRMED"
  | "BOOKING_DECLINED"
  | "PENDING_FOLLOW_UP"
  | "COMPLETED"
  | string;

export interface Booking {
  id: string;
  incident_id: string;
  service_id: string;
  service_name: string | null;
  service_phone_masked: string | null;
  service_is_demo: boolean;
  patient_condition: string | null;
  emergency_type: string | null;
  pickup_address: string | null;
  booking_state: BookingState;
  availability: "UNKNOWN" | "AVAILABLE" | "UNAVAILABLE";
  booking_accepted: boolean;
  receptionist_response: string | null;
  eta_text: string | null;
  charges_text: string | null;
  booking_reference: string | null;
  provider: string | null;
}

export interface TranscriptItem {
  role: "ai" | "receptionist";
  text: string;
}

export const listServices = () => apiFetch<AmbulanceServiceItem[]>("/api/ambulance/services");

export const createBooking = (b: {
  service_id: string;
  incident_id?: string;
  emergency_type?: string;
  patient_name?: string;
  patient_age?: number;
  patient_condition?: string;
  special_requirements?: string;
  latitude?: number;
  longitude?: number;
  address?: string;
}) => apiFetch<Booking>("/api/ambulance/bookings", { method: "POST", body: JSON.stringify(b) });

export const getBooking = (id: string) => apiFetch<Booking>(`/api/ambulance/bookings/${id}`);

export const callOpen = (id: string) =>
  apiFetch<{ booking: Booking; reply: string }>(`/api/ambulance/bookings/${id}/call/open`, {
    method: "POST",
  });

export const callTurn = (id: string, transcript: TranscriptItem[]) =>
  apiFetch<{ reply: string }>(`/api/ambulance/bookings/${id}/call/turn`, {
    method: "POST",
    body: JSON.stringify({ transcript }),
  });

export const callFinalize = (id: string, transcript: TranscriptItem[]) =>
  apiFetch<Booking>(`/api/ambulance/bookings/${id}/call/finalize`, {
    method: "POST",
    body: JSON.stringify({ transcript }),
  });

export const BOOKING_STATE_LABEL: Record<string, string> = {
  PREPARING_REQUEST: "Preparing request",
  CONNECTED: "Receptionist connected",
  DISCUSSING_AVAILABILITY: "Discussing availability",
  BOOKING_CONFIRMED: "Booking confirmed",
  BOOKING_DECLINED: "Booking declined",
  PENDING_FOLLOW_UP: "Follow-up required",
  COMPLETED: "Call completed",
};
