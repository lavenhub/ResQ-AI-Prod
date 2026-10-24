import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { Shield, Mic, QrCode, Mail, MapPin, Heart } from "lucide-react";

export const Route = createFileRoute("/landing")({
  head: () => ({
    meta: [
      { title: "ResQ AI — Emergency Assistance System" },
      { name: "description", content: "AI-powered emergency voice assistant. Real-time first aid guidance, instant alerts, and medical handover pass." },
    ],
  }),
  component: LandingPage,
});

function LandingPage() {
  const navigate = useNavigate();

  // If already logged in, skip landing and go to dashboard
  useEffect(() => {
    if (typeof window !== "undefined" && localStorage.getItem("resq_phone")) {
      navigate({ to: "/" });
    }
  }, [navigate]);

  return (
    <div className="min-h-screen bg-white">

      {/* ── Hero ── */}
      <div className="relative flex flex-col items-center justify-center bg-[#C62828] px-6 pt-16 pb-12 text-white">
        {/* Logo */}
        <div className="mb-6 flex h-20 w-20 items-center justify-center border-4 border-white bg-white/10">
          <span className="text-4xl">🚨</span>
        </div>
        <h1 className="text-4xl font-black uppercase tracking-widest">RESQ AI</h1>
        <p className="mt-2 text-center text-sm font-bold uppercase tracking-[0.2em] text-white/80">
          Emergency Assistance System
        </p>
        <p className="mt-5 text-center text-base font-medium leading-relaxed text-white/90 max-w-[320px]">
          AI-powered voice dispatcher that guides you through any emergency — in real time, in your language.
        </p>

        {/* CTA Buttons */}
        <div className="mt-8 w-full max-w-[320px] space-y-3">
          <button
            onClick={() => navigate({ to: "/auth" })}
            className="flex w-full items-center justify-center gap-2 border-2 border-white bg-white px-6 py-4 text-sm font-black uppercase tracking-wide text-[#C62828]"
          >
            Get Started — It's Free
          </button>
          <button
            onClick={() => navigate({ to: "/auth" })}
            className="flex w-full items-center justify-center gap-2 border-2 border-white/60 bg-transparent px-6 py-3 text-sm font-bold uppercase tracking-wide text-white"
          >
            Sign In
          </button>
        </div>

        {/* Demo tag */}
        <p className="mt-4 text-[11px] text-white/60">
          Demo mode — OTP is always <span className="font-black text-white">123456</span>
        </p>
      </div>

      {/* ── Features ── */}
      <div className="px-6 py-10">
        <h2 className="mb-6 text-center text-xs font-black uppercase tracking-[0.2em] text-black/40">
          Everything you need in an emergency
        </h2>
        <div className="space-y-4">
          <Feature
            icon={<Mic size={20} />}
            title="Voice AI Dispatcher"
            desc="Push to talk — describe your emergency. The AI gives step-by-step first aid in 22 Indian languages."
          />
          <Feature
            icon={<Mail size={20} />}
            title="Instant Contact Alerts"
            desc="Emergency contacts are notified by email the moment a crisis is detected — with your location."
          />
          <Feature
            icon={<MapPin size={20} />}
            title="Live Vehicle Dispatch"
            desc="Watch an ambulance, fire brigade, or police van dispatched and moving toward your location on the map."
          />
          <Feature
            icon={<QrCode size={20} />}
            title="Medical Handover QR"
            desc="Scan the QR code to open a full PDF medical report — your profile and incident details for first responders."
          />
          <Feature
            icon={<Shield size={20} />}
            title="Medical ID Pass"
            desc="Blood group, allergies, and conditions always available. No internet needed to view your profile."
          />
          <Feature
            icon={<Heart size={20} />}
            title="Emergency History"
            desc="Every session saved with transcript, QR code, and GPS link. Review past incidents anytime."
          />
        </div>
      </div>

      {/* ── How it works ── */}
      <div className="border-t-2 border-black bg-black px-6 py-10">
        <h2 className="mb-6 text-center text-xs font-black uppercase tracking-[0.2em] text-white/60">
          How it works
        </h2>
        <div className="space-y-5">
          {[
            { n: "1", title: "Sign up", desc: "Enter your phone number — OTP is instant." },
            { n: "2", title: "Set your profile", desc: "Add blood group, allergies, and emergency contacts." },
            { n: "3", title: "SOS", desc: "Tap the red SOS button when you need help." },
            { n: "4", title: "Talk", desc: "Hold to talk. Describe what happened. The AI guides you." },
            { n: "5", title: "Contacts notified", desc: "Your contacts get an email with your location automatically." },
            { n: "6", title: "QR for responders", desc: "End the session — share the QR with first responders." },
          ].map(({ n, title, desc }) => (
            <div key={n} className="flex items-start gap-4">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center border-2 border-[#C62828] bg-[#C62828] text-sm font-black text-white">
                {n}
              </div>
              <div>
                <div className="text-sm font-black uppercase text-white">{title}</div>
                <div className="text-xs text-white/60">{desc}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Final CTA ── */}
      <div className="border-t-2 border-black px-6 py-10 text-center">
        <p className="text-sm font-bold text-black/60">Because in an emergency,</p>
        <p className="text-xl font-black uppercase text-black">every second counts.</p>
        <button
          onClick={() => navigate({ to: "/auth" })}
          className="mt-6 w-full border-2 border-black bg-[#C62828] px-6 py-4 text-sm font-black uppercase tracking-wide text-white"
        >
          🚨 Open ResQ AI
        </button>
        <p className="mt-3 text-[10px] text-black/40">
          Free to use · No credit card · Demo OTP: 123456
        </p>
      </div>

    </div>
  );
}

function Feature({ icon, title, desc }: { icon: React.ReactNode; title: string; desc: string }) {
  return (
    <div className="flex items-start gap-4 border-2 border-black p-4">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center border-2 border-black bg-[#C62828] text-white">
        {icon}
      </div>
      <div>
        <div className="text-sm font-black uppercase text-black">{title}</div>
        <div className="mt-0.5 text-xs text-black/60 leading-relaxed">{desc}</div>
      </div>
    </div>
  );
}
