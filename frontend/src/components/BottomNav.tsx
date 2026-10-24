import { Link, useRouterState } from "@tanstack/react-router";
import { Home, Plus, Clock, User } from "lucide-react";
import { useBottomBarClasses } from "@/hooks/useBottomBarPos";

const TABS = [
  { to: "/",          label: "HOME",      icon: Home },
  { to: "/first-aid", label: "FIRST AID", icon: Plus },
  { to: "/history",   label: "HISTORY",   icon: Clock },
  { to: "/profile",   label: "PROFILE",   icon: User },
] as const;

export function BottomNav() {
  const pathname    = useRouterState({ select: (s) => s.location.pathname });
  const navClasses  = useBottomBarClasses("grid grid-cols-4 border-t-2 border-black bg-white");
  const isEmergency = pathname === "/emergency";
  const isAuth      = pathname === "/auth";

  if (isAuth) return null;

  return (
    <>
      {!isEmergency && (
        <Link
          to="/emergency"
          className="fixed right-4 bottom-20 z-40 flex h-14 w-14 items-center justify-center rounded-full border-2 border-black bg-[#C62828] text-xs font-bold text-white"
          style={{ fontFamily: "Inter, sans-serif" }}
        >
          SOS
        </Link>
      )}
      <nav className={navClasses}>
        {TABS.map((t) => {
          const active = pathname === t.to;
          const Icon   = t.icon;
          return (
            <Link
              key={t.to}
              to={t.to}
              className="flex flex-col items-center gap-1 py-3"
              style={{ color: active ? "#C62828" : "#111111" }}
            >
              <Icon size={20} strokeWidth={2.5} />
              <span className="text-[10px] font-bold tracking-wide">{t.label}</span>
            </Link>
          );
        })}
      </nav>
      {/* spacer so content isn't hidden behind fixed nav on mobile */}
      <div className="h-20 sm:h-0" />
    </>
  );
}
