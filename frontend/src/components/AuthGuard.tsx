import { useEffect, useState, type ReactNode } from "react";
import { useNavigate, useRouterState } from "@tanstack/react-router";

export function AuthGuard({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const phone = typeof window !== "undefined" ? localStorage.getItem("resq_phone") : null;
    if (!phone && pathname !== "/auth" && pathname !== "/landing") {
      navigate({ to: "/landing" });
    } else {
      setReady(true);
    }
  }, [navigate, pathname]);

  if (!ready) return null;
  return <>{children}</>;
}
