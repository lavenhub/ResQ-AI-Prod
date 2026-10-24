import React, { useEffect, useState } from "react";
import "./resqai-widget.css";

export type ResQAIWidgetProps = {
  /** Hosted Tough Tongue AI embed URL. Prefer passing this from app config. */
  agentUrl?: string;
  /** Optional callback for application analytics; do not log sensitive emergency details. */
  onOpen?: () => void;
  onClose?: () => void;
  /** If true, show the safety disclaimer below the embedded agent. */
  showDisclaimer?: boolean;
};

const DEFAULT_AGENT_URL =
  typeof import.meta !== "undefined" &&
  (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env
    ?.VITE_RESQAI_AGENT_URL;

export default function ResQAIWidget({
  agentUrl = DEFAULT_AGENT_URL,
  onOpen,
  onClose,
  showDisclaimer = true,
}: ResQAIWidgetProps) {
  const [open, setOpen] = useState(false);
  const [frameError, setFrameError] = useState(false);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closePanel();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // closePanel is stable enough for this local effect; no external dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function openPanel() {
    setFrameError(false);
    setOpen(true);
    onOpen?.();
  }

  function closePanel() {
    setOpen(false);
    onClose?.();
  }

  return (
    <>
      <button
        type="button"
        className="resqai-trigger"
        onClick={openPanel}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <span className="resqai-trigger__pulse" aria-hidden="true" />
        <span>Talk to ResQAI</span>
      </button>

      {open && (
        <div
          className="resqai-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closePanel();
          }}
        >
          <section
            className="resqai-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="resqai-title"
          >
            <header className="resqai-dialog__header">
              <div>
                <p className="resqai-eyebrow">EMERGENCY VOICE ASSISTANT</p>
                <h2 id="resqai-title">ResQAI</h2>
                <p className="resqai-subtitle">
                  Speak naturally in English, Hindi, or Marathi.
                </p>
              </div>
              <button
                type="button"
                className="resqai-close"
                onClick={closePanel}
                aria-label="Close ResQAI"
              >
                ×
              </button>
            </header>

            {!agentUrl ? (
              <div className="resqai-state" role="status">
                <strong>Voice agent is not configured.</strong>
                <p>
                  Set <code>VITE_RESQAI_AGENT_URL</code> to your current hosted
                  scenario embed URL, or pass <code>agentUrl</code> as a prop.
                </p>
              </div>
            ) : frameError ? (
              <div className="resqai-state" role="status">
                <strong>The voice agent could not be loaded.</strong>
                <p>
                  Check that the access link is valid, the provider allows
                  embedding, and your browser has a network connection.
                </p>
                <button type="button" onClick={() => setFrameError(false)}>
                  Retry
                </button>
              </div>
            ) : (
              <iframe
                key={agentUrl}
                title="ResQAI voice assistant"
                className="resqai-iframe"
                src={agentUrl}
                allow="microphone; autoplay; clipboard-read; clipboard-write"
                referrerPolicy="strict-origin-when-cross-origin"
                onError={() => setFrameError(true)}
              />
            )}

            {showDisclaimer && (
              <p className="resqai-disclaimer">
                Prototype: ResQAI does not automatically contact emergency
                services or notify your contacts unless those features are
                separately integrated and confirmed. In an immediate
                emergency, contact local emergency services directly.
              </p>
            )}
          </section>
        </div>
      )}
    </>
  );
}
