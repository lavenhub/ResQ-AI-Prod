import { useCallback, useEffect, useRef, useState } from "react";
import { Ambulance, CheckCircle2, Phone, PhoneOff, RefreshCw, XCircle } from "lucide-react";
import {
  type AmbulanceServiceItem,
  type Booking,
  BOOKING_STATE_LABEL,
  createBooking,
  getBooking,
  isInFlight,
  isRetryable,
  listServices,
  placeCall,
  retryCall,
} from "@/lib/ambulanceApi";

function loadLocal<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v ? (JSON.parse(v) as T) : fallback;
  } catch {
    return fallback;
  }
}

interface Props {
  coords: { lat: number; lng: number } | null;
  address: string | null;
  incidentType?: string;
}

/**
 * Real ambulance-coordination panel. Replaces the old simulated moving-vehicle
 * animation — it shows the ACTUAL backend-owned call/booking state, a masked
 * target number, and only shows ETA / charges / reference if the receptionist
 * actually provided them. It never implies a dispatch.
 */
export function AmbulanceCoordination({ coords, address, incidentType }: Props) {
  const [services, setServices] = useState<AmbulanceServiceItem[]>([]);
  const [serviceId, setServiceId] = useState<string>("");
  const [consent, setConsent] = useState(false);
  const [booking, setBooking] = useState<Booking | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const name = loadLocal("resq_name", "");
  const age = loadLocal<number>("resq_age", 0);
  const conditions = loadLocal("resq_conditions", "");

  // Load the directory once. Prefer the DEMO reception entry as the default.
  useEffect(() => {
    listServices()
      .then((rows) => {
        setServices(rows);
        const demo = rows.find((r) => r.is_demo && r.active);
        const verified = rows.find((r) => r.verified && r.active);
        setServiceId((demo ?? verified ?? rows[0])?.id ?? "");
      })
      .catch((e) => setError(e?.message ?? "Could not load ambulance services"));
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // Poll the booking while a call is in flight; stop on a terminal state.
  const startPolling = useCallback(
    (id: string) => {
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const b = await getBooking(id);
          setBooking(b);
          if (!isInFlight(b.booking_state)) stopPolling();
        } catch {
          /* transient — keep polling */
        }
      }, 2500);
    },
    [stopPolling],
  );

  useEffect(() => () => stopPolling(), [stopPolling]);

  const selected = services.find((s) => s.id === serviceId);

  const startCoordination = useCallback(async () => {
    if (!serviceId || !consent || busy) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createBooking({
        service_id: serviceId,
        consent: true,
        emergency_type: incidentType && incidentType !== "Emergency" ? incidentType : undefined,
        patient_name: name || undefined,
        patient_age: age || undefined,
        patient_condition: conditions || undefined,
        latitude: coords?.lat,
        longitude: coords?.lng,
        address: address || undefined,
      });
      setBooking(created);
      const afterCall = await placeCall(created.id);
      setBooking(afterCall);
      if (isInFlight(afterCall.booking_state)) startPolling(afterCall.id);
    } catch (e: any) {
      setError(e?.message ?? "Could not start ambulance coordination");
    } finally {
      setBusy(false);
    }
  }, [serviceId, consent, busy, incidentType, name, age, conditions, coords, address, startPolling]);

  const doRetry = useCallback(async () => {
    if (!booking || busy) return;
    setBusy(true);
    setError(null);
    try {
      await retryCall(booking.id);
      const after = await placeCall(booking.id);
      setBooking(after);
      if (isInFlight(after.booking_state)) startPolling(after.id);
    } catch (e: any) {
      setError(e?.message ?? "Retry failed");
    } finally {
      setBusy(false);
    }
  }, [booking, busy, startPolling]);

  const state = booking?.booking_state;
  const confirmed = state === "BOOKING_CONFIRMED";
  const declined = state === "BOOKING_DECLINED";
  const failed = state === "CALL_FAILED" || state === "CALL_DISCONNECTED";
  const live = booking && isInFlight(booking.booking_state);

  return (
    <div className="border-2 border-black bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-black uppercase text-[#C62828]">
          <Ambulance size={18} /> Ambulance Coordination
        </div>
        {selected?.is_demo && (
          <span className="border-2 border-black bg-amber-300 px-2 py-0.5 text-[9px] font-black uppercase">
            Demonstration
          </span>
        )}
      </div>

      {/* Honest framing — this is a REAL call, not a dispatch guarantee */}
      <p className="mb-3 text-[11px] leading-snug text-black/60">
        ResQAI places a real phone call to the reception number below and an AI speaks with
        whoever answers. No ambulance is dispatched unless the service confirms it.
      </p>

      {error && (
        <div className="mb-3 flex items-start gap-2 border-2 border-red-600 bg-red-50 px-3 py-2 text-[11px] font-bold text-red-700">
          <XCircle size={14} className="mt-0.5 shrink-0" /> {error}
        </div>
      )}

      {services.length === 0 && !error && (
        <p className="text-[11px] text-black/60">
          No ambulance services are configured yet. An administrator can add one in Profile.
        </p>
      )}

      {/* ── Pre-call setup ── */}
      {!booking && services.length > 0 && (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-[10px] font-bold uppercase tracking-wide">
              Reception / Service
            </label>
            <select
              value={serviceId}
              onChange={(e) => setServiceId(e.target.value)}
              className="w-full border-2 border-black bg-white px-3 py-2 text-sm font-semibold"
            >
              {services
                .filter((s) => s.active)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} — {s.phone}
                    {s.is_demo ? " (DEMO)" : s.verified ? " ✓" : " (unverified)"}
                  </option>
                ))}
            </select>
            {selected && !selected.verified && !selected.is_demo && (
              <p className="mt-1 text-[10px] font-bold uppercase text-red-600">
                Unverified number — cannot be called until verified.
              </p>
            )}
          </div>

          <label className="flex items-start gap-2 text-[11px] leading-snug text-black/80">
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              className="mt-0.5 h-4 w-4 border-2 border-black"
            />
            I authorize ResQAI to share the patient's condition and pickup location with this
            service to coordinate an ambulance.
          </label>

          <button
            onClick={startCoordination}
            disabled={!serviceId || !consent || busy || (selected && !selected.verified && !selected.is_demo)}
            className="flex w-full items-center justify-center gap-2 border-2 border-black bg-[#C62828] px-4 py-3 text-sm font-black uppercase tracking-wide text-white disabled:opacity-40"
          >
            <Phone size={16} /> {busy ? "Starting…" : "Start Ambulance Coordination"}
          </button>
        </div>
      )}

      {/* ── Live / result ── */}
      {booking && (
        <div className="space-y-3">
          {/* Status line */}
          <div className="flex items-center gap-2">
            <span
              className={`h-2.5 w-2.5 rounded-full ${
                confirmed
                  ? "bg-green-600"
                  : declined || failed
                    ? "bg-red-600"
                    : live
                      ? "bg-amber-500 animate-pulse"
                      : "bg-black/30"
              }`}
            />
            <span className="text-sm font-black uppercase">
              {BOOKING_STATE_LABEL[booking.booking_state]}
            </span>
          </div>

          {/* Target */}
          <div className="border-2 border-black/20 bg-black/5 px-3 py-2 text-[11px]">
            <div className="flex justify-between">
              <span className="font-bold uppercase text-black/60">Calling</span>
              <span className="font-black">
                {booking.service_name}
                {booking.service_is_demo ? " (DEMO)" : ""}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="font-bold uppercase text-black/60">Number</span>
              <span className="font-mono">{booking.service_phone_masked}</span>
            </div>
          </div>

          {/* Availability — only when the receptionist actually said something */}
          {booking.availability !== "UNKNOWN" && (
            <div className="flex justify-between border-b border-black/10 pb-1 text-xs">
              <span className="font-bold uppercase text-black/60">Availability</span>
              <span className="font-black">
                {booking.availability === "AVAILABLE" ? "Available" : "Not available"}
              </span>
            </div>
          )}

          {/* These are shown ONLY when actually provided by the receptionist */}
          {booking.eta_text && (
            <Row label="ETA (as told)" value={booking.eta_text} />
          )}
          {booking.charges_text && <Row label="Charges (as told)" value={booking.charges_text} />}
          {booking.booking_reference && (
            <Row label="Booking ref" value={booking.booking_reference} />
          )}

          {confirmed && (
            <div className="flex items-start gap-2 border-2 border-green-600 bg-green-50 px-3 py-2 text-[11px] font-bold text-green-800">
              <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
              The reception confirmed the booking. This confirms a booking, not that an ambulance
              has physically been dispatched.
            </div>
          )}
          {booking.receptionist_response && !confirmed && (
            <p className="text-[11px] italic text-black/60">“{booking.receptionist_response}”</p>
          )}
          {booking.last_error && failed && (
            <p className="text-[11px] font-bold text-red-600">{booking.last_error}</p>
          )}

          {isRetryable(booking.booking_state) && (
            <button
              onClick={doRetry}
              disabled={busy}
              className="flex w-full items-center justify-center gap-2 border-2 border-black bg-white px-4 py-2 text-xs font-black uppercase disabled:opacity-40"
            >
              <RefreshCw size={14} /> {busy ? "Retrying…" : "Retry call"}
            </button>
          )}

          {live && (
            <div className="flex items-center justify-center gap-2 text-[10px] font-bold uppercase text-amber-700">
              <PhoneOff size={12} /> Call in progress — do not start another
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between border-b border-black/10 pb-1 text-xs">
      <span className="font-bold uppercase text-black/60">{label}</span>
      <span className="font-black">{value}</span>
    </div>
  );
}
