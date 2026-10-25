import { useCallback, useEffect, useRef, useState } from "react";
import QRCode from "qrcode";
import { apiUrl, authHeader } from "@/lib/api";
import type { TranscriptEntry, VoiceStatus } from "@/lib/types";

function uid() {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/** Short incident label from text (drives email + which vehicle is dispatched) */
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

/** Map incident → dispatched vehicle */
function vehicleForIncident(incident: string): {
  type: "ambulance" | "fire" | "police";
  label: string;
} {
  const t = incident.toLowerCase();
  if (/fire|burn|smoke|gas|explos/.test(t)) return { type: "fire", label: "🚒 Fire Brigade" };
  if (/theft|rob|assault|attack|knife|weapon|crime|threat/.test(t))
    return { type: "police", label: "🚔 Police Van" };
  return { type: "ambulance", label: "🚑 Ambulance" };
}

function isEmergencyText(text: string): boolean {
  const t = text.toLowerCase();
  return /\bi am\b|\bi'm\b|\bi was\b|\bi have\b|\bhelp\b|\binjured\b|\bhurt\b|\bbleeding\b|\baccident\b|\bcrash\b|\bfire\b|\bchest pain\b|\bbreathe\b|\bfell\b|\bstuck\b|\bemergency\b|\bsnake\b|\bbit|\bpain\b|\bburn/.test(
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

export interface HistoryEntry {
  id: string;
  date: string;
  incidentType: string;
  duration: string;
  transcript: TranscriptEntry[];
  qrDataUrl: string;
  location: { lat: number; lng: number } | null;
}

export function useVoiceAgent() {
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [hasApiKey, setHasApiKey] = useState<boolean>(true);
  const [coords, setCoords] = useState<{ lat: number; lng: number } | null>(null);
  const [gpsError, setGpsError] = useState<string | null>(null);
  const [gpsAccuracy, setGpsAccuracy] = useState<number | null>(null);
  const [address, setAddress] = useState<string | null>(null);
  const [isMuted, setIsMuted] = useState(false);
  const [medicalPassQr, setMedicalPassQr] = useState<string | null>(null);
  const [incidentType, setIncidentType] = useState<string>("Emergency");
  const [vehicleDispatch, setVehicleDispatch] = useState<{
    vehicleType: "ambulance" | "fire" | "police";
    incidentType: string;
    userCoords: { lat: number; lng: number };
  } | null>(null);

  // ── refs ──────────────────────────────────────────────────────────────────
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const audioElRef = useRef<HTMLAudioElement | null>(null);
  const sessionStartRef = useRef<number>(0);
  const transcriptRef = useRef<TranscriptEntry[]>([]);
  const dispatchedRef = useRef<boolean>(false);
  const coordsRef = useRef<{ lat: number; lng: number } | null>(null);
  const addressRef = useRef<string | null>(null);
  const incidentRef = useRef<string>("Emergency");

  const addEntry = useCallback((sender: TranscriptEntry["sender"], text: string) => {
    setTranscript((p) => {
      const next = [...p, { id: uid(), sender, text, timestamp: new Date().toISOString() }];
      transcriptRef.current = next;
      return next;
    });
  }, []);

  // Reverse-geocode coordinates -> human-readable address (free OpenStreetMap Nominatim)
  const reverseGeocode = useCallback(async (lat: number, lng: number) => {
    try {
      const r = await fetch(
        `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat}&lon=${lng}&zoom=18&addressdetails=1`,
        { headers: { "Accept-Language": "en" } },
      );
      const d = await r.json();
      const a = d.address || {};
      // Build a concise, readable address rather than the full display_name
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
      /* keep coords-only if geocoding fails */
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

  // ── QR / history ──────────────────────────────────────────────────────────
  const generateMedicalQr = useCallback(
    async (
      incType: string,
      sessionTranscript: TranscriptEntry[],
      location: { lat: number; lng: number } | null,
    ): Promise<string> => {
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
        summary: sessionTranscript
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

  const saveToHistory = useCallback(
    async (
      incType: string,
      sessionTranscript: TranscriptEntry[],
      startTime: number,
      location: { lat: number; lng: number } | null,
    ) => {
      const qrDataUrl = await generateMedicalQr(incType, sessionTranscript, location);
      const elapsed = Math.floor((Date.now() - startTime) / 1000);
      const entry: HistoryEntry = {
        id: uid(),
        date: new Date().toISOString(),
        incidentType: incType,
        duration: `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`,
        transcript: sessionTranscript,
        qrDataUrl,
        location,
      };
      const existing: HistoryEntry[] = loadLocal("resq_history", []);
      localStorage.setItem("resq_history", JSON.stringify([entry, ...existing].slice(0, 50)));
      return qrDataUrl;
    },
    [generateMedicalQr],
  );

  // ── Emergency side-effects (email + vehicle), fired from the transcript ─────
  const handleEmergencyText = useCallback(
    (userText: string) => {
      if (!isEmergencyText(userText)) return;
      const inc = detectIncidentType(userText);
      setIncidentType(inc);
      incidentRef.current = inc;

      // Vehicle dispatch (once)
      if (!dispatchedRef.current && coordsRef.current) {
        dispatchedRef.current = true;
        const v = vehicleForIncident(inc);
        setVehicleDispatch({
          vehicleType: v.type,
          incidentType: inc,
          userCoords: coordsRef.current,
        });
        // Honesty: this is a MAP SIMULATION only. ResQAI does not dispatch real
        // vehicles, so the message must not imply one is actually coming.
        addEntry(
          "system",
          `🧭 Demo: showing a simulated ${v.label} route on the map. No real vehicle has been dispatched — call 112 / 108 for real help.`,
        );
      }

      // Contact alerting is handled by the real server-side EMAIL pipeline (see the
      // "Alert Emergency Contacts" panel) and the voice agent's trigger_sos_alert
      // tool — NOT by a browser-side email send (no provider creds in the bundle).
      // We only detect the incident type here to prefill that panel.
    },
    [addEntry],
  );

  // ── RTVI data-channel messages from the bot ─────────────────────────────────
  const handleRtvi = useCallback(
    (raw: string) => {
      let msg: any;
      try {
        msg = JSON.parse(raw);
      } catch {
        return;
      }
      const data = msg.data || {};
      switch (msg.type) {
        case "user-started-speaking":
          setStatus("listening");
          break;
        case "user-stopped-speaking":
          setStatus("thinking");
          break;
        case "bot-started-speaking":
          setStatus("speaking");
          break;
        case "bot-stopped-speaking":
          setStatus("listening");
          break;
        case "user-transcription":
          if (data.final && data.text?.trim()) {
            addEntry("user", data.text.trim());
            handleEmergencyText(data.text);
          }
          break;
        case "bot-transcription":
          if (data.text?.trim()) addEntry("resq", data.text.trim());
          break;
      }
    },
    [addEntry, handleEmergencyText],
  );

  // ── Terminate ───────────────────────────────────────────────────────────────
  const terminateSession = useCallback(() => {
    try {
      pcRef.current?.close();
    } catch {}
    pcRef.current = null;
    micStreamRef.current?.getTracks().forEach((t) => t.stop());
    micStreamRef.current = null;
    if (audioElRef.current) {
      try {
        audioElRef.current.srcObject = null;
        audioElRef.current.remove();
      } catch {}
      audioElRef.current = null;
    }

    const snap = transcriptRef.current;
    if (snap.some((t) => t.sender !== "system")) {
      saveToHistory(incidentRef.current, snap, sessionStartRef.current, coordsRef.current).then(
        (qr) => setMedicalPassQr(qr),
      );
    }
    setIsMuted(false);
    setStatus("idle");
  }, [saveToHistory]);

  // ── Start the WebRTC call ────────────────────────────────────────────────────
  const startSession = useCallback(
    async (language: string) => {
      try {
        setStatus("connecting");
        setTranscript([]);
        transcriptRef.current = [];
        setMedicalPassQr(null);
        setVehicleDispatch(null);
        setIncidentType("Emergency");
        incidentRef.current = "Emergency";
        dispatchedRef.current = false;
        sessionStartRef.current = Date.now();

        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
        micStreamRef.current = stream;

        const pc = new RTCPeerConnection({
          iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
        });
        pcRef.current = pc;

        // Data channel for RTVI messages (transcripts, speaking state)
        const dc = pc.createDataChannel("chat");
        dc.onmessage = (e) => handleRtvi(e.data);
        pc.ondatachannel = (e) => {
          e.channel.onmessage = (ev) => handleRtvi(ev.data);
        };

        stream.getTracks().forEach((t) => pc.addTrack(t, stream));

        // Play the bot's audio
        const audioEl = document.createElement("audio");
        audioEl.autoplay = true;
        (audioEl as any).playsInline = true;
        audioEl.style.display = "none";
        document.body.appendChild(audioEl);
        audioElRef.current = audioEl;
        pc.ontrack = (e) => {
          audioEl.srcObject = e.streams[0];
          audioEl.play().catch(() => {});
        };

        pc.onconnectionstatechange = () => {
          const s = pc.connectionState;
          if (s === "connected") {
            setStatus("listening");
            addEntry("system", "Dispatcher on the line — just speak.");
          }
          if (s === "failed" || s === "closed") setStatus("idle");
        };

        await pc.setLocalDescription(await pc.createOffer());
        await new Promise<void>((resolve) => {
          if (pc.iceGatheringState === "complete") return resolve();
          const check = () => {
            if (pc.iceGatheringState === "complete") {
              pc.removeEventListener("icegatheringstatechange", check);
              resolve();
            }
          };
          pc.addEventListener("icegatheringstatechange", check);
          setTimeout(resolve, 2000);
        });

        const resp = await fetch(apiUrl("/api/rtc/offer"), {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeader() },
          body: JSON.stringify({
            sdp: pc.localDescription!.sdp,
            type: pc.localDescription!.type,
            language,
            lat: coordsRef.current?.lat,
            lng: coordsRef.current?.lng,
            address: addressRef.current,
          }),
        });
        if (!resp.ok) throw new Error(`offer failed: ${resp.status}`);
        await pc.setRemoteDescription(await resp.json());
      } catch (e: any) {
        setStatus("error");
        addEntry("system", `Failed to start: ${e?.message || e}`);
      }
    },
    [addEntry, handleRtvi],
  );

  // ── Mute toggle ───────────────────────────────────────────────────────────
  const toggleMute = useCallback(() => {
    setIsMuted((m) => {
      const next = !m;
      micStreamRef.current?.getAudioTracks().forEach((t) => {
        t.enabled = !next;
      });
      return next;
    });
  }, []);

  // ── Text input (fallback demo path — triggers alerts without voice) ─────────
  const sendTextMessage = useCallback(
    async (text: string) => {
      if (!text.trim()) return;
      addEntry("user", text);
      handleEmergencyText(text);
    },
    [addEntry, handleEmergencyText],
  );

  const copyLocation = useCallback(async () => {
    if (!coordsRef.current) return;
    await navigator.clipboard.writeText(
      `${coordsRef.current.lat.toFixed(6)}, ${coordsRef.current.lng.toFixed(6)}`,
    );
  }, []);

  useEffect(() => {
    return () => {
      terminateSession();
    };
  }, []); // eslint-disable-line

  return {
    status,
    transcript,
    hasApiKey,
    coords,
    gpsError,
    gpsAccuracy,
    address,
    isMuted,
    medicalPassQr,
    incidentType,
    vehicleDispatch,
    startSession,
    terminateSession,
    toggleMute,
    sendTextMessage,
    copyLocation,
    refreshGps,
  };
}
