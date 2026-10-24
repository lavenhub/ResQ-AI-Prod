import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Plus } from "lucide-react";
import { apiFetch, setToken } from "@/lib/api";

export const Route = createFileRoute("/auth")({
  head: () => ({
    meta: [
      { title: "Sign in — ResQ AI" },
      { name: "description", content: "Sign in to ResQ AI emergency assistance." },
    ],
  }),
  component: AuthPage,
});

function AuthPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState<"phone" | "otp">("phone");
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState(["", "", "", "", "", ""]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputsRef = useRef<(HTMLInputElement | null)[]>([]);

  useEffect(() => {
    if (typeof window !== "undefined" && localStorage.getItem("resq_phone")) {
      navigate({ to: "/" });
    }
  }, [navigate]);

  const sendOtp = async () => {
    setError(null);
    if (!phone.trim()) {
      setError("Phone number required");
      return;
    }
    setLoading(true);
    try {
      const res = await apiFetch<{ success?: boolean; error?: string }>("/api/auth/send-otp", {
        method: "POST",
        body: JSON.stringify({ phone }),
      });
      if (res.error) setError(res.error);
      else setStep("otp");
    } catch (e: any) {
      setError(e?.message || "Failed to send OTP");
    } finally {
      setLoading(false);
    }
  };

  const verifyOtp = async () => {
    setError(null);
    const code = otp.join("");
    if (code.length !== 6) {
      setError("Enter all 6 digits");
      return;
    }
    setLoading(true);
    try {
      const res = await apiFetch<{ success?: boolean; phone?: string; token?: string; error?: string }>(
        "/api/auth/verify-otp",
        { method: "POST", body: JSON.stringify({ phone, code }) },
      );
      if (res.error) {
        setError(res.error);
        return;
      }
      // Store the normalized E.164 phone + the signed session token that
      // authorizes contacts/SOS calls from here on.
      localStorage.setItem("resq_phone", res.phone || phone);
      if (res.token) setToken(res.token);
      navigate({ to: "/" });
    } catch (e: any) {
      setError(e?.message || "Verification failed");
    } finally {
      setLoading(false);
    }
  };

  const onOtpChange = (i: number, val: string) => {
    const digit = val.replace(/\D/g, "").slice(-1);
    const next = [...otp];
    next[i] = digit;
    setOtp(next);
    if (digit && i < 5) inputsRef.current[i + 1]?.focus();
  };

  const onOtpKey = (i: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Backspace" && !otp[i] && i > 0) inputsRef.current[i - 1]?.focus();
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-white px-4 py-12">
      <div className="w-full max-w-[400px]">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center border-2 border-black bg-[#C62828] text-white">
            <Plus size={36} strokeWidth={3} />
          </div>
          <h1 className="text-3xl font-black uppercase tracking-wide text-black">RESQ AI</h1>
          <p className="mt-1 text-xs font-bold uppercase tracking-[0.2em] text-[#C62828]">
            Emergency Assistance System
          </p>
          <p className="mt-3 border-2 border-black bg-black/5 px-3 py-2 text-xs font-semibold text-black/70">
            Demo mode — OTP is always <span className="font-black text-black">123456</span>
          </p>
        </div>

        {error && (
          <div className="mb-4 border-2 border-[#C62828] bg-white p-3 text-sm font-semibold text-[#C62828]">
            {error}
          </div>
        )}

        {step === "phone" ? (
          <div className="space-y-4">
            <div>
              <label className="mb-2 block text-xs font-bold uppercase tracking-wide text-black">
                Phone Number
              </label>
              <input
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+91 98765 43210"
                className="w-full border-2 border-black bg-white px-4 py-3 text-base font-medium text-black placeholder:text-black/40"
              />
            </div>
            <button
              onClick={sendOtp}
              disabled={loading}
              className="w-full rounded border-2 border-black bg-[#C62828] px-4 py-3 text-base font-black uppercase tracking-wide text-white disabled:opacity-60"
            >
              {loading ? "Sending..." : "Send OTP →"}
            </button>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <label className="block text-xs font-bold uppercase tracking-wide text-black">
                Enter 6-Digit Code
              </label>
              <button
                onClick={() => {
                  setStep("phone");
                  setOtp(["", "", "", "", "", ""]);
                  setError(null);
                }}
                className="text-xs font-bold uppercase text-[#C62828]"
              >
                Change Number
              </button>
            </div>
            {/* Demo hint — prominent */}
            <div className="rounded border-2 border-[#C62828] bg-[#C62828]/10 px-4 py-3 text-center">
              <p className="text-xs text-black/60">Demo mode — no SMS sent.</p>
              <p className="mt-1 text-xl font-black tracking-[0.3em] text-black">1 2 3 4 5 6</p>
              <p className="mt-1 text-[10px] font-bold uppercase text-[#C62828]">Use this code</p>
            </div>
            <div className="flex justify-between gap-2">
              {otp.map((d, i) => (
                <input
                  key={i}
                  ref={(el) => {
                    inputsRef.current[i] = el;
                  }}
                  value={d}
                  onChange={(e) => onOtpChange(i, e.target.value)}
                  onKeyDown={(e) => onOtpKey(i, e)}
                  maxLength={1}
                  inputMode="numeric"
                  className="h-14 w-12 border-2 border-black bg-white text-center text-xl font-black text-black"
                />
              ))}
            </div>
            <p className="text-xs text-black/70">
              Signing in as <span className="font-bold text-black">{phone}</span>.{" "}
              <button
                onClick={() => { setStep("phone"); setOtp(["","","","","",""]); setError(null); }}
                className="font-bold uppercase text-[#C62828]"
                type="button"
              >
                Change
              </button>
            </p>
            <button
              onClick={verifyOtp}
              disabled={loading}
              className="w-full rounded border-2 border-black bg-[#C62828] px-4 py-3 text-base font-black uppercase tracking-wide text-white disabled:opacity-60"
            >
              {loading ? "Verifying..." : "Verify & Sign In →"}
            </button>
          </div>
        )}

        <p className="mt-8 text-center text-[11px] text-black/60">
          By signing in you agree to our Terms of Service and Privacy Policy
        </p>
      </div>
    </div>
  );
}
