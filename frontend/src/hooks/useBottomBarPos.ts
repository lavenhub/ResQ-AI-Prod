/**
 * Returns the correct CSS position class for bottom bars.
 * - On real mobile (screen ≤ 639px): "fixed" — sticks to the viewport bottom.
 * - On desktop inside the phone frame: "sticky" — sticks to the bottom of
 *   the scrollable .phone-screen container so it stays inside the frame.
 */
import { useEffect, useState } from "react";

export function useBottomBarPos(): "fixed" | "sticky" {
  const [pos, setPos] = useState<"fixed" | "sticky">(
    typeof window !== "undefined" && window.innerWidth >= 640 ? "sticky" : "fixed",
  );

  useEffect(() => {
    const mq = window.matchMedia("(min-width: 640px)");
    const handler = (e: MediaQueryListEvent) => setPos(e.matches ? "sticky" : "fixed");
    mq.addEventListener("change", handler);
    setPos(mq.matches ? "sticky" : "fixed");
    return () => mq.removeEventListener("change", handler);
  }, []);

  return pos;
}

/** Returns positioning classes for bottom bars.
 *  sticky = inside phone frame on desktop (no left/right needed, fills container)
 *  fixed  = real mobile (needs left-0 right-0 to span viewport)
 */
export function useBottomBarClasses(extra = ""): string {
  const pos = useBottomBarPos();
  if (pos === "sticky") {
    return `sticky bottom-0 z-30 w-full ${extra}`;
  }
  return `fixed bottom-0 left-0 right-0 z-30 ${extra}`;
}
