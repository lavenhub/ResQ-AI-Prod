import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Clock, Phone, RefreshCw, Send, XCircle } from "lucide-react";
import {
  cancelIncident,
  getIncidentStatus,
  retryNotification,
  smsHealth,
  STATUS_HINT,
  STATUS_LABEL,
  triggerSos,
  type NotificationRead,
  type NotificationStatus,
  type SmsHealth,
  type SosResponse,
} from "@/lib/emergencyApi";

function uid() {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

const TERMINAL: NotificationStatus[] = ["delivered", "failed", "unknown"];

const STATUS_STYLE: Record<NotificationStatus, string> = {
  pending: "bg-black/5 text-black border-black",
  processing: "bg-amber-50 text-amber-700 border-amber-500",
  provider_accepted: "bg-blue-50 text-blue-700 border-blue-500",
  delivered: "bg-green-50 text-green-700 border-green-600",
  failed: "bg-[#C62828]/10 text-[#C62828] border-[#C62828]",
  unknown: "bg-amber-50 text-amber-800 border-amber-600",
};

function StatusIcon({ s }: { s: NotificationStatus }) {
  if (s === "delivered") return <CheckCircle2 size={16} />;
  if (s === "failed") return <XCircle size={16} />;
  if (s === "unknown") return <AlertTriangle size={16} />;
  return <Clock size={16} />;
}

interface Props {
  coords: { lat: number; lng: number } | null;
  address: string | null;
  accuracy: number | null;
  incidentType?: string;
  victimName?: string;
}

type Phase = "idle" | "confirming" | "submitting" | "tracking" | "error";

export default function SosAlertPanel({ coords, address, accuracy, incidentType, victimName }: Props) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [incidentId, setIncidentId] = useState<string | null>(null);
  const [notifications, setNotifications] = useState<NotificationRead[]>([]);
  const [overall, setOverall] = useState<string>("");
  const [locationIncluded, setLocationIncluded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<SmsHealth | null>(null);
  const idempotencyKey = useRef<string>(uid());
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    smsHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  const applyResponse = useCallback((r: SosResponse) => {
    setIncidentId(r.incident_id);
    setNotifications(r.notifications);
    setOverall(r.notification_summary.overall);
    setLocationIncluded(r.location_available);
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // Poll the incident status while any notification is still in flight.
  useEffect(() => {
    if (phase !== "tracking" || !incidentId) return;
    const tick = async () => {
      try {
        const r = await getIncidentStatus(incidentId);
        setNotifications(r.notifications);
        setOverall(r.notification_summary.overall);
        if (r.notifications.length > 0 && r.notifications.every((n) => TERMINAL.includes(n.status))) {
          stopPolling();
        }
      } catch {
        /* keep last known state on a transient poll error */
      }
    };
    pollRef.current = setInterval(tick, 3000);
    tick();
    return stopPolling;
  }, [phase, incidentId, stopPolling]);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const submit = useCallback(async () => {
    setPhase("submitting");
    setError(null);
    try {
      const r = await triggerSos({
        incident_type: incidentType,
        victim_name: victimName || undefined,
        latitude: coords?.lat,
        longitude: coords?.lng,
        location_accuracy: accuracy ?? undefined,
        address: address ?? undefined,
        idempotency_key: idempotencyKey.current,
      });
      applyResponse(r);
      setPhase("tracking");
    } catch (e: any) {
      setError(e?.message || "Could not send the alert.");
      setPhase("error");
    }
  }, [incidentType, victimName, coords, accuracy, address, applyResponse]);

  const doRetry = useCallback(async (id: string) => {
    try {
      await retryNotification(id);
      setPhase("tracking"); // ensure polling resumes
      if (incidentId) {
        const r = await getIncidentStatus(incidentId);
        setNotifications(r.notifications);
        setOverall(r.notification_summary.overall);
      }
    } catch {
      /* surfaced on next poll */
    }
  }, [incidentId]);

  const doCancel = useCallback(async () => {
    if (!incidentId) return;
    try {
      await cancelIncident(incidentId);
      const r = await getIncidentStatus(incidentId);
      setNotifications(r.notifications);
      setOverall(r.notification_summary.overall);
      stopPolling();
    } catch {
      /* ignore */
    }
  }, [incidentId, stopPolling]);

  const reset = useCallback(() => {
    stopPolling();
    idempotencyKey.current = uid();
    setIncidentId(null);
    setNotifications([]);
    setOverall("");
    setError(null);
    setPhase("idle");
  }, [stopPolling]);

  return (
    <div className="border-2 border-black bg-white p-4">
      <div className="mb-1 flex items-center gap-2 text-[10px] font-bold uppercase tracking-wide text-[#C62828]">
        <Send size={14} /> Alert Emergency Contacts
        {health && <span className="text-black/50">· via {health.channel === "email" ? "Email" : "SMS"}</span>}
      </div>

      {/* Provider truthfulness banner */}
      {health && (health.isMock || !health.realSendingConfigured) && (
        <div className="mb-3 border-2 border-amber-500 bg-amber-50 px-3 py-2 text-[11px] font-semibold text-amber-800">
          {health.isMock
            ? "Demo provider: alerts are recorded but NO real message is sent."
            : `${health.channel === "email" ? "Email" : "SMS"} provider not fully configured — real sending is OFF.`}
        </div>
      )}

      {/* Always-visible real-help fallback */}
      <div className="mb-3 flex items-center gap-2 border-2 border-black bg-black px-3 py-2 text-[11px] font-bold uppercase text-white">
        <Phone size={14} /> Real emergency? Call 112 / 108 now — this does not dispatch help.
      </div>

      {phase === "idle" && (
        <>
          <p className="mb-3 text-xs text-black/70">
            Alert your saved emergency contacts{health?.channel === "email" ? " by email" : ""} with your
            location link and a short incident reference. Recipients are taken from your saved contacts.
          </p>
          <button
            onClick={() => setPhase("confirming")}
            className="flex w-full items-center justify-center gap-2 border-2 border-black bg-[#C62828] px-4 py-3 text-sm font-black uppercase tracking-wide text-white"
          >
            <Send size={16} /> Alert My Contacts
          </button>
          {!coords && (
            <p className="mt-2 text-[11px] text-black/50">
              Location not available yet — the alert will still be sent, without a map link.
            </p>
          )}
        </>
      )}

      {phase === "confirming" && (
        <>
          <p className="mb-3 text-sm font-bold text-black">
            Send an emergency SMS to your contacts now?
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setPhase("idle")}
              className="flex-1 border-2 border-black bg-white px-4 py-3 text-sm font-black uppercase"
            >
              Cancel
            </button>
            <button
              onClick={submit}
              className="flex-1 border-2 border-black bg-[#C62828] px-4 py-3 text-sm font-black uppercase text-white"
            >
              Yes, Send Now
            </button>
          </div>
        </>
      )}

      {phase === "submitting" && (
        <div className="py-4 text-center text-sm font-bold uppercase text-black/70">Recording incident…</div>
      )}

      {phase === "error" && (
        <div className="space-y-3">
          <div className="border-2 border-[#C62828] bg-[#C62828]/10 px-3 py-2 text-xs font-semibold text-[#C62828]">
            {error}
          </div>
          <button
            onClick={() => setPhase("idle")}
            className="w-full border-2 border-black bg-white px-4 py-2 text-xs font-black uppercase"
          >
            Back
          </button>
        </div>
      )}

      {phase === "tracking" && (
        <div className="space-y-3">
          <div className="flex items-center justify-between border-2 border-black bg-black/5 px-3 py-2">
            <div className="text-[11px] font-bold uppercase text-black">
              Incident recorded
              {incidentId && <span className="ml-1 font-mono text-black/50">#{incidentId.slice(0, 8).toUpperCase()}</span>}
            </div>
            <span className="text-[10px] font-bold uppercase text-black/60">{overall.replace(/_/g, " ")}</span>
          </div>

          {!locationIncluded && (
            <div className="border-2 border-amber-500 bg-amber-50 px-3 py-1.5 text-[11px] font-semibold text-amber-800">
              No location was included (GPS unavailable). Contacts were alerted without a map link.
            </div>
          )}

          <div className="space-y-2">
            {notifications.map((n) => (
              <div key={n.id} className="border-2 border-black bg-white p-2">
                <div className="flex items-center justify-between">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-bold text-black">
                      {n.recipient_name || n.recipient_email || n.recipient_phone}
                    </div>
                    <div className="truncate text-[11px] text-black/50">
                      {n.channel === "email" ? (n.recipient_email || "no email on file") : n.recipient_phone}
                    </div>
                  </div>
                  <span className={`flex shrink-0 items-center gap-1 border-2 px-2 py-1 text-[10px] font-black uppercase ${STATUS_STYLE[n.status]}`}>
                    <StatusIcon s={n.status} /> {STATUS_LABEL[n.status]}
                  </span>
                </div>
                <div className="mt-1 text-[10px] text-black/50">{STATUS_HINT[n.status]}</div>
                {(n.status === "failed" || n.status === "unknown") && (
                  <button
                    onClick={() => doRetry(n.id)}
                    className="mt-2 inline-flex items-center gap-1 border-2 border-black bg-white px-2 py-1 text-[10px] font-bold uppercase"
                  >
                    <RefreshCw size={11} /> Retry
                  </button>
                )}
              </div>
            ))}
            {notifications.length === 0 && (
              <div className="text-xs text-black/50">No recipients.</div>
            )}
          </div>

          <div className="flex gap-2">
            <button
              onClick={doCancel}
              className="flex-1 border-2 border-black bg-white px-3 py-2 text-[11px] font-black uppercase"
            >
              False Alarm — Cancel
            </button>
            <button
              onClick={reset}
              className="flex-1 border-2 border-black bg-black px-3 py-2 text-[11px] font-black uppercase text-white"
            >
              Done
            </button>
          </div>
          <p className="text-[10px] text-black/40">
            "Cancel" stops pending messages; it cannot recall messages a carrier already accepted.
          </p>
        </div>
      )}
    </div>
  );
}
