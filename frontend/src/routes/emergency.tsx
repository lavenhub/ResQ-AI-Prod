import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Copy,
  Mic,
  MicOff,
  RefreshCw,
  Send,
  Siren,
  Volume2,
  VolumeX,
} from "lucide-react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { AuthGuard } from "@/components/AuthGuard";
import { BottomNav } from "@/components/BottomNav";
import { useVoiceAgent } from "@/hooks/useVoiceAgent";
import { useBottomBarClasses } from "@/hooks/useBottomBarPos";
import { INDIAN_LANGUAGES } from "@/lib/types";

type VehicleType = "ambulance" | "fire" | "police";

export const Route = createFileRoute("/emergency")({
  head: () => ({
    meta: [
      { title: "Emergency — ResQ AI" },
      { name: "description", content: "Active emergency response with AI assistant." },
    ],
  }),
  component: () => (
    <AuthGuard>
      <EmergencyPage />
    </AuthGuard>
  ),
});

function loadLocal<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v ? (JSON.parse(v) as T) : fallback;
  } catch {
    return fallback;
  }
}

// ─── Leaflet map with a SIMULATED vehicle moving toward the patient ───────────
const VEHICLE_EMOJI: Record<string, string> = { ambulance: "🚑", fire: "🚒", police: "🚔" };
const VEHICLE_COLOR: Record<string, string> = { ambulance: "#C62828", fire: "#E65100", police: "#1565C0" };
const TRAVEL_MS = 45_000;
const SPAWN_KM = 2.0;

function offsetLatLng(lat: number, lng: number, bearingDeg: number, km: number) {
  const R = 6371, d = km / R, b = (bearingDeg * Math.PI) / 180;
  const φ1 = (lat * Math.PI) / 180, λ1 = (lng * Math.PI) / 180;
  const φ2 = Math.asin(Math.sin(φ1) * Math.cos(d) + Math.cos(φ1) * Math.sin(d) * Math.cos(b));
  const λ2 = λ1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(φ1), Math.cos(d) - Math.sin(φ1) * Math.sin(φ2));
  return { lat: (φ2 * 180) / Math.PI, lng: (λ2 * 180) / Math.PI };
}
function lerp(a: number, b: number, t: number) { return a + (b - a) * t; }
function ease(p: number) { return p < 0.5 ? 2 * p * p : -1 + (4 - 2 * p) * p; }

function EmergencyMap({
  userCoords,
  vehicleType,
}: {
  userCoords: { lat: number; lng: number };
  vehicleType?: VehicleType;
}) {
  const mapDivRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const vehicleMarkerRef = useRef<L.Marker | null>(null);
  const polylineRef = useRef<L.Polyline | null>(null);
  const rafRef = useRef<number | null>(null);
  const [eta, setEta] = useState<number | null>(null);
  const [arrived, setArrived] = useState(false);

  useEffect(() => {
    if (!mapDivRef.current || mapRef.current) return;
    const map = L.map(mapDivRef.current, {
      center: [userCoords.lat, userCoords.lng], zoom: 14, zoomControl: true, attributionControl: false,
    });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);
    const userIcon = L.divIcon({
      className: "",
      html: `<div style="width:36px;height:36px;border-radius:50%;background:#C62828;border:3px solid white;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,0.4);font-size:16px;">📍</div><div style="position:absolute;top:38px;left:50%;transform:translateX(-50%);background:#C62828;color:white;font-size:9px;font-weight:900;padding:1px 4px;white-space:nowrap;">YOU</div>`,
      iconSize: [36, 36], iconAnchor: [18, 18],
    });
    L.marker([userCoords.lat, userCoords.lng], { icon: userIcon }).addTo(map);
    mapRef.current = map;
    return () => { map.remove(); mapRef.current = null; };
  }, []); // eslint-disable-line

  useEffect(() => {
    if (!vehicleType || !mapRef.current) return;
    const map = mapRef.current;
    const emoji = VEHICLE_EMOJI[vehicleType] ?? "🚑";
    const color = VEHICLE_COLOR[vehicleType] ?? "#C62828";
    const spawn = offsetLatLng(userCoords.lat, userCoords.lng, Math.floor(Math.random() * 360), SPAWN_KM);
    const vehicleIcon = L.divIcon({
      className: "",
      html: `<div style="font-size:32px;filter:drop-shadow(0 3px 6px rgba(0,0,0,0.5));">${emoji}</div>`,
      iconSize: [40, 40], iconAnchor: [20, 20],
    });
    if (vehicleMarkerRef.current) vehicleMarkerRef.current.remove();
    const vehicleMarker = L.marker([spawn.lat, spawn.lng], { icon: vehicleIcon }).addTo(map);
    vehicleMarkerRef.current = vehicleMarker;
    if (polylineRef.current) polylineRef.current.remove();
    const polyline = L.polyline(
      [[spawn.lat, spawn.lng], [userCoords.lat, userCoords.lng]],
      { color, dashArray: "8 6", weight: 3, opacity: 0.7 },
    ).addTo(map);
    polylineRef.current = polyline;
    map.fitBounds(L.latLngBounds([[spawn.lat, spawn.lng], [userCoords.lat, userCoords.lng]]), { padding: [40, 40] });
    setArrived(false);
    setEta(Math.round(TRAVEL_MS / 1000));
    const startTime = performance.now();
    const animate = (ts: number) => {
      const p = Math.min((ts - startTime) / TRAVEL_MS, 1);
      const ep = ease(p);
      const curLat = lerp(spawn.lat, userCoords.lat, ep);
      const curLng = lerp(spawn.lng, userCoords.lng, ep);
      vehicleMarker.setLatLng([curLat, curLng]);
      polyline.setLatLngs([[curLat, curLng], [userCoords.lat, userCoords.lng]]);
      setEta(Math.max(0, Math.round(((1 - p) * TRAVEL_MS) / 1000)));
      if (p < 1) rafRef.current = requestAnimationFrame(animate);
      else { setArrived(true); polyline.remove(); vehicleMarker.setLatLng([userCoords.lat, userCoords.lng]); }
    };
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(animate);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [vehicleType]); // eslint-disable-line

  const color = vehicleType ? VEHICLE_COLOR[vehicleType] : "#C62828";
  const emoji = vehicleType ? VEHICLE_EMOJI[vehicleType] : "";
  return (
    <div className="relative border-2 border-black bg-white overflow-hidden" style={{ height: 260 }}>
      <div ref={mapDivRef} className="absolute inset-0" />
      {vehicleType && (
        <div
          className="absolute top-2 left-2 z-[1000] border-2 border-white px-3 py-1.5 text-xs font-black uppercase text-white shadow-lg"
          style={{ background: color }}
        >
          {arrived ? `${emoji} On scene (demo)` : `${emoji} ${eta}s away (demo)`}
        </div>
      )}
    </div>
  );
}

// ─── Emergency page ───────────────────────────────────────────────────────────
function EmergencyPage() {
  const navigate = useNavigate();
  const agent = useVoiceAgent();
  const pos = useBottomBarClasses(
    "border-t-2 border-black bg-[#C62828] py-4 text-sm font-black uppercase tracking-wide text-white",
  );
  const [textInput, setTextInput] = useState("");
  const logRef = useRef<HTMLDivElement>(null);

  const name = loadLocal("resq_name", "");
  const blood = loadLocal("resq_blood", "—");
  const age = loadLocal<number>("resq_age", 0);
  const allergies = loadLocal("resq_allergies", "—");
  const conditions = loadLocal("resq_conditions", "—");

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [agent.transcript]);

  const active = agent.emergencyActive;
  const phaseLabel =
    agent.status === "speaking"
      ? "Assistant speaking…"
      : agent.status === "thinking"
        ? "Thinking…"
        : agent.listening
          ? "Listening — speak now"
          : "Ask ResQ anything";

  const send = () => {
    if (textInput.trim()) {
      agent.sendMessage(textInput);
      setTextInput("");
    }
  };

  return (
    <div className="min-h-screen bg-white pb-32">
      <header
        className={`sticky top-0 z-20 border-b-2 border-black px-4 py-3 ${active ? "bg-[#C62828] text-white" : "bg-white text-black"}`}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle size={22} strokeWidth={2.5} />
            <span className="text-base font-black uppercase tracking-wide">
              {active ? "Emergency Active" : "Emergency"}
            </span>
          </div>
          <span
            className={`flex items-center gap-2 border-2 px-2 py-1 text-[10px] font-bold uppercase ${active ? "border-white bg-white text-[#C62828]" : "border-black bg-white text-black"}`}
          >
            <span className={`h-2 w-2 rounded-full ${active ? "bg-[#C62828]" : "bg-black/40"}`} />
            {active ? "Live" : "Standby"}
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-[480px] space-y-4 px-4 py-4">
        {/* Language + speak toggle */}
        <div className="flex gap-2">
          <select
            value={agent.language}
            onChange={(e) => agent.setLanguage(e.target.value)}
            className="flex-1 border-2 border-black bg-white px-3 py-2 text-sm font-semibold text-black"
          >
            {INDIAN_LANGUAGES.map((l) => (
              <option key={l}>{l}</option>
            ))}
          </select>
          <button
            onClick={() => agent.setSpeakEnabled(!agent.speakEnabled)}
            title={agent.speakEnabled ? "Voice replies on" : "Voice replies off"}
            className={`flex items-center justify-center border-2 border-black px-3 ${agent.speakEnabled ? "bg-[#C62828] text-white" : "bg-white text-black"}`}
          >
            {agent.speakEnabled ? <Volume2 size={18} /> : <VolumeX size={18} />}
          </button>
        </div>

        {/* Map */}
        {agent.coords ? (
          <EmergencyMap userCoords={agent.coords} vehicleType={agent.vehicleDispatch?.vehicleType} />
        ) : (
          <div
            className="flex items-center justify-center border-2 border-black bg-white text-xs font-bold uppercase tracking-wider text-black/60"
            style={{ height: 260 }}
          >
            Acquiring GPS...
          </div>
        )}

        {/* Mock ambulance "booked" card */}
        {agent.vehicleDispatch && (
          <div
            className="flex items-center gap-3 border-2 border-black px-4 py-2"
            style={{ background: VEHICLE_COLOR[agent.vehicleDispatch.vehicleType] }}
          >
            <span className="text-2xl">{VEHICLE_EMOJI[agent.vehicleDispatch.vehicleType]}</span>
            <div>
              <div className="text-xs font-black uppercase text-white">
                Ambulance booked <span className="opacity-80">(simulated — demo)</span>
              </div>
              <div className="text-[10px] text-white/80">
                {agent.vehicleDispatch.incidentType}. This is a demo animation — no real vehicle was
                dispatched. Call 112 / 108 for real help.
              </div>
            </div>
          </div>
        )}

        {/* Real email-sent banner */}
        {agent.alertSent && agent.alertSent.count > 0 && (
          <div className="flex items-start gap-2 border-2 border-green-600 bg-green-50 px-3 py-3 text-green-800">
            <CheckCircle2 size={18} className="mt-0.5 shrink-0" />
            <div className="text-xs font-bold uppercase tracking-wide">
              Emergency email sent to your contacts
              <span className="ml-1 font-normal normal-case text-green-700">
                ({agent.alertSent.count} contact{agent.alertSent.count === 1 ? "" : "s"})
              </span>
            </div>
          </div>
        )}

        {/* Trigger emergency button */}
        <button
          onClick={() => agent.triggerEmergency(agent.incidentType)}
          className="flex w-full items-center justify-center gap-2 border-2 border-black bg-[#C62828] px-4 py-3 text-sm font-black uppercase tracking-wide text-white"
        >
          <Siren size={18} /> Trigger Emergency (SOS)
        </button>

        {/* Assistant chat */}
        <div className="border-2 border-black bg-white p-4">
          <div className="mb-2 flex items-center gap-2">
            <span
              className={`h-2.5 w-2.5 rounded-full ${
                agent.status === "speaking"
                  ? "bg-[#C62828] animate-pulse"
                  : agent.status === "thinking"
                    ? "bg-amber-500 animate-pulse"
                    : agent.listening
                      ? "bg-green-600 animate-pulse"
                      : "bg-black/30"
              }`}
            />
            <span className="text-sm font-black uppercase text-black">{phaseLabel}</span>
          </div>

          {agent.transcript.length > 0 && (
            <div ref={logRef} className="mb-3 max-h-[240px] space-y-2 overflow-y-auto pr-1 text-sm">
              {agent.transcript.map((t) => (
                <div key={t.id} className="leading-snug">
                  {t.sender === "system" ? (
                    <span className="text-xs italic text-black/50">{t.text}</span>
                  ) : (
                    <span>
                      <span
                        className={`mr-1 font-black uppercase ${t.sender === "resq" ? "text-[#C62828]" : "text-black"}`}
                      >
                        {t.sender === "resq" ? "RESQ:" : "YOU:"}
                      </span>
                      <span className="text-black">{t.text}</span>
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="flex gap-2">
            <input
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder="Describe your emergency…"
              className="flex-1 border-2 border-black bg-white px-3 py-2 text-sm"
            />
            {agent.sttSupported && (
              <button
                onClick={() => (agent.listening ? agent.stopListening() : agent.startListening())}
                title="Speak"
                className={`flex items-center justify-center border-2 border-black px-3 ${agent.listening ? "bg-[#C62828] text-white" : "bg-white text-black"}`}
              >
                {agent.listening ? <MicOff size={16} /> : <Mic size={16} />}
              </button>
            )}
            <button
              onClick={send}
              className="flex items-center justify-center border-2 border-black bg-black px-3 text-white"
            >
              <Send size={16} />
            </button>
          </div>
          <p className="mt-2 text-[10px] text-black/50">
            {agent.hasApiKey
              ? "Type or tap the mic to talk to ResQ. Replies are read aloud."
              : "Assistant unavailable (server has no API key)."}
          </p>
        </div>

        {/* Medical ID / handover pass */}
        {agent.medicalPassQr ? (
          <div className="border-2 border-black bg-white p-4">
            <div className="mb-3 text-xs font-black uppercase text-[#C62828]">
              Medical Handover Pass — Saved to History
            </div>
            <div className="flex justify-center">
              <img src={agent.medicalPassQr} alt="Medical Handover QR" className="h-52 w-52 border-2 border-black" />
            </div>
          </div>
        ) : (
          <div className="border-2 border-black bg-[#C62828]/10 p-4">
            <div className="mb-2 text-[10px] font-bold uppercase tracking-wide text-[#C62828]">Medical ID Pass</div>
            <div className="mb-2 text-sm font-black text-black">{name || "—"}</div>
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div>
                <div className="text-[9px] font-bold uppercase text-black/60">Age</div>
                <div className="font-black">{age || "—"}</div>
              </div>
              <div>
                <div className="text-[9px] font-bold uppercase text-black/60">Blood</div>
                <div className="font-black">{blood}</div>
              </div>
              <div>
                <div className="text-[9px] font-bold uppercase text-black/60">Allergies</div>
                <div className="font-black truncate">{allergies}</div>
              </div>
            </div>
            {conditions && (
              <div className="mt-2 text-xs">
                <span className="font-bold uppercase text-black/60">Conditions: </span>
                <span className="text-black">{conditions}</span>
              </div>
            )}
            <div className="mt-2 text-xs">
              <span className="font-bold uppercase text-black/60">Location: </span>
              <span className="text-black">
                {agent.address
                  ? agent.address
                  : agent.coords
                    ? "Locating address…"
                    : agent.gpsError
                      ? `GPS error: ${agent.gpsError}`
                      : "Acquiring GPS…"}
              </span>
              {agent.gpsAccuracy != null && <span className="ml-1 text-black/40">(±{agent.gpsAccuracy}m)</span>}
            </div>
            <div className="mt-3 flex items-center justify-between text-xs">
              <button onClick={agent.copyLocation} className="inline-flex items-center gap-1 font-bold uppercase text-black">
                <Copy size={12} />
                {agent.coords ? `${agent.coords.lat.toFixed(4)}, ${agent.coords.lng.toFixed(4)}` : "—"}
              </button>
              <button
                onClick={agent.refreshGps}
                className="inline-flex items-center gap-2 border-2 border-black bg-white px-3 py-1.5 text-[10px] font-bold uppercase"
              >
                <RefreshCw size={12} /> Refresh GPS
              </button>
            </div>
          </div>
        )}
      </main>

      <button
        onClick={() => {
          agent.endSession();
          navigate({ to: "/history" });
        }}
        className={pos}
      >
        End Emergency — View Pass &amp; History
      </button>
      <BottomNav />
    </div>
  );
}
