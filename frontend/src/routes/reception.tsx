/**
 * /reception?booking=<id>
 *
 * The HOSPITAL / AMBULANCE RECEPTION side of a free, no-PSTN ambulance call.
 * A teammate opens this on any device, taps Answer, and the ResQAI AI
 * coordinator talks to them live over WebRTC (same transport as the in-app
 * dispatcher). No login required — the booking id in the URL is the capability.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { Ambulance, Mic, MicOff, Phone, PhoneOff } from "lucide-react";
import { apiUrl, getIceServers } from "@/lib/api";
import { receptionBookingInfo, type ReceptionBookingInfo } from "@/lib/ambulanceApi";

export const Route = createFileRoute("/reception")({
  head: () => ({
    meta: [
      { title: "Ambulance Reception — ResQ AI" },
      { name: "description", content: "Answer an incoming ResQAI ambulance-coordination call." },
    ],
  }),
  component: ReceptionPage,
});

type Line = { id: string; who: "ai" | "me" | "sys"; text: string };
type Phase = "idle" | "connecting" | "live" | "ended" | "error";

function uid() {
  return Math.random().toString(36).slice(2);
}

function ReceptionPage() {
  const [bookingId, setBookingId] = useState<string>("");
  const [info, setInfo] = useState<ReceptionBookingInfo | null>(null);
  const [infoErr, setInfoErr] = useState<string>("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [speaking, setSpeaking] = useState<"ai" | "me" | null>(null);
  const [muted, setMuted] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);

  const pcRef = useRef<RTCPeerConnection | null>(null);
  const micRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  // Read ?booking= from the URL and load the incoming-call context.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("booking") || "";
    setBookingId(id);
    if (!id) {
      setInfoErr("No booking id in the link.");
      return;
    }
    receptionBookingInfo(id)
      .then(setInfo)
      .catch((e) => setInfoErr(e?.message || "Could not load the incoming call."));
  }, []);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [lines]);

  const addLine = useCallback((who: Line["who"], text: string) => {
    setLines((p) => [...p, { id: uid(), who, text }]);
  }, []);

  const onRtvi = useCallback(
    (raw: string) => {
      let m: any;
      try {
        m = JSON.parse(raw);
      } catch {
        return;
      }
      const d = m.data || {};
      switch (m.type) {
        case "bot-started-speaking":
          setSpeaking("ai");
          break;
        case "bot-stopped-speaking":
          setSpeaking(null);
          break;
        case "user-started-speaking":
          setSpeaking("me");
          break;
        case "user-stopped-speaking":
          setSpeaking(null);
          break;
        case "user-transcription":
          if (d.final && d.text?.trim()) addLine("me", d.text.trim());
          break;
        case "bot-transcription":
          if (d.text?.trim()) addLine("ai", d.text.trim());
          break;
      }
    },
    [addLine],
  );

  const hangUp = useCallback(() => {
    try {
      pcRef.current?.close();
    } catch {}
    pcRef.current = null;
    micRef.current?.getTracks().forEach((t) => t.stop());
    micRef.current = null;
    if (audioRef.current) {
      try {
        audioRef.current.srcObject = null;
        audioRef.current.remove();
      } catch {}
      audioRef.current = null;
    }
    setSpeaking(null);
    setPhase((p) => (p === "live" || p === "connecting" ? "ended" : p));
  }, []);

  const answer = useCallback(async () => {
    if (!bookingId) return;
    setPhase("connecting");
    setLines([]);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      micRef.current = stream;

      const pc = new RTCPeerConnection({ iceServers: await getIceServers() });
      pcRef.current = pc;

      const dc = pc.createDataChannel("chat");
      dc.onmessage = (e) => onRtvi(e.data);
      pc.ondatachannel = (e) => {
        e.channel.onmessage = (ev) => onRtvi(ev.data);
      };

      stream.getTracks().forEach((t) => pc.addTrack(t, stream));

      const audio = document.createElement("audio");
      audio.autoplay = true;
      (audio as any).playsInline = true;
      audio.style.display = "none";
      document.body.appendChild(audio);
      audioRef.current = audio;
      pc.ontrack = (e) => {
        audio.srcObject = e.streams[0];
        audio.play().catch(() => {});
      };

      pc.onconnectionstatechange = () => {
        const s = pc.connectionState;
        if (s === "connected") {
          setPhase("live");
          addLine("sys", "Connected — the ResQAI assistant will speak first.");
        }
        if (s === "failed" || s === "closed") hangUp();
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

      const resp = await fetch(apiUrl("/api/ambulance/reception/offer"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sdp: pc.localDescription!.sdp,
          type: pc.localDescription!.type,
          booking_id: bookingId,
        }),
      });
      if (!resp.ok) {
        const t = await resp.text();
        throw new Error(`answer failed: ${resp.status} ${t}`);
      }
      await pc.setRemoteDescription(await resp.json());
    } catch (e: any) {
      addLine("sys", `Could not answer: ${e?.message || e}`);
      setPhase("error");
      hangUp();
    }
  }, [bookingId, onRtvi, addLine, hangUp]);

  const toggleMute = useCallback(() => {
    setMuted((m) => {
      const next = !m;
      micRef.current?.getAudioTracks().forEach((t) => (t.enabled = !next));
      return next;
    });
  }, []);

  useEffect(() => () => hangUp(), [hangUp]);

  const live = phase === "live" || phase === "connecting";

  return (
    <div className="min-h-screen bg-white">
      <header className="sticky top-0 z-20 flex items-center gap-2 border-b-2 border-black bg-[#C62828] px-4 py-3 text-white">
        <Ambulance size={22} strokeWidth={2.5} />
        <span className="text-base font-black uppercase tracking-wide">Ambulance Reception</span>
      </header>

      <main className="mx-auto max-w-[480px] space-y-4 px-4 py-5">
        {/* Incoming-call context */}
        <div className="border-2 border-black bg-white p-4">
          <div className="mb-2 text-[10px] font-bold uppercase tracking-wide text-black/60">
            Incoming ResQAI coordination call
          </div>
          {infoErr && <div className="text-xs font-bold text-[#C62828]">{infoErr}</div>}
          {info && (
            <div className="space-y-1 text-sm">
              {info.is_demo && (
                <span className="inline-block border-2 border-black bg-amber-300 px-2 py-0.5 text-[9px] font-black uppercase">
                  Demonstration
                </span>
              )}
              <Row k="Emergency" v={info.emergency_type || "—"} />
              <Row k="Patient condition" v={info.patient_condition || "—"} />
              <Row k="Patients" v={String(info.num_patients)} />
              <Row k="Pickup" v={info.pickup_address || "—"} />
            </div>
          )}
        </div>

        {/* Status + controls */}
        <div className="border-2 border-black bg-white p-4">
          <div className="mb-3 flex items-center gap-2">
            <span
              className={`h-2.5 w-2.5 rounded-full ${
                speaking === "ai"
                  ? "bg-[#C62828] animate-pulse"
                  : speaking === "me"
                    ? "bg-green-600 animate-pulse"
                    : phase === "live"
                      ? "bg-green-600"
                      : phase === "connecting"
                        ? "bg-amber-500 animate-pulse"
                        : "bg-black/30"
              }`}
            />
            <span className="text-sm font-black uppercase">
              {phase === "live"
                ? speaking === "ai"
                  ? "Assistant speaking"
                  : speaking === "me"
                    ? "Listening to you"
                    : "On the call"
                : phase === "connecting"
                  ? "Connecting…"
                  : phase === "ended"
                    ? "Call ended"
                    : phase === "error"
                      ? "Could not connect"
                      : "Ready to answer"}
            </span>
          </div>

          {phase === "idle" && (
            <button
              onClick={answer}
              disabled={!info?.answerable}
              className="flex w-full items-center justify-center gap-2 border-2 border-black bg-[#C62828] px-4 py-3 text-sm font-black uppercase tracking-wide text-white disabled:opacity-40"
            >
              <Phone size={18} /> Answer call
            </button>
          )}
          {!info?.answerable && phase === "idle" && info && (
            <p className="mt-2 text-[11px] font-bold uppercase text-black/50">
              This call is not currently answerable ({info.call_status}).
            </p>
          )}

          {live && (
            <div className="space-y-2">
              <button
                onClick={toggleMute}
                className={`flex w-full items-center justify-center gap-2 border-2 border-black px-4 py-3 text-sm font-black uppercase ${muted ? "bg-[#C62828] text-white" : "bg-white text-black"}`}
              >
                {muted ? <MicOff size={18} /> : <Mic size={18} />}
                {muted ? "Muted — tap to speak" : "Mic live — tap to mute"}
              </button>
              <button
                onClick={hangUp}
                className="flex w-full items-center justify-center gap-2 border-2 border-black bg-black px-4 py-3 text-sm font-black uppercase text-white"
              >
                <PhoneOff size={18} /> End call
              </button>
            </div>
          )}
          {(phase === "ended" || phase === "error") && (
            <p className="text-[11px] text-black/60">You can close this page.</p>
          )}
        </div>

        {/* Live transcript */}
        {lines.length > 0 && (
          <div className="border-2 border-black bg-white p-3">
            <div className="mb-2 text-[10px] font-bold uppercase tracking-wide">Live transcript</div>
            <div ref={logRef} className="max-h-[220px] space-y-2 overflow-y-auto pr-1 text-sm">
              {lines.map((l) => (
                <div key={l.id} className="leading-snug">
                  {l.who === "sys" ? (
                    <span className="text-xs italic text-black/50">{l.text}</span>
                  ) : (
                    <span>
                      <span
                        className={`mr-1 font-black uppercase ${l.who === "ai" ? "text-[#C62828]" : "text-black"}`}
                      >
                        {l.who === "ai" ? "RESQ:" : "YOU:"}
                      </span>
                      <span className="text-black">{l.text}</span>
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="font-bold uppercase text-black/50">{k}</span>
      <span className="text-right font-semibold">{v}</span>
    </div>
  );
}
