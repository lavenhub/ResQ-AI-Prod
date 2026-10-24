import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Bell, Plus, Clock, Lightbulb } from "lucide-react";
import { AuthGuard } from "@/components/AuthGuard";
import { BottomNav } from "@/components/BottomNav";
import { listContacts } from "@/lib/emergencyApi";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "ResQ AI — Home" },
      { name: "description", content: "Your emergency dashboard." },
    ],
  }),
  component: () => (
    <AuthGuard>
      <Home />
    </AuthGuard>
  ),
});

function Home() {
  const navigate = useNavigate();
  const [phone, setPhone] = useState("");
  const [holding, setHolding] = useState(false);
  const [activeContacts, setActiveContacts] = useState<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setPhone(localStorage.getItem("resq_phone") || "");
    listContacts()
      .then((cs) => setActiveContacts(cs.filter((c) => c.active).length))
      .catch(() => setActiveContacts(null));
  }, []);

  const startHold = () => {
    setHolding(true);
    timerRef.current = setTimeout(() => {
      setHolding(false);
      navigate({ to: "/emergency" });
    }, 1200);
  };
  const cancelHold = () => {
    setHolding(false);
    if (timerRef.current) clearTimeout(timerRef.current);
  };

  return (
    <div className="min-h-screen bg-white">
      <header className="sticky top-0 z-20 flex items-center justify-between border-b-2 border-black bg-white px-4 py-3">
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center border-2 border-black bg-white text-xs font-bold">
            {(phone || "U").slice(-2)}
          </div>
          <span className="text-sm font-semibold text-black">
            Hello, <span className="font-bold">{phone || "User"}</span>
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-lg font-black text-[#C62828]">RESQ AI</span>
          <Bell size={20} strokeWidth={2.5} className="text-black" />
        </div>
      </header>

      <main className="mx-auto max-w-[480px] px-4 py-8">
        <div className="flex flex-col items-center">
          <div className="relative">
            {holding && (
              <>
                <span className="resq-pulse-ring absolute inset-0 rounded-full bg-[#C62828]/40" />
                <span
                  className="resq-pulse-ring absolute inset-0 rounded-full bg-[#C62828]/30"
                  style={{ animationDelay: "0.4s" }}
                />
              </>
            )}
            <button
              onMouseDown={startHold}
              onMouseUp={cancelHold}
              onMouseLeave={cancelHold}
              onTouchStart={startHold}
              onTouchEnd={cancelHold}
              className="relative h-48 w-48 select-none rounded-full border-2 border-black bg-[#C62828] text-5xl font-black text-white"
            >
              SOS
            </button>
          </div>
          <p className="mt-4 text-xs font-bold uppercase tracking-[0.25em] text-black">
            Hold to Activate
          </p>
        </div>

        <div className="mt-8 flex justify-center gap-2">
          <span className="flex items-center gap-2 border-2 border-black bg-white px-3 py-1 text-xs font-bold text-black">
            <span className="h-2 w-2 rounded-full bg-green-600" />
            GPS ACTIVE
          </span>
          <button
            onClick={() => navigate({ to: "/profile" })}
            className="flex items-center gap-2 border-2 border-black bg-white px-3 py-1 text-xs font-bold text-black"
          >
            <Plus size={12} strokeWidth={3} className="text-[#C62828]" />
            {activeContacts === null
              ? "CONTACTS —"
              : `${activeContacts} CONTACT${activeContacts === 1 ? "" : "S"} ACTIVE`}
          </button>
        </div>

        <div className="mt-8 grid grid-cols-2 gap-3">
          <button
            onClick={() => navigate({ to: "/first-aid" })}
            className="border-2 border-black bg-white p-4 text-left"
          >
            <div className="mb-3 flex h-10 w-10 items-center justify-center border-2 border-black bg-[#C62828] text-white">
              <Plus size={20} strokeWidth={3} />
            </div>
            <div className="text-sm font-black uppercase text-black">First Aid Library</div>
            <div className="mt-1 text-xs text-black/60">Step-by-step guides</div>
          </button>
          <button
            onClick={() => navigate({ to: "/history" })}
            className="border-2 border-black bg-white p-4 text-left"
          >
            <div className="mb-3 flex h-10 w-10 items-center justify-center border-2 border-black bg-white">
              <Clock size={20} strokeWidth={2.5} className="text-black" />
            </div>
            <div className="text-sm font-black uppercase text-black">Emergency History</div>
            <div className="mt-1 text-xs text-black/60">Past alerts & logs</div>
          </button>
        </div>

        <div className="mt-6 flex items-start gap-3 border-2 border-black bg-black p-4 text-white">
          <Lightbulb size={20} className="mt-0.5 shrink-0" />
          <div>
            <div className="text-sm font-black uppercase">Stay Safe Today</div>
            <p className="mt-1 text-xs text-white/80">
              Thunderstorm warning active. Keep location permissions on.
            </p>
          </div>
        </div>
      </main>

      <BottomNav />
    </div>
  );
}
