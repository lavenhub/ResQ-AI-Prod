import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Copy,
  Mic,
  MicOff,
  Phone,
  PhoneOff,
  QrCode,
  RefreshCw,
  Send,
} from "lucide-react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { AuthGuard } from "@/components/AuthGuard";
import { BottomNav } from "@/components/BottomNav";
import ResQAIWidget from "@/components/ResQAIWidget";
import SosAlertPanel from "@/components/SosAlertPanel";
import { useVoiceAgent } from "@/hooks/useVoiceAgent";
import { useBottomBarClasses } from "@/hooks/useBottomBarPos";
import { INDIAN_LANGUAGES } from "@/lib/types";

type VehicleType = "ambulance" | "fire" | "police";

export const Route = createFileRoute("/emergency")({
  head: () => ({
    meta: [
      { title: "Emergency — ResQ AI" },
      { name: "description", content: "Active emergency response with AI dispatcher." },
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

// ─── Leaflet Emergency Map ────────────────────────────────────────────────────
// Real interactive map with animated vehicle marker moving along actual coords.

const VEHICLE_EMOJI: Record<string, string> = {
  ambulance: "🚑",
  fire: "🚒",
  police: "🚔",
};
const VEHICLE_COLOR: Record<string, string> = {
  ambulance: "#C62828",
  fire: "#E65100",
  police: "#1565C0",
};
const TRAVEL_MS = 45_000; // 45 seconds travel time — clearly visible slow movement
const SPAWN_KM = 2.0; // spawn 2km away

function offsetLatLng(lat: number, lng: number, bearingDeg: number, km: number) {
  const R = 6371,
    d = km / R,
    b = (bearingDeg * Math.PI) / 180;
  const φ1 = (lat * Math.PI) / 180,
    λ1 = (lng * Math.PI) / 180;
  const φ2 = Math.asin(Math.sin(φ1) * Math.cos(d) + Math.cos(φ1) * Math.sin(d) * Math.cos(b));
  const λ2 =
    λ1 +
    Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(φ1), Math.cos(d) - Math.sin(φ1) * Math.sin(φ2));
  return { lat: (φ2 * 180) / Math.PI, lng: (λ2 * 180) / Math.PI };
}

function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t;
}
function ease(p: number) {
  return p < 0.5 ? 2 * p * p : -1 + (4 - 2 * p) * p;
}

interface EmergencyMapProps {
  userCoords: { lat: number; lng: number };
  vehicleType?: VehicleType;
  incidentType?: string;
}

function EmergencyMap({ userCoords, vehicleType, incidentType }: EmergencyMapProps) {
  const mapDivRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const userMarkerRef = useRef<L.Marker | null>(null);
  const vehicleMarkerRef = useRef<L.Marker | null>(null);
  const polylineRef = useRef<L.Polyline | null>(null);
  const rafRef = useRef<number | null>(null);
  const startRef = useRef<number | null>(null);
  const [eta, setEta] = useState<number | null>(null);
  const [arrived, setArrived] = useState(false);
  const spawnRef = useRef<{ lat: number; lng: number } | null>(null);

  // Initialise map once
  useEffect(() => {
    if (!mapDivRef.current || mapRef.current) return;

    const map = L.map(mapDivRef.current, {
      center: [userCoords.lat, userCoords.lng],
      zoom: 14,
      zoomControl: true,
      attributionControl: false,
    });

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
    }).addTo(map);

    // User pin
    const userIcon = L.divIcon({
      className: "",
      html: `<div style="
        width:36px;height:36px;border-radius:50%;
        background:#C62828;border:3px solid white;
        display:flex;align-items:center;justify-content:center;
        box-shadow:0 2px 8px rgba(0,0,0,0.4);
        font-size:16px;
      ">📍</div>
      <div style="
        position:absolute;top:38px;left:50%;transform:translateX(-50%);
        background:#C62828;color:white;font-size:9px;font-weight:900;
        padding:1px 4px;white-space:nowrap;
      ">YOU</div>`,
      iconSize: [36, 36],
      iconAnchor: [18, 18],
    });

    const userMarker = L.marker([userCoords.lat, userCoords.lng], { icon: userIcon }).addTo(map);
    userMarkerRef.current = userMarker;
    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []); // eslint-disable-line

  // Start vehicle animation when dispatched
  useEffect(() => {
    if (!vehicleType || !mapRef.current) return;

    const map = mapRef.current;
    const emoji = VEHICLE_EMOJI[vehicleType] ?? "🚑";
    const color = VEHICLE_COLOR[vehicleType] ?? "#C62828";
    const bearing = Math.floor(Math.random() * 360);
    const spawn = offsetLatLng(userCoords.lat, userCoords.lng, bearing, SPAWN_KM);
    spawnRef.current = spawn;

    // Vehicle marker
    const vehicleIcon = L.divIcon({
      className: "",
      html: `<div style="
        font-size:32px;filter:drop-shadow(0 3px 6px rgba(0,0,0,0.5));
        transition: transform 0.3s;
      " id="vehicle-emoji">${emoji}</div>`,
      iconSize: [40, 40],
      iconAnchor: [20, 20],
    });

    if (vehicleMarkerRef.current) vehicleMarkerRef.current.remove();
    const vehicleMarker = L.marker([spawn.lat, spawn.lng], { icon: vehicleIcon }).addTo(map);
    vehicleMarkerRef.current = vehicleMarker;

    // Dashed route line
    if (polylineRef.current) polylineRef.current.remove();
    const polyline = L.polyline(
      [
        [spawn.lat, spawn.lng],
        [userCoords.lat, userCoords.lng],
      ],
      { color, dashArray: "8 6", weight: 3, opacity: 0.7 },
    ).addTo(map);
    polylineRef.current = polyline;

    // Fit map to show both points
    map.fitBounds(
      L.latLngBounds([
        [spawn.lat, spawn.lng],
        [userCoords.lat, userCoords.lng],
      ]),
      { padding: [40, 40] },
    );

    setArrived(false);
    setEta(Math.round(TRAVEL_MS / 1000));

    // Use explicit start time — avoids stale ref issues
    const startTime = performance.now();

    // Animation loop
    const animate = (ts: number) => {
      const p = Math.min((ts - startTime) / TRAVEL_MS, 1);
      const ep = ease(p);

      const curLat = lerp(spawn.lat, userCoords.lat, ep);
      const curLng = lerp(spawn.lng, userCoords.lng, ep);

      vehicleMarker.setLatLng([curLat, curLng]);

      // Update dashed line to only show remaining path
      polyline.setLatLngs([
        [curLat, curLng],
        [userCoords.lat, userCoords.lng],
      ]);

      setEta(Math.max(0, Math.round(((1 - p) * TRAVEL_MS) / 1000)));

      if (p < 1) {
        rafRef.current = requestAnimationFrame(animate);
      } else {
        setArrived(true);
        polyline.remove();
        // Flash vehicle on user pin
        vehicleMarker.setLatLng([userCoords.lat, userCoords.lng]);
      }
    };

    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(animate);

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [vehicleType]); // eslint-disable-line

  const color = vehicleType ? VEHICLE_COLOR[vehicleType] : "#C62828";
  const emoji = vehicleType ? VEHICLE_EMOJI[vehicleType] : "";

  return (
    <div
      className="relative border-2 border-black bg-white overflow-hidden"
      style={{ height: 260 }}
    >
      <div ref={mapDivRef} className="absolute inset-0" />

      {/* ETA badge — only shown when vehicle is dispatched */}
      {vehicleType && (
        <div
          className="absolute top-2 left-2 z-[1000] border-2 border-white px-3 py-1.5 text-xs font-black uppercase text-white shadow-lg"
          style={{ background: color }}
        >
          {arrived ? `${emoji} On Scene (demo)` : `${emoji} ${eta}s away (demo)`}
        </div>
      )}

      {/* No GPS fallback */}
      {!userCoords && (
        <div className="absolute inset-0 flex items-center justify-center text-xs font-bold uppercase tracking-wider text-black/60 bg-white z-10">
          Acquiring GPS...
        </div>
      )}
    </div>
  );
}

// ─── VehicleStatusBar ─────────────────────────────────────────────────────────

function VehicleStatusBar({
  vehicleType,
  incidentType,
}: {
  vehicleType: VehicleType;
  incidentType: string;
}) {
  const emoji = VEHICLE_EMOJI[vehicleType] ?? "🚑";
  const color = VEHICLE_COLOR[vehicleType] ?? "#C62828";
  const label =
    vehicleType === "ambulance"
      ? "Ambulance"
      : vehicleType === "fire"
        ? "Fire Brigade"
        : "Police Van";
  return (
    <div
      className="flex items-center gap-3 border-2 border-black px-4 py-2"
      style={{ background: color }}
    >
      <span className="text-2xl">{emoji}</span>
      <div>
        <div className="text-xs font-black uppercase text-white">
          {label} — Simulated Route <span className="opacity-80">(demo)</span>
        </div>
        <div className="text-[10px] text-white/80">
          {incidentType}. This is a map demo — no real {label.toLowerCase()} has been dispatched.
          Call 112 / 108 for real help.
        </div>
      </div>
    </div>
  );
}

// ─── EmergencyPage ────────────────────────────────────────────────────────────

function EmergencyPage() {
  const navigate = useNavigate();
  const agent = useVoiceAgent();
  const pos = useBottomBarClasses(
    "border-t-2 border-black bg-[#C62828] py-4 text-sm font-black uppercase tracking-wide text-white",
  );
  const [language, setLanguage] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("resq_language") || '"English"');
    } catch {
      return "English";
    }
  });
  const [textInput, setTextInput] = useState("");
  const transcriptDivRef = useRef<HTMLDivElement>(null);

  const name = loadLocal("resq_name", "");
  const blood = loadLocal("resq_blood", "—");
  const age = loadLocal<number>("resq_age", 0);
  const allergies = loadLocal("resq_allergies", "—");
  const conditions = loadLocal("resq_conditions", "—");

  useEffect(() => {
    if (transcriptDivRef.current)
      transcriptDivRef.current.scrollTop = transcriptDivRef.current.scrollHeight;
  }, [agent.transcript]);

  const connecting = agent.status === "connecting";
  const inCall =
    agent.status === "listening" || agent.status === "thinking" || agent.status === "speaking";
  const active = inCall; // header / pill styling
  const phase = agent.status;

  const phaseLabel =
    phase === "speaking"
      ? "AI Dispatcher Speaking"
      : phase === "thinking"
        ? "Thinking…"
        : phase === "listening"
          ? "Listening — just speak"
          : connecting
            ? "Connecting to Dispatcher…"
            : "AI Dispatcher Standby";

  return (
    <div className="min-h-screen bg-white pb-32">
      {/* ── Header ── */}
      <header
        className={`sticky top-0 z-20 border-b-2 border-black px-4 py-3 ${active ? "resq-header-active bg-[#C62828] text-white" : "bg-white text-black"}`}
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
        {/* ── Language ── */}
        <div className="border-2 border-black bg-white p-3">
          <label className="mb-2 block text-[10px] font-bold uppercase tracking-wide text-black">
            Language
          </label>
          <select
            disabled={active || connecting}
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            className="w-full border-2 border-black bg-white px-3 py-2 text-sm font-semibold text-black disabled:opacity-50"
          >
            {INDIAN_LANGUAGES.map((l) => (
              <option key={l}>{l}</option>
            ))}
          </select>
        </div>

        {/* ── Leaflet Map — vehicle animates as real marker ── */}
        {agent.coords && (
          <EmergencyMap
            userCoords={agent.coords}
            vehicleType={agent.vehicleDispatch?.vehicleType}
            incidentType={agent.vehicleDispatch?.incidentType}
          />
        )}
        {!agent.coords && (
          <div
            className="flex items-center justify-center border-2 border-black bg-white text-xs font-bold uppercase tracking-wider text-black/60"
            style={{ height: 260 }}
          >
            Acquiring GPS...
          </div>
        )}

        {/* ── Vehicle status bar ── */}
        {agent.vehicleDispatch && (
          <VehicleStatusBar
            vehicleType={agent.vehicleDispatch.vehicleType}
            incidentType={agent.vehicleDispatch.incidentType}
          />
        )}

        {/* ── Real email alert to saved contacts (backend pipeline) ── */}
        <SosAlertPanel
          coords={agent.coords}
          address={agent.address}
          accuracy={agent.gpsAccuracy}
          incidentType={agent.incidentType !== "Emergency" ? agent.incidentType : undefined}
          victimName={name || undefined}
        />

        {/* ── Dispatcher control ── */}
        <div className="border-2 border-black bg-white p-4">
          <div className="flex items-center gap-2">
            <span
              className={`h-2.5 w-2.5 rounded-full ${
                phase === "speaking"
                  ? "bg-[#C62828] animate-pulse"
                  : phase === "listening"
                    ? "bg-green-600 animate-pulse"
                    : phase === "thinking"
                      ? "bg-amber-500 animate-pulse"
                      : connecting
                        ? "bg-amber-500"
                        : "bg-black/30"
              }`}
            />
            <span className="text-sm font-black uppercase text-black">{phaseLabel}</span>
          </div>
          <p className="mt-1 text-xs text-black/70">
            {inCall
              ? "This is a live call — talk naturally. You can interrupt the dispatcher any time; it will stop and listen."
              : connecting
                ? "Establishing secure live channel…"
                : "Tap below to start a hands-free live call with the AI dispatcher."}
          </p>
          <div className="my-4 flex h-12 items-end justify-center gap-1">
            {[0, 1, 2, 3, 4, 5, 6].map((i) => (
              <span
                key={i}
                className={`w-1.5 ${inCall || connecting ? "resq-wave-bar" : ""} ${
                  phase === "listening"
                    ? "bg-green-600"
                    : phase === "thinking"
                      ? "bg-amber-500"
                      : "bg-[#C62828]"
                }`}
                style={{
                  height: inCall || connecting ? "100%" : "10%",
                  animationDelay: `${i * 0.1}s`,
                }}
              />
            ))}
          </div>
          <button
            onClick={() => (inCall ? agent.terminateSession() : agent.startSession(language))}
            disabled={connecting}
            className={`flex w-full items-center justify-center gap-2 rounded border-2 border-black px-4 py-3 text-sm font-black uppercase tracking-wide text-white ${connecting ? "bg-black/40" : inCall ? "bg-black" : "bg-[#C62828]"}`}
          >
            {inCall ? <PhoneOff size={18} /> : <Phone size={18} />}
            {inCall ? "End Call" : connecting ? "Connecting…" : "Start Live Call"}
          </button>
          {inCall && (
            <button
              onClick={agent.toggleMute}
              className={`mt-3 flex w-full select-none items-center justify-center gap-2 rounded border-2 border-black px-4 py-4 text-base font-black uppercase tracking-wide transition-colors ${agent.isMuted ? "bg-[#C62828] text-white" : "bg-white text-black"}`}
            >
              {agent.isMuted ? <MicOff size={22} /> : <Mic size={22} />}
              {agent.isMuted ? "Muted — Tap to Speak" : "Mic Live — Tap to Mute"}
            </button>
          )}
          {inCall && (
            <div className="mt-3 flex gap-2">
              <input
                value={textInput}
                onChange={(e) => setTextInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && textInput.trim()) {
                    agent.sendTextMessage(textInput);
                    setTextInput("");
                  }
                }}
                placeholder="Type a message..."
                className="flex-1 border-2 border-black bg-white px-3 py-2 text-sm"
              />
              <button
                onClick={() => {
                  if (textInput.trim()) {
                    agent.sendTextMessage(textInput);
                    setTextInput("");
                  }
                }}
                className="flex items-center justify-center border-2 border-black bg-black px-3 text-white"
              >
                <Send size={16} />
              </button>
            </div>
          )}
        </div>

        {/* ── Hosted AI assistant (Tough Tongue AI) — browser-only, no backend needed ── */}
        <div className="border-2 border-black bg-white p-4">
          <div className="mb-1 text-[10px] font-bold uppercase tracking-wide text-[#C62828]">
            Hosted Voice Assistant
          </div>
          <p className="mb-3 text-xs text-black/70">
            Prefer a browser-only assistant? Talk to the hosted ResQ AI agent — it runs entirely in
            your browser and works even if the live dispatcher backend is offline.
          </p>
          <ResQAIWidget
            onOpen={() => {
              if (inCall) agent.terminateSession();
            }}
          />
        </div>

        {/* ── Live Transcript ── */}
        {agent.transcript.length > 0 && (
          <div className="border-2 border-black bg-white p-3">
            <div className="mb-2 text-[10px] font-bold uppercase tracking-wide text-black">
              Live Transcript
            </div>
            <div
              ref={transcriptDivRef}
              className="max-h-[200px] space-y-2 overflow-y-auto pr-1 text-sm"
            >
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
          </div>
        )}

        {/* ── Medical Handover Pass ── */}
        {agent.medicalPassQr ? (
          <div className="border-2 border-black bg-white p-4">
            <div className="mb-3 flex items-center gap-2 text-xs font-black uppercase text-[#C62828]">
              <QrCode size={16} /> Medical Handover Pass — Saved to History
            </div>
            <p className="mb-3 text-[11px] text-black/60">
              Scan this QR to open the full medical report PDF.
            </p>
            <div className="flex justify-center">
              <img
                src={agent.medicalPassQr}
                alt="Medical Handover QR"
                className="h-52 w-52 border-2 border-black"
              />
            </div>
            <button
              onClick={() => {
                const a = document.createElement("a");
                a.href = agent.medicalPassQr!;
                a.download = `resq-handover-${Date.now()}.png`;
                a.click();
              }}
              className="mt-3 flex w-full items-center justify-center gap-2 border-2 border-black bg-black px-4 py-2 text-xs font-black uppercase text-white"
            >
              <QrCode size={14} /> Download QR Pass
            </button>
          </div>
        ) : (
          <div className="border-2 border-black bg-[#C62828]/10 p-4">
            <div className="mb-2 text-[10px] font-bold uppercase tracking-wide text-[#C62828]">
              Medical ID Pass
            </div>
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
              {agent.gpsAccuracy != null && (
                <span className="ml-1 text-black/40">(±{agent.gpsAccuracy}m)</span>
              )}
            </div>
            <div className="mt-3 flex items-center justify-between text-xs">
              <span
                className={`border-2 border-black px-2 py-1 font-black uppercase ${active ? "bg-[#C62828] text-white" : "bg-white text-black"}`}
              >
                {active ? "Priority Red" : "Standby"}
              </span>
              <button
                onClick={agent.copyLocation}
                className="inline-flex items-center gap-1 font-bold uppercase text-black"
              >
                <Copy size={12} />
                {agent.coords
                  ? `${agent.coords.lat.toFixed(4)}, ${agent.coords.lng.toFixed(4)}`
                  : "—"}
              </button>
            </div>
            <button
              onClick={agent.refreshGps}
              className="mt-3 inline-flex items-center gap-2 border-2 border-black bg-white px-3 py-1.5 text-[10px] font-bold uppercase"
            >
              <RefreshCw size={12} /> Refresh GPS
            </button>
          </div>
        )}
      </main>

      {/* ── End Emergency ── */}
      <button
        onClick={() => {
          agent.terminateSession();
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
