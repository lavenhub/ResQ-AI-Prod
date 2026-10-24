import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import {
  Bug,
  ChevronRight,
  Flame,
  Heart,
  Search,
  Shield,
  User,
  Wind,
  type LucideIcon,
} from "lucide-react";
import { AuthGuard } from "@/components/AuthGuard";
import { BottomNav } from "@/components/BottomNav";
import { GUIDES, type FirstAidGuide } from "@/lib/types";

const ICONS: Record<FirstAidGuide["iconName"], LucideIcon> = {
  shield: Shield,
  bug: Bug,
  heart: Heart,
  wind: Wind,
  flame: Flame,
  user: User,
};

export const Route = createFileRoute("/first-aid")({
  head: () => ({
    meta: [
      { title: "First Aid Library — ResQ AI" },
      { name: "description", content: "Step-by-step first aid guides for emergencies." },
    ],
  }),
  component: () => (
    <AuthGuard>
      <FirstAidPage />
    </AuthGuard>
  ),
});

function FirstAidPage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const q = query.toLowerCase().trim();
  const list = q
    ? GUIDES.filter(
        (g) =>
          g.title.toLowerCase().includes(q) ||
          g.steps.some((s) => s.toLowerCase().includes(q)) ||
          g.precautions.some((s) => s.toLowerCase().includes(q)),
      )
    : GUIDES;

  return (
    <div className="min-h-screen bg-white">
      <header className="sticky top-0 z-20 flex items-center justify-between border-b-2 border-black bg-white px-4 py-3">
        <span className="text-base font-black uppercase text-black">First Aid Library</span>
        <span className="text-lg font-black text-[#C62828]">RESQ AI</span>
      </header>

      <main className="mx-auto max-w-[480px] space-y-4 px-4 py-4">
        <div className="flex items-center gap-2 border-2 border-black bg-white px-3 py-2">
          <Search size={18} strokeWidth={2.5} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="SEARCH EMERGENCIES..."
            className="flex-1 bg-white text-sm font-semibold uppercase placeholder:text-black/40"
            style={{ outline: "none", boxShadow: "none" }}
          />
        </div>

        <button
          onClick={() => setExpanded(expanded === "cpr" ? null : "cpr")}
          className="block w-full border-2 border-black bg-[#C62828] p-4 text-left text-white"
        >
          <span className="inline-block border-2 border-white px-2 py-0.5 text-[10px] font-black uppercase">
            Urgent Guide
          </span>
          <div className="mt-2 text-2xl font-black uppercase">CPR Protocol</div>
          <p className="mt-1 text-xs">Real-time audio and visual pacing for chest compressions.</p>
        </button>

        <div className="border-2 border-black bg-white">
          {list.map((g, i) => {
            const Icon = ICONS[g.iconName];
            const open = expanded === g.id;
            return (
              <div key={g.id} className={i > 0 ? "border-t-2 border-black" : ""}>
                <button
                  onClick={() => setExpanded(open ? null : g.id)}
                  className="flex w-full items-center gap-3 p-3 text-left"
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center border-2 border-black bg-[#C62828] text-white">
                    <Icon size={20} strokeWidth={2.5} />
                  </div>
                  <div className="flex-1">
                    <div className="text-sm font-black uppercase text-black">{g.title}</div>
                    <div className="text-[10px] font-bold uppercase text-black/60">
                      Tap for step-by-step guide
                    </div>
                  </div>
                  <ChevronRight
                    size={20}
                    className={`shrink-0 transition-transform ${open ? "rotate-90" : ""}`}
                  />
                </button>
                {open && (
                  <div className="space-y-4 border-t-2 border-black bg-white p-4">
                    <div>
                      <div className="mb-1 text-[11px] font-black uppercase text-[#C62828]">
                        Precautions
                      </div>
                      <ul className="list-disc space-y-1 pl-5 text-sm text-black">
                        {g.precautions.map((p, k) => (
                          <li key={k}>{p}</li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <div className="mb-1 text-[11px] font-black uppercase text-black">Steps</div>
                      <ol className="space-y-2 text-sm text-black">
                        {g.steps.map((s, k) => (
                          <li key={k} className="flex gap-2">
                            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 border-[#C62828] text-xs font-black text-[#C62828]">
                              {k + 1}
                            </span>
                            <span>{s}</span>
                          </li>
                        ))}
                      </ol>
                    </div>
                    <div className="border-2 border-black bg-[#C62828]/10 p-3">
                      <div className="mb-1 text-[11px] font-black uppercase text-[#C62828]">
                        Do Not
                      </div>
                      <ul className="space-y-1 text-sm">
                        {g.warnings.map((w, k) => (
                          <li key={k} className="text-black">
                            <span className="mr-1 font-black text-[#C62828]">✕</span>
                            {w}
                          </li>
                        ))}
                      </ul>
                    </div>
                    <button
                      onClick={() => navigate({ to: "/emergency" })}
                      className="w-full rounded border-2 border-black bg-[#C62828] px-3 py-2 text-xs font-black uppercase text-white"
                    >
                      Ask ResQ AI to Guide Me Through This
                    </button>
                  </div>
                )}
              </div>
            );
          })}
          {list.length === 0 && (
            <div className="p-6 text-center text-xs font-bold uppercase text-black/60">
              No matches
            </div>
          )}
        </div>

        <button
          onClick={() => navigate({ to: "/emergency" })}
          className="flex w-full items-center justify-between border-2 border-black bg-black p-4 text-left text-white"
        >
          <div>
            <div className="text-xs font-black uppercase">AI Diagnose</div>
            <p className="text-[11px] text-white/70">
              Can't identify the issue? Describe symptoms to our AI.
            </p>
          </div>
          <span className="text-xs font-black uppercase text-[#C62828]">Start →</span>
        </button>
      </main>

      <BottomNav />
    </div>
  );
}
