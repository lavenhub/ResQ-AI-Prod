/**
 * /medical-pass?data=<base64-encoded-JSON>
 *
 * This page is what gets opened when a first responder scans the QR code.
 * It renders a clean, printable PDF-style medical handover report.
 * All data is encoded in the URL — no server needed.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { AlertCircle, Download, Printer } from "lucide-react";

export const Route = createFileRoute("/medical-pass")({
  head: () => ({
    meta: [
      { title: "Medical Handover Pass — ResQ AI" },
      { name: "description", content: "Emergency medical handover pass for first responders." },
    ],
  }),
  component: MedicalPassPage,
});

interface PassData {
  name: string;
  phone: string;
  blood: string;
  age: string | number;
  allergies: string;
  conditions: string;
  incident: string;
  date: string;
  location: string;
  address?: string;
  summary: string;
}

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleString("en-IN", {
      day: "numeric", month: "long", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

function urgencyColor(incident: string): string {
  const i = incident.toLowerCase();
  if (/heart|cardiac|stroke|not breath|cpr|unconscious|choking/.test(i)) return "#C62828";
  if (/fire|burn|electric|accident|crash|road|fall/.test(i)) return "#E65100";
  if (/bleed|fracture|injur|wound/.test(i)) return "#B71C1C";
  return "#1565C0";
}

function MedicalPassPage() {
  const [data, setData]   = useState<PassData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const printRef          = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const raw    = params.get("data");
    if (!raw) { setError("No data found in QR code."); return; }
    try {
      const decoded = JSON.parse(atob(raw));
      setData(decoded as PassData);
    } catch {
      try {
        const decoded = JSON.parse(decodeURIComponent(raw));
        setData(decoded as PassData);
      } catch {
        setError("Could not decode QR data.");
      }
    }
  }, []);

  const handlePrint = () => window.print();

  const handleDownloadPdf = () => {
    window.print(); // browser's "Save as PDF" option
  };

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white p-8 text-center">
        <div>
          <AlertCircle size={48} className="mx-auto mb-4 text-[#C62828]" />
          <h1 className="text-xl font-black uppercase text-black">Invalid QR Code</h1>
          <p className="mt-2 text-sm text-black/60">{error}</p>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-[#C62828] border-t-transparent" />
      </div>
    );
  }

  const accentColor = urgencyColor(data.incident);
  const mapsUrl     = data.location && data.location !== "Unknown"
    ? `https://maps.google.com/?q=${data.location}`
    : null;

  return (
    <>
      {/* Print styles */}
      <style>{`
        @media print {
          .no-print { display: none !important; }
          body { margin: 0; }
          .print-page { box-shadow: none !important; margin: 0 !important; max-width: 100% !important; }
        }
      `}</style>

      {/* Action bar — hidden when printing */}
      <div className="no-print sticky top-0 z-20 flex items-center justify-between border-b-2 border-black bg-white px-4 py-3">
        <div className="flex items-center gap-2">
          <AlertCircle size={20} style={{ color: accentColor }} />
          <span className="text-sm font-black uppercase text-black">Medical Handover Pass</span>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handlePrint}
            className="flex items-center gap-1.5 border-2 border-black bg-white px-3 py-1.5 text-xs font-black uppercase"
          >
            <Printer size={13} /> Print
          </button>
          <button
            onClick={handleDownloadPdf}
            className="flex items-center gap-1.5 border-2 border-black bg-black px-3 py-1.5 text-xs font-black uppercase text-white"
          >
            <Download size={13} /> Save PDF
          </button>
        </div>
      </div>

      {/* ── PDF Page ── */}
      <div className="min-h-screen bg-gray-100 py-6 px-4 print:bg-white print:p-0">
        <div
          ref={printRef}
          className="print-page mx-auto max-w-[680px] bg-white shadow-xl"
          style={{ fontFamily: "Arial, sans-serif" }}
        >
          {/* Header */}
          <div style={{ background: accentColor, padding: "20px 28px" }}>
            <div className="flex items-start justify-between">
              <div>
                <div style={{ color: "white", fontSize: 22, fontWeight: 900, letterSpacing: 1, textTransform: "uppercase" }}>
                  🚨 EMERGENCY MEDICAL PASS
                </div>
                <div style={{ color: "rgba(255,255,255,0.85)", fontSize: 12, marginTop: 4 }}>
                  ResQ AI — First Responder Handover Document
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ background: "rgba(255,255,255,0.2)", color: "white", padding: "6px 14px", fontSize: 13, fontWeight: 900, textTransform: "uppercase", borderRadius: 2 }}>
                  {data.incident}
                </div>
                <div style={{ color: "rgba(255,255,255,0.75)", fontSize: 10, marginTop: 4 }}>
                  {formatDate(data.date)}
                </div>
              </div>
            </div>
          </div>

          {/* Body */}
          <div style={{ padding: "24px 28px" }}>

            {/* Patient info */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 20 }}>
              {/* Left */}
              <div style={{ border: "2px solid #111", padding: 16 }}>
                <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "#666", marginBottom: 8 }}>Patient Details</div>
                <Row label="Full Name"  value={data.name  || "—"} large />
                <Row label="Phone"      value={data.phone || "—"} />
                <Row label="Age"        value={data.age   ? String(data.age) : "—"} />
              </div>
              {/* Right — vital stats */}
              <div style={{ border: "2px solid #111", padding: 16 }}>
                <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "#666", marginBottom: 8 }}>Medical Profile</div>
                <Row label="Blood Group"  value={data.blood      || "—"} large accent={accentColor} />
                <Row label="Allergies"    value={data.allergies  || "None known"} />
                <Row label="Conditions"   value={data.conditions || "None known"} />
              </div>
            </div>

            {/* Incident summary */}
            <div style={{ border: "2px solid #111", padding: 16, marginBottom: 20 }}>
              <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "#666", marginBottom: 8 }}>
                Incident Summary
              </div>
              <div style={{ fontSize: 14, color: "#111", lineHeight: 1.6 }}>
                {data.summary || "No summary recorded."}
              </div>
            </div>

            {/* Location */}
            <div style={{ border: "2px solid #111", padding: 16, marginBottom: 20 }}>
              <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "#666", marginBottom: 8 }}>
                Location
              </div>
              {data.address && (
                <div style={{ fontSize: 14, fontWeight: 700, color: "#111", marginBottom: 4 }}>
                  {data.address}
                </div>
              )}
              <div style={{ fontSize: 12, color: "#666" }}>
                {data.location || "Unknown"}
              </div>
              {mapsUrl && (
                <a
                  href={mapsUrl}
                  target="_blank"
                  rel="noreferrer"
                  style={{ display: "inline-block", marginTop: 8, background: accentColor, color: "white", padding: "6px 14px", fontSize: 12, fontWeight: 700, textDecoration: "none", textTransform: "uppercase" }}
                >
                  📍 Open in Google Maps
                </a>
              )}
            </div>

            {/* Instructions for responders */}
            <div style={{ background: "#FFF8E1", border: "2px solid #F9A825", padding: 16, marginBottom: 20 }}>
              <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "#F57F17", marginBottom: 8 }}>
                ⚠️ For First Responders
              </div>
              <div style={{ fontSize: 12, color: "#333", lineHeight: 1.7 }}>
                This pass was generated automatically by ResQ AI during an active emergency session.
                The information above reflects the patient's stored medical profile and the AI dispatcher's
                conversation summary. Verify details with the patient when possible.
                Generated: {formatDate(data.date)}.
              </div>
            </div>

            {/* Footer */}
            <div style={{ borderTop: "2px solid #eee", paddingTop: 16, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ fontSize: 10, color: "#999" }}>
                Generated by ResQ AI Emergency Assistance System
              </div>
              <div style={{ fontSize: 11, fontWeight: 900, color: accentColor, textTransform: "uppercase" }}>
                RESQ AI
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

function Row({ label, value, large, accent }: { label: string; value: string; large?: boolean; accent?: string }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 9, fontWeight: 700, textTransform: "uppercase", color: "#888", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: large ? 16 : 13, fontWeight: large ? 900 : 600, color: accent || "#111" }}>{value}</div>
    </div>
  );
}
