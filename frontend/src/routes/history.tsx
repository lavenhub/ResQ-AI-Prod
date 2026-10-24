import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { ChevronDown, QrCode, Trash2 } from "lucide-react";
import { AuthGuard } from "@/components/AuthGuard";
import { BottomNav } from "@/components/BottomNav";
import type { HistoryEntry } from "@/hooks/useVoiceAgent";

export const Route = createFileRoute("/history")({
  head: () => ({
    meta: [
      { title: "History — ResQ AI" },
      { name: "description", content: "Review past emergency incidents." },
    ],
  }),
  component: () => (
    <AuthGuard>
      <HistoryPage />
    </AuthGuard>
  ),
});

function loadHistory(): HistoryEntry[] {
  try {
    const v = localStorage.getItem("resq_history");
    return v ? JSON.parse(v) : [];
  } catch { return []; }
}

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString("en-IN", {
      day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

function HistoryPage() {
  const [incidents, setIncidents] = useState<HistoryEntry[]>(loadHistory);
  const [open,    setOpen]   = useState<string | null>(null);
  const [showQr,  setShowQr] = useState<string | null>(null); // entry id

  const deleteEntry = (id: string) => {
    const next = incidents.filter((e) => e.id !== id);
    setIncidents(next);
    localStorage.setItem("resq_history", JSON.stringify(next));
    if (open === id)   setOpen(null);
    if (showQr === id) setShowQr(null);
  };

  const downloadQr = (entry: HistoryEntry) => {
    const a = document.createElement("a");
    a.href     = entry.qrDataUrl;
    a.download = `resq-pass-${entry.id}.png`;
    a.click();
  };

  return (
    <div className="min-h-screen bg-white">
      <header className="sticky top-0 z-20 flex items-center justify-between border-b-2 border-black bg-white px-4 py-3">
        <span className="text-base font-black uppercase text-black">Emergency History</span>
        <span className="text-lg font-black text-[#C62828]">RESQ AI</span>
      </header>

      <main className="mx-auto max-w-[480px] space-y-4 px-4 py-4">
        <div>
          <h1 className="text-xl font-black uppercase text-black">Emergency History</h1>
          <p className="mt-1 text-xs text-black/60">Past incidents with Medical Handover QR passes.</p>
        </div>

        {incidents.length === 0 && (
          <div className="border-2 border-dashed border-black bg-white p-8 text-center">
            <QrCode size={32} className="mx-auto mb-3 text-black/30" />
            <p className="text-sm font-bold uppercase text-black/50">No incidents yet</p>
            <p className="mt-1 text-xs text-black/40">
              After you end an emergency session, a Medical Handover Pass will appear here.
            </p>
          </div>
        )}

        <div className="space-y-3">
          {incidents.map((inc) => {
            const isOpen  = open === inc.id;
            const isQrOpen = showQr === inc.id;

            return (
              <div key={inc.id} className="border-2 border-black bg-white">
                {/* ── Accordion header ── */}
                <button
                  onClick={() => setOpen(isOpen ? null : inc.id)}
                  className="flex w-full items-center justify-between p-4 text-left"
                >
                  <div>
                    <div className="text-xs font-bold uppercase text-black/60">{formatDate(inc.date)}</div>
                    <div className="text-sm font-black uppercase text-black">{inc.incidentType}</div>
                    <div className="text-[11px] text-black/60">Duration: {inc.duration}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="border-2 border-black bg-white px-2 py-1 text-[10px] font-black uppercase text-black">
                      Resolved
                    </span>
                    <ChevronDown size={18} className={`transition-transform ${isOpen ? "rotate-180" : ""}`} />
                  </div>
                </button>

                {/* ── Expanded content ── */}
                {isOpen && (
                  <div className="space-y-4 border-t-2 border-black p-4">

                    {/* Medical Handover Pass QR */}
                    <div>
                      <div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase text-[#C62828]">
                        <QrCode size={13} /> Medical Handover Pass
                      </div>
                      <p className="mb-3 text-[11px] text-black/60">
                        Show this QR to first responders. Scan to view full medical profile + incident details.
                      </p>

                      {isQrOpen ? (
                        <div className="flex flex-col items-center gap-3">
                          <img
                            src={inc.qrDataUrl}
                            alt="Medical Handover QR"
                            className="h-56 w-56 border-2 border-black"
                          />
                          <div className="flex gap-2 w-full">
                            <button
                              onClick={() => downloadQr(inc)}
                              className="flex-1 border-2 border-black bg-black px-3 py-2 text-xs font-black uppercase text-white"
                            >
                              Download QR
                            </button>
                            <button
                              onClick={() => setShowQr(null)}
                              className="flex-1 border-2 border-black bg-white px-3 py-2 text-xs font-black uppercase text-black"
                            >
                              Hide QR
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button
                          onClick={() => setShowQr(inc.id)}
                          className="flex w-full items-center justify-center gap-2 border-2 border-black bg-[#C62828] px-4 py-3 text-xs font-black uppercase text-white"
                        >
                          <QrCode size={15} /> Show QR Pass
                        </button>
                      )}
                    </div>

                    {/* Transcript summary */}
                    {inc.transcript.filter((t) => t.sender !== "system").length > 0 && (
                      <div>
                        <div className="mb-2 text-[10px] font-black uppercase text-black/60">
                          Conversation Summary
                        </div>
                        <div className="max-h-40 space-y-1 overflow-y-auto text-xs">
                          {inc.transcript
                            .filter((t) => t.sender !== "system")
                            .map((t) => (
                              <div key={t.id} className="leading-snug">
                                <span className={`mr-1 font-bold uppercase ${t.sender === "resq" ? "text-[#C62828]" : "text-black"}`}>
                                  {t.sender === "resq" ? "RESQ:" : "YOU:"}
                                </span>
                                <span className="text-black/80">{t.text}</span>
                              </div>
                            ))}
                        </div>
                      </div>
                    )}

                    {/* Location */}
                    {inc.location && (
                      <div className="text-xs">
                        <span className="font-bold uppercase text-black/60">Location: </span>
                        <a
                          href={`https://maps.google.com/?q=${inc.location.lat},${inc.location.lng}`}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[#C62828] underline"
                        >
                          {inc.location.lat.toFixed(5)}, {inc.location.lng.toFixed(5)}
                        </a>
                      </div>
                    )}

                    {/* Delete */}
                    <button
                      onClick={() => deleteEntry(inc.id)}
                      className="flex items-center gap-2 border-2 border-black bg-white px-3 py-2 text-xs font-black uppercase text-[#C62828]"
                    >
                      <Trash2 size={13} /> Delete Record
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {incidents.length > 0 && (
          <p className="pt-4 text-center text-xs italic text-black/50">
            {incidents.length} incident{incidents.length !== 1 ? "s" : ""} on record.
          </p>
        )}
      </main>

      <BottomNav />
    </div>
  );
}
