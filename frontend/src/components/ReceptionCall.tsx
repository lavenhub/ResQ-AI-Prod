import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Mic, MicOff, PhoneOff, Send, Volume2, VolumeX, XCircle } from "lucide-react";
import {
  type Booking,
  type TranscriptItem,
  BOOKING_STATE_LABEL,
  callFinalize,
  callOpen,
  callTurn,
} from "@/lib/ambulanceApi";

function speechLang(language: string): string {
  const map: Record<string, string> = {
    English: "en-IN", Hindi: "hi-IN", Marathi: "mr-IN", Bengali: "bn-IN", Telugu: "te-IN",
    Tamil: "ta-IN", Gujarati: "gu-IN", Kannada: "kn-IN", Malayalam: "ml-IN", Punjabi: "pa-IN",
  };
  return map[language] || "en-IN";
}
function forSpeech(t: string) {
  return t.replace(/[*_`#>]/g, "").replace(/\s+/g, " ").trim();
}

interface Props {
  bookingId: string;
  serviceName: string;
  language: string;
  onClose: () => void;
  onBooked: (b: Booking) => void;
}

/**
 * The hospital RECEPTION side of the simulated ambulance call. The AI coordinator
 * (ResQ) leads — its lines are shown and spoken aloud. YOU reply as the
 * receptionist (type or tap the mic). "End call" records the structured outcome.
 */
export function ReceptionCall({ bookingId, serviceName, language, onClose, onBooked }: Props) {
  const [turns, setTurns] = useState<TranscriptItem[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [speakOn, setSpeakOn] = useState(true);
  const [listening, setListening] = useState(false);
  const [outcome, setOutcome] = useState<Booking | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const recRef = useRef<any>(null);
  const openedRef = useRef(false);

  const sttSupported =
    typeof window !== "undefined" &&
    !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);

  const speak = useCallback(
    (text: string) => {
      if (!speakOn || typeof window === "undefined" || !window.speechSynthesis) return;
      try {
        window.speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(forSpeech(text));
        u.lang = speechLang(language);
        u.rate = 1.02;
        window.speechSynthesis.speak(u);
      } catch {
        /* ignore */
      }
    },
    [speakOn, language],
  );

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [turns]);

  // Open the call once (AI speaks first).
  useEffect(() => {
    if (openedRef.current) return;
    openedRef.current = true;
    setBusy(true);
    callOpen(bookingId)
      .then(({ reply }) => {
        setTurns([{ role: "ai", text: reply }]);
        speak(reply);
      })
      .catch((e) => setErr(e?.message || "Could not start the call"))
      .finally(() => setBusy(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookingId]);

  const sendReceptionist = useCallback(
    async (text: string) => {
      const said = text.trim();
      if (!said || busy || outcome) return;
      const next = [...turns, { role: "receptionist" as const, text: said }];
      setTurns(next);
      setInput("");
      setBusy(true);
      setErr(null);
      try {
        const { reply } = await callTurn(bookingId, next);
        setTurns((p) => [...p, { role: "ai", text: reply }]);
        speak(reply);
      } catch (e: any) {
        setErr(e?.message || "Assistant error");
      } finally {
        setBusy(false);
      }
    },
    [turns, busy, outcome, bookingId, speak],
  );

  const toggleMic = useCallback(() => {
    if (listening) {
      try {
        recRef.current?.stop();
      } catch {}
      setListening(false);
      return;
    }
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) return;
    try {
      window.speechSynthesis?.cancel();
      const rec = new SR();
      rec.lang = speechLang(language);
      rec.interimResults = false;
      rec.continuous = false;
      rec.onstart = () => setListening(true);
      rec.onerror = () => setListening(false);
      rec.onend = () => setListening(false);
      rec.onresult = (ev: any) => {
        const said = ev.results?.[0]?.[0]?.transcript?.trim();
        setListening(false);
        if (said) void sendReceptionist(said);
      };
      recRef.current = rec;
      rec.start();
    } catch {
      setListening(false);
    }
  }, [listening, language, sendReceptionist]);

  const endCall = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      window.speechSynthesis?.cancel();
      const b = await callFinalize(bookingId, turns);
      setOutcome(b);
      if (b.booking_state === "BOOKING_CONFIRMED") onBooked(b);
    } catch (e: any) {
      setErr(e?.message || "Could not record the booking");
    } finally {
      setBusy(false);
    }
  }, [bookingId, turns, onBooked]);

  useEffect(() => {
    return () => {
      try {
        window.speechSynthesis?.cancel();
        recRef.current?.stop();
      } catch {}
    };
  }, []);

  const confirmed = outcome?.booking_state === "BOOKING_CONFIRMED";
  const declined = outcome?.booking_state === "BOOKING_DECLINED";

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 sm:items-center">
      <div className="flex max-h-[92vh] w-full max-w-[480px] flex-col border-2 border-black bg-white">
        {/* Header */}
        <div className="flex items-center justify-between border-b-2 border-black bg-[#C62828] px-4 py-3 text-white">
          <div className="min-w-0">
            <div className="text-xs font-black uppercase tracking-wide">📞 Reception call (demo)</div>
            <div className="truncate text-[11px] text-white/80">
              AI is calling {serviceName} — you are the receptionist
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setSpeakOn((s) => !s)}
              title={speakOn ? "Voice on" : "Voice off"}
              className="flex h-8 w-8 items-center justify-center border-2 border-white"
            >
              {speakOn ? <Volume2 size={16} /> : <VolumeX size={16} />}
            </button>
            <button
              onClick={() => {
                window.speechSynthesis?.cancel();
                onClose();
              }}
              title="Hang up"
              className="flex h-8 w-8 items-center justify-center border-2 border-white"
            >
              <PhoneOff size={16} />
            </button>
          </div>
        </div>

        {/* Transcript */}
        <div ref={logRef} className="flex-1 space-y-2 overflow-y-auto px-4 py-3 text-sm">
          {turns.map((t, i) => (
            <div key={i} className={`flex ${t.role === "ai" ? "justify-start" : "justify-end"}`}>
              <div
                className={`max-w-[85%] border-2 border-black px-3 py-2 ${t.role === "ai" ? "bg-[#C62828]/10" : "bg-black text-white"}`}
              >
                <div className="mb-0.5 text-[9px] font-black uppercase opacity-70">
                  {t.role === "ai" ? "ResQ (AI)" : "You (reception)"}
                </div>
                {t.text}
              </div>
            </div>
          ))}
          {busy && !outcome && (
            <div className="text-[11px] italic text-black/50">…</div>
          )}
          {err && (
            <div className="flex items-start gap-1 border-2 border-red-600 bg-red-50 px-2 py-1 text-[11px] font-bold text-red-700">
              <XCircle size={13} className="mt-0.5 shrink-0" /> {err}
            </div>
          )}
        </div>

        {/* Outcome or input */}
        {outcome ? (
          <div className="border-t-2 border-black p-4">
            <div
              className={`mb-2 flex items-center gap-2 text-sm font-black uppercase ${confirmed ? "text-green-700" : declined ? "text-red-700" : "text-amber-700"}`}
            >
              {confirmed ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
              {BOOKING_STATE_LABEL[outcome.booking_state] || outcome.booking_state}
            </div>
            {outcome.availability !== "UNKNOWN" && (
              <Row k="Availability" v={outcome.availability === "AVAILABLE" ? "Available" : "Not available"} />
            )}
            {outcome.eta_text && <Row k="ETA (as told)" v={outcome.eta_text} />}
            {outcome.charges_text && <Row k="Charges (as told)" v={outcome.charges_text} />}
            {outcome.booking_reference && <Row k="Booking ref" v={outcome.booking_reference} />}
            {outcome.receptionist_response && (
              <p className="mt-2 text-[11px] italic text-black/60">“{outcome.receptionist_response}”</p>
            )}
            <button
              onClick={() => {
                window.speechSynthesis?.cancel();
                onClose();
              }}
              className="mt-3 w-full border-2 border-black bg-black py-2 text-xs font-black uppercase text-white"
            >
              Done
            </button>
          </div>
        ) : (
          <div className="border-t-2 border-black p-3">
            <div className="flex gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && sendReceptionist(input)}
                placeholder="Reply as the receptionist…"
                disabled={busy}
                className="flex-1 border-2 border-black bg-white px-3 py-2 text-sm"
              />
              {sttSupported && (
                <button
                  onClick={toggleMic}
                  title="Speak"
                  className={`flex items-center justify-center border-2 border-black px-3 ${listening ? "bg-[#C62828] text-white" : "bg-white text-black"}`}
                >
                  {listening ? <MicOff size={16} /> : <Mic size={16} />}
                </button>
              )}
              <button
                onClick={() => sendReceptionist(input)}
                disabled={busy}
                className="flex items-center justify-center border-2 border-black bg-black px-3 text-white"
              >
                <Send size={16} />
              </button>
            </div>
            <button
              onClick={endCall}
              disabled={busy || turns.length < 2}
              className="mt-2 w-full border-2 border-black bg-[#C62828] py-2 text-xs font-black uppercase text-white disabled:opacity-40"
            >
              End call &amp; record booking
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between border-b border-black/10 py-1 text-xs">
      <span className="font-bold uppercase text-black/60">{k}</span>
      <span className="font-black">{v}</span>
    </div>
  );
}
