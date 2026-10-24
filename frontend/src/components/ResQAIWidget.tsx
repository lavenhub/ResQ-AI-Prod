import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ExternalLink, Mic, RefreshCw, X } from "lucide-react";
import { apiUrl } from "@/lib/api";

/**
 * ResQAIWidget — embeds the hosted Tough Tongue AI emergency voice scenario.
 *
 * This is a self-contained, client-side alternative to the project's own
 * WebRTC dispatcher (see `useVoiceAgent`): it runs entirely in an iframe served
 * by Tough Tongue AI, so it works for a demo even when the Python voice backend
 * is not running. It does NOT replace the existing SOS flow — it sits alongside
 * it on the Emergency screen.
 *
 * The embed URL is resolved from configuration (most explicit wins):
 *   1. `agentUrl` prop
 *   2. `VITE_RESQAI_AGENT_URL` (full embed URL — use for private/token scenarios)
 *   3. `VITE_RESQAI_SCENARIO_ID` (scenario id → built into the documented URL)
 *   4. backend `GET /api/resqai/config` (runtime server config, if available)
 *   5. the public demo scenario id baked in below
 *
 * Security: an embed URL that carries a private scenario access token is a
 * bearer credential — keep it in `.env.local` (gitignored), never commit it,
 * and treat it as short-lived. The public scenario id used as the default below
 * is not a secret (it ships in the starter's README).
 */

/** Public ResQAI Emergency Voice Assistant scenario (from the starter README). */
const DEFAULT_SCENARIO_ID = "6ad83140767d01fd14f81fa4";

/** Documented Tough Tongue AI hosted embed URL format. */
function buildEmbedUrl(scenarioId: string): string {
  return `https://app.toughtongueai.com/embed/${scenarioId}?bg=black&skipPrecheck=true`;
}

export type ResQAIWidgetProps = {
  /** Full hosted embed URL. Overrides every other source when set. */
  agentUrl?: string;
  /**
   * Scenario id used to build the embed URL when no full `agentUrl` is given.
   * Defaults to the public demo scenario. Pass `""` to disable the baked
   * default and force the "not configured" state when nothing else is set.
   */
  scenarioId?: string;
  /** Optional analytics hooks. Do NOT log transcripts or sensitive details. */
  onOpen?: () => void;
  onClose?: () => void;
  /** Show the safety disclaimer under the embed (default true). */
  showDisclaimer?: boolean;
  /** Extra classes for the trigger button, to blend with the host layout. */
  triggerClassName?: string;
};

export default function ResQAIWidget({
  agentUrl,
  scenarioId = DEFAULT_SCENARIO_ID,
  onOpen,
  onClose,
  showDisclaimer = true,
  triggerClassName,
}: ResQAIWidgetProps) {
  // Resolve a synchronous URL from the explicit, build-time sources first.
  const envUrl = import.meta.env.VITE_RESQAI_AGENT_URL?.trim();
  const envScenario = import.meta.env.VITE_RESQAI_SCENARIO_ID?.trim();
  const explicitUrl = agentUrl?.trim() || envUrl || (envScenario ? buildEmbedUrl(envScenario) : "");
  const initialUrl = explicitUrl || (scenarioId ? buildEmbedUrl(scenarioId) : "");

  const [resolvedUrl, setResolvedUrl] = useState(initialUrl);
  const [open, setOpen] = useState(false);
  const [frameError, setFrameError] = useState(false);
  const [frameLoaded, setFrameLoaded] = useState(false);

  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);

  // When no explicit build-time URL is set, let the backend (if running) supply
  // one at runtime. This never overrides an explicit env/prop configuration.
  useEffect(() => {
    if (explicitUrl) return;
    let cancelled = false;
    fetch(apiUrl("/api/resqai/config"))
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!cancelled && d?.configured && typeof d.agentUrl === "string" && d.agentUrl.trim()) {
          setResolvedUrl(d.agentUrl.trim());
        }
      })
      .catch(() => {
        /* backend optional — fall back to the baked default already in state */
      });
    return () => {
      cancelled = true;
    };
  }, [explicitUrl]);

  const closePanel = useCallback(() => {
    setOpen(false);
    onClose?.();
    // Return focus to the trigger for keyboard/screen-reader users.
    triggerRef.current?.focus();
  }, [onClose]);

  function openPanel() {
    setFrameError(false);
    setFrameLoaded(false);
    setOpen(true);
    onOpen?.();
  }

  // Escape to close + lock background scroll while the dialog is open.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closePanel();
    };
    window.addEventListener("keydown", onKeyDown);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // Move focus into the dialog once it mounts.
    const t = window.setTimeout(() => closeRef.current?.focus(), 0);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = prevOverflow;
      window.clearTimeout(t);
    };
  }, [open, closePanel]);

  const modal =
    open &&
    createPortal(
      <div
        // Inline position:fixed (not the Tailwind `.fixed` class) so the global
        // phone-frame override in styles.css does not turn this into `sticky`.
        style={{ position: "fixed", inset: 0, zIndex: 9999 }}
        className="flex items-center justify-center bg-black/80 p-0 sm:p-4"
        role="presentation"
        onMouseDown={(e) => {
          if (e.target === e.currentTarget) closePanel();
        }}
      >
        <section
          role="dialog"
          aria-modal="true"
          aria-labelledby="resqai-widget-title"
          className="flex h-full w-full max-w-[440px] flex-col border-black bg-white sm:h-[min(90dvh,760px)] sm:border-2"
        >
          <header className="flex items-start justify-between gap-3 border-b-2 border-black px-4 py-3">
            <div>
              <p className="text-[10px] font-black uppercase tracking-[0.12em] text-[#C62828]">
                Emergency Voice Assistant
              </p>
              <h2 id="resqai-widget-title" className="text-xl font-black uppercase text-black">
                ResQ AI
              </h2>
              <p className="mt-0.5 text-xs text-black/60">
                Speak naturally in English, Hindi, or Marathi.
              </p>
            </div>
            <button
              ref={closeRef}
              type="button"
              onClick={closePanel}
              aria-label="Close ResQ AI voice assistant"
              className="flex h-9 w-9 shrink-0 items-center justify-center border-2 border-black bg-white text-black"
            >
              <X size={18} strokeWidth={2.5} />
            </button>
          </header>

          <div className="relative flex-1 overflow-hidden bg-white">
            {!resolvedUrl ? (
              <div
                role="status"
                className="flex h-full flex-col items-start justify-center gap-2 p-5"
              >
                <strong className="text-sm font-black uppercase text-black">
                  Voice agent is not configured.
                </strong>
                <p className="text-xs leading-relaxed text-black/60">
                  Set <code className="font-bold">VITE_RESQAI_AGENT_URL</code> (or{" "}
                  <code className="font-bold">VITE_RESQAI_SCENARIO_ID</code>) in your frontend{" "}
                  <code className="font-bold">.env.local</code> to the current Tough Tongue AI
                  hosted scenario, then restart the dev server.
                </p>
              </div>
            ) : frameError ? (
              <div
                role="status"
                className="flex h-full flex-col items-start justify-center gap-3 p-5"
              >
                <strong className="text-sm font-black uppercase text-black">
                  The voice agent could not load.
                </strong>
                <p className="text-xs leading-relaxed text-black/60">
                  Check that the embed link is current and valid, that the provider allows
                  embedding, and that you are online.
                </p>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setFrameError(false);
                      setFrameLoaded(false);
                    }}
                    className="inline-flex items-center gap-1.5 border-2 border-black bg-black px-3 py-2 text-xs font-black uppercase text-white"
                  >
                    <RefreshCw size={13} strokeWidth={2.5} /> Retry
                  </button>
                  <a
                    href={resolvedUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1.5 border-2 border-black bg-white px-3 py-2 text-xs font-black uppercase text-black"
                  >
                    <ExternalLink size={13} strokeWidth={2.5} /> Open in new tab
                  </a>
                </div>
              </div>
            ) : (
              <>
                {!frameLoaded && (
                  <div className="absolute inset-0 z-10 flex items-center justify-center bg-white">
                    <span className="text-xs font-bold uppercase tracking-wider text-black/50">
                      Connecting to ResQ AI…
                    </span>
                  </div>
                )}
                <iframe
                  key={resolvedUrl}
                  title="ResQ AI voice assistant"
                  src={resolvedUrl}
                  allow="microphone; autoplay; clipboard-write"
                  referrerPolicy="strict-origin-when-cross-origin"
                  className="h-full w-full border-0 bg-white"
                  onLoad={() => setFrameLoaded(true)}
                  onError={() => setFrameError(true)}
                />
              </>
            )}
          </div>

          {showDisclaimer && (
            <p className="border-t-2 border-black px-4 py-2 text-[10px] leading-relaxed text-black/60">
              Prototype: ResQ AI does not automatically contact emergency services or notify your
              contacts unless those features are separately integrated and confirmed. In an
              immediate emergency, contact local emergency services directly.
            </p>
          )}
        </section>
      </div>,
      document.body,
    );

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={openPanel}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={
          triggerClassName ??
          "flex w-full items-center justify-center gap-2 border-2 border-black bg-[#C62828] px-4 py-3 text-sm font-black uppercase tracking-wide text-white"
        }
      >
        <Mic size={18} strokeWidth={2.5} />
        Talk to ResQ AI
      </button>
      {modal}
    </>
  );
}
