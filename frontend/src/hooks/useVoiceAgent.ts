import { useCallback, useEffect, useRef, useState } from "react";
import QRCode from "qrcode";
import { apiUrl, authHeader } from "@/lib/api";
import { triggerSos } from "@/lib/emergencyApi";
import type { TranscriptEntry } from "@/lib/types";

function uid() {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/** Short incident label from text (drives email + which vehicle is shown) */
function detectIncidentType(text: string): string {
  const t = text.toLowerCase();
  if (/heart|chest|cardiac/.test(t)) return "Heart Attack";
  if (/snake|bit|bitten|bite|sting/.test(t)) return "Snakebite";
  if (/bleed|blood|wound/.test(t)) return "Bleeding";
  if (/fire|burn|smoke/.test(t)) return "Fire";
  if (/accident|crash|car|road/.test(t)) return "Road Accident";
  if (/chok|breath|throat/.test(t)) return "Choking";
  if (/fall|fell|fracture|bone/.test(t)) return "Fall / Fracture";
  if (/theft|rob|attack|knife|weapon/.test(t)) return "Crime / Assault";
  return "Emergency";
}

/** Map incident → which simulated vehicle to show on the map */
function vehicleForIncident(incident: string): "ambulance" | "fire" | "police" {
  const t = incident.toLowerCase();
  if (/fire|burn|smoke|gas|explos/.test(t)) return "fire";
  if (/theft|rob|assault|attack|knife|weapon|crime|threat/.test(t)) return "police";
  return "ambulance";
}

function isEmergencyText(text: string): boolean {
  const t = text.toLowerCase();
  return /\bi am\b|\bi'm\b|\bi was\b|\bi have\b|\bhelp\b|\binjured\b|\bhurt\b|\bbleeding\b|\baccident\b|\bcrash\b|\bfire\b|\bchest pain\b|\bbreathe\b|\bfell\b|\bstuck\b|\bemergency\b|\bsnake\b|\bbit|\bpain\b|\bburn|\bchoking\b|\bunconscious\b|\bcollapse\b|\bdrown/.test(
    t,
  );
}

function loadLocal<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v ? (JSON.parse(v) as T) : fallback;
  } catch {
    return fallback;
  }
}

/** BCP-47 code for browser speech, from the app's language name. */
function speechLang(language: string): string {
  const map: Record<string, string> = {
    English: "en-IN",
    Hindi: "hi-IN",
    Marathi: "mr-IN",
    Bengali: "bn-IN",
    Telugu: "te-IN",
    Tamil: "ta-IN",
    Gujarati: "gu-IN",
    Kannada: "kn-IN",
    Malayalam: "ml-IN",
    Punjabi: "pa-IN",
  };
  return map[language] || "en-IN";
}

/** Strip simple markdown so TTS doesn't read asterisks/underscores aloud. */
function forSpeech(text: string): string {
  return text.replace(/[*_`#>]/g, "").replace(/\s+/g, " ").trim();
}

export interface HistoryEntry {
  id: string;
  date: string;
  incidentType: string;
  duration: string;
  transcript: TranscriptEntry[];
  qrDataUrl: string;
  location: { lat: number; lng: number } | null;
}

type ChatStatus = "idle" | "thinking" | "speaking";

export function useVoiceAgent() {
  const [status, setStatus] = useState<ChatStatus>("idle");
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [hasApiKey, setHasApiKey] = useState<boolean>(true);
  const [coords, setCoords] = useState<{ lat: number; lng: number } | null>(null);
  const [gpsError, setGpsError] = useState<string | null>(null);
  const [gpsAccuracy, setGpsAccuracy] = useState<number | null>(null);
  const [address, setAddress] = useState<string | null>(null);
  const [language, setLanguage] = useState<string>(() => loadLocal("resq_language", "English"));
  const [medicalPassQr, setMedicalPassQr] = useState<string | null>(null);
  const [incidentType, setIncidentType] = useState<string>("Emergency");
  const [emergencyActive, setEmergencyActive] = useState(false);
  const [alertSent, setAlertSent] = useState<{ count: number; detail?: string } | null>(null);
  const [vehicleDispatch, setVehicleDispatch] = useState<{
    vehicleType: "ambulance" | "fire" | "police";
    incidentType: string;
    userCoords: { lat: number; lng: number };
  } | null>(null);

  // Speech
  const [speakEnabled, setSpeakEnabled] = useState<boolean>(() => loadLocal("resq_speak", true));
  const [listening, setListening] = useState(false);
  const sttSupported =
    typeof window !== "undefined" &&
    !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);

  // refs
  const coordsRef = useRef<{ lat: number; lng: number } | null>(null);
  const addressRef = useRef<string | null>(null);
  const incidentRef = useRef<string>("Emergency");
  const dispatchedRef = useRef(false);
  const transcriptRef = useRef<TranscriptEntry[]>([]);
  const sessionStartRef = useRef<number>(Date.now());
  const recognitionRef = useRef<any>(null);
  const languageRef = useRef<string>(language);

  useEffect(() => {
    languageRef.current = language;
    try {
      localStorage.setItem("resq_language", JSON.stringify(language));
    } catch {
      /* ignore */
    }
  }, [language]);

  useEffect(() => {
    try {
      localStorage.setItem("resq_speak", JSON.stringify(speakEnabled));
    } catch {
      /* ignore */
    }
  }, [speakEnabled]);

  const addEntry = useCallback((sender: TranscriptEntry["sender"], text: string) => {
    setTranscript((p) => {
      const next = [...p, { id: uid(), sender, text, timestamp: new Date().toISOString() }];
      transcriptRef.current = next;
      return next;
    });
  }, []);

  // ── GPS ─────────────────────────────────────────────────────────────────────
  const reverseGeocode = useCallback(async (lat: number, lng: number) => {
    try {
      const r = await fetch(
        `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat}&lon=${lng}&zoom=18&addressdetails=1`,
        { headers: { "Accept-Language": "en" } },
      );
      const d = await r.json();
      const a = d.address || {};
      const parts = [
        a.road || a.neighbourhood || a.suburb,
        a.suburb && a.suburb !== a.road ? a.suburb : null,
        a.city || a.town || a.village || a.county,
        a.postcode,
      ].filter(Boolean);
      const nice = parts.length ? Array.from(new Set(parts)).join(", ") : d.display_name;
      if (nice) {
        setAddress(nice);
        addressRef.current = nice;
      }
    } catch {
      /* keep coords-only */
    }
  }, []);

  const refreshGps = useCallback(() => {
    if (!navigator.geolocation) {
      setGpsError("Geolocation unsupported");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const c = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        setCoords(c);
        coordsRef.current = c;
        setGpsAccuracy(Math.round(pos.coords.accuracy));
        setGpsError(null);
        reverseGeocode(c.lat, c.lng);
      },
      (err) => setGpsError(err.message),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  }, [reverseGeocode]);

  useEffect(() => {
    fetch(apiUrl("/api/status"))
      .then((r) => r.json())
      .then((d) => setHasApiKey(!!d.hasApiKey))
      .catch(() => setHasApiKey(false));
    refreshGps();
  }, [refreshGps]);

  // ── Text-to-speech ──────────────────────────────────────────────────────────
  const speak = useCallback(
    (text: string) => {
      if (!speakEnabled || typeof window === "undefined" || !window.speechSynthesis) return;
      try {
        window.speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(forSpeech(text));
        u.lang = speechLang(languageRef.current);
        u.rate = 1.02;
        u.onstart = () => setStatus("speaking");
        u.onend = () => setStatus("idle");
        u.onerror = () => setStatus("idle");
        window.speechSynthesis.speak(u);
      } catch {
        /* ignore */
      }
    },
    [speakEnabled],
  );

  // ── QR / history ────────────────────────────────────────────────────────────
  const generateMedicalQr = useCallback(
    async (incType: string, snap: TranscriptEntry[], location: { lat: number; lng: number } | null) => {
      const passData = {
        name: loadLocal("resq_name", ""),
        phone: localStorage.getItem("resq_phone") || "",
        blood: loadLocal("resq_blood", ""),
        age: loadLocal("resq_age", ""),
        allergies: loadLocal("resq_allergies", ""),
        conditions: loadLocal("resq_conditions", ""),
        incident: incType,
        date: new Date().toISOString(),
        location: location ? `${location.lat.toFixed(5)},${location.lng.toFixed(5)}` : "Unknown",
        address: addressRef.current || "",
        summary: snap
          .filter((t) => t.sender !== "system")
          .slice(-8)
          .map((t) => `${t.sender === "user" ? "Patient" : "Dispatcher"}: ${t.text}`)
          .join(" | "),
      };
      const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(passData))));
      const passUrl = `${window.location.origin}/medical-pass?data=${encoded}`;
      return QRCode.toDataURL(passUrl, {
        width: 400,
        margin: 2,
        errorCorrectionLevel: "M",
        color: { dark: "#111111", light: "#ffffff" },
      });
    },
    [],
  );

  const endSession = useCallback(async () => {
    try {
      window.speechSynthesis?.cancel();
    } catch {}
    const snap = transcriptRef.current;
    if (snap.some((t) => t.sender !== "system")) {
      const qr = await generateMedicalQr(incidentRef.current, snap, coordsRef.current);
      setMedicalPassQr(qr);
      const elapsed = Math.floor((Date.now() - sessionStartRef.current) / 1000);
      const entry: HistoryEntry = {
        id: uid(),
        date: new Date().toISOString(),
        incidentType: incidentRef.current,
        duration: `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`,
        transcript: snap,
        qrDataUrl: qr,
        location: coordsRef.current,
      };
      const existing: HistoryEntry[] = loadLocal("resq_history", []);
      localStorage.setItem("resq_history", JSON.stringify([entry, ...existing].slice(0, 50)));
      return qr;
    }
    return null;
  }, [generateMedicalQr]);

  // ── Emergency trigger (real email + simulated ambulance) ────────────────────
  const triggerEmergency = useCallback(
    async (type?: string) => {
      const inc = type || incidentRef.current || "Emergency";
      setIncidentType(inc);
      incidentRef.current = inc;
      setEmergencyActive(true);

      // Mock ambulance on the map (clearly labelled "demo" in the UI).
      if (coordsRef.current) {
        setVehicleDispatch({
          vehicleType: vehicleForIncident(inc),
          incidentType: inc,
          userCoords: coordsRef.current,
        });
      }
      if (dispatchedRef.current) return; // email only once per session
      dispatchedRef.current = true;

      // REAL: alert the caller's verified contacts by email via the SOS pipeline.
      try {
        const res = await triggerSos({
          incident_type: inc,
          victim_name: loadLocal("resq_name", "") || undefined,
          latitude: coordsRef.current?.lat,
          longitude: coordsRef.current?.lng,
          address: addressRef.current || undefined,
          idempotency_key: `app-${sessionStartRef.current}`,
        });
        setAlertSent({ count: res.notifications.length });
        addEntry(
          "system",
          `✅ Emergency recorded. Notifying ${res.notifications.length} emergency contact(s) by email. 🚑 Simulated ambulance shown on the map (demo — call 112/108 for real dispatch).`,
        );
      } catch (e: any) {
        const msg = String(e?.message || e);
        const status = e?.status;
        if (status === 401) {
          setAlertSent({ count: 0, detail: "not signed in" });
          addEntry("system", "⚠️ Sign in to alert your saved emergency contacts.");
        } else if (status === 409) {
          setAlertSent({ count: 0, detail: "no verified contacts" });
          addEntry(
            "system",
            "⚠️ No verified emergency contacts to email. Add one in Profile. (Simulated ambulance still shown for the demo.)",
          );
        } else {
          addEntry("system", `⚠️ Could not send contact alerts: ${msg}`);
        }
      }
    },
    [addEntry],
  );

  // ── Send a chat message to the agent ────────────────────────────────────────
  const sendMessage = useCallback(
    async (text: string) => {
      const msg = text.trim();
      if (!msg || status === "thinking") return;
      addEntry("user", msg);

      // Detect an emergency from the user's words → fire the real email + sim once.
      if (isEmergencyText(msg)) {
        const inc = detectIncidentType(msg);
        incidentRef.current = inc;
        setIncidentType(inc);
        void triggerEmergency(inc);
      }

      setStatus("thinking");
      try {
        const history = transcriptRef.current
          .filter((t) => t.sender === "user" || t.sender === "resq")
          .map((t) => ({ role: t.sender === "user" ? "user" : "assistant", content: t.text }));
        history.push({ role: "user", content: msg });

        const res = await fetch(apiUrl("/api/agent/chat"), {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeader() },
          body: JSON.stringify({
            messages: history,
            language: languageRef.current,
            lat: coordsRef.current?.lat,
            lng: coordsRef.current?.lng,
            address: addressRef.current,
          }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data?.detail || `Request failed: ${res.status}`);
        const reply = data.reply as string;
        addEntry("resq", reply);
        speak(reply);
        if (!speakEnabled) setStatus("idle");
      } catch (e: any) {
        setStatus("idle");
        addEntry("system", `⚠️ Assistant unavailable: ${e?.message || e}`);
      }
    },
    [status, addEntry, triggerEmergency, speak, speakEnabled],
  );

  // ── Speech-to-text (browser) ────────────────────────────────────────────────
  const startListening = useCallback(() => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) return;
    try {
      window.speechSynthesis?.cancel();
      const rec = new SR();
      rec.lang = speechLang(languageRef.current);
      rec.interimResults = false;
      rec.maxAlternatives = 1;
      rec.continuous = false;
      rec.onstart = () => setListening(true);
      rec.onerror = () => setListening(false);
      rec.onend = () => setListening(false);
      rec.onresult = (ev: any) => {
        const said = ev.results?.[0]?.[0]?.transcript?.trim();
        setListening(false);
        if (said) void sendMessage(said);
      };
      recognitionRef.current = rec;
      rec.start();
    } catch {
      setListening(false);
    }
  }, [sendMessage]);

  const stopListening = useCallback(() => {
    try {
      recognitionRef.current?.stop();
    } catch {}
    setListening(false);
  }, []);

  const copyLocation = useCallback(async () => {
    if (!coordsRef.current) return;
    await navigator.clipboard.writeText(
      `${coordsRef.current.lat.toFixed(6)}, ${coordsRef.current.lng.toFixed(6)}`,
    );
  }, []);

  useEffect(() => {
    return () => {
      try {
        window.speechSynthesis?.cancel();
        recognitionRef.current?.stop();
      } catch {}
    };
  }, []);

  return {
    status,
    transcript,
    hasApiKey,
    coords,
    gpsError,
    gpsAccuracy,
    address,
    language,
    setLanguage,
    incidentType,
    emergencyActive,
    vehicleDispatch,
    alertSent,
    medicalPassQr,
    speakEnabled,
    setSpeakEnabled,
    sttSupported,
    listening,
    startListening,
    stopListening,
    sendMessage,
    triggerEmergency,
    endSession,
    refreshGps,
    copyLocation,
  };
}
