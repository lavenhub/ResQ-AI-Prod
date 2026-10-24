/**
 * VehicleSimulation
 *
 * Shows an animated vehicle (ambulance / fire brigade / police van) moving
 * toward the user's GPS location on an OpenStreetMap background.
 *
 * - Vehicle spawns ~2 km away in a random direction
 * - Moves smoothly to user pin over ~30 seconds
 * - Shows ETA countdown, vehicle type badge, pulsing destination dot
 * - Pure CSS/JS — no map SDK required
 */

import { useEffect, useRef, useState } from "react";

export type VehicleType = "ambulance" | "fire" | "police";

interface Props {
  vehicleType: VehicleType;
  vehicleLabel: string;
  userCoords: { lat: number; lng: number };
  incidentType: string;
}

const VEHICLE_CONFIG: Record<VehicleType, { emoji: string; color: string; bg: string }> = {
  ambulance: { emoji: "🚑", color: "#C62828", bg: "#FFEBEE" },
  fire:      { emoji: "🚒", color: "#E65100", bg: "#FFF3E0" },
  police:    { emoji: "🚔", color: "#1565C0", bg: "#E3F2FD" },
};

const TRAVEL_DURATION_MS = 30_000; // 30 seconds to arrive
const SPAWN_OFFSET_KM    = 2.2;    // spawn this far away

/** Convert km offset to lat/lng degrees (rough) */
function offsetCoords(
  lat: number,
  lng: number,
  bearingDeg: number,
  distanceKm: number,
): { lat: number; lng: number } {
  const R    = 6371;
  const d    = distanceKm / R;
  const brng = (bearingDeg * Math.PI) / 180;
  const lat1 = (lat * Math.PI) / 180;
  const lon1 = (lng * Math.PI) / 180;

  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(d) + Math.cos(lat1) * Math.sin(d) * Math.cos(brng),
  );
  const lon2 =
    lon1 +
    Math.atan2(
      Math.sin(brng) * Math.sin(d) * Math.cos(lat1),
      Math.cos(d) - Math.sin(lat1) * Math.sin(lat2),
    );

  return {
    lat: (lat2 * 180) / Math.PI,
    lng: (lon2 * 180) / Math.PI,
  };
}

/** Map a lat/lng to a percentage position within the visible map tile */
function coordToPercent(
  lat: number,
  lng: number,
  minLat: number,
  maxLat: number,
  minLng: number,
  maxLng: number,
): { x: number; y: number } {
  const x = ((lng - minLng) / (maxLng - minLng)) * 100;
  // lat is inverted — higher lat = lower y
  const y = ((maxLat - lat) / (maxLat - minLat)) * 100;
  return { x, y };
}

export function VehicleSimulation({ vehicleType, vehicleLabel, userCoords, incidentType }: Props) {
  const cfg        = VEHICLE_CONFIG[vehicleType];
  const startRef   = useRef<number | null>(null);
  const rafRef     = useRef<number | null>(null);
  const [pos, setPos]           = useState({ x: 0, y: 0 });
  const [arrived, setArrived]   = useState(false);
  const [eta, setEta]           = useState(Math.round(TRAVEL_DURATION_MS / 1000));

  // Spawn point — fixed random bearing per mount
  const bearingRef = useRef(Math.floor(Math.random() * 360));
  const spawnRef   = useRef(
    offsetCoords(userCoords.lat, userCoords.lng, bearingRef.current, SPAWN_OFFSET_KM),
  );

  // Viewport: pad 0.03° around user
  const PAD    = 0.028;
  const minLat = userCoords.lat - PAD;
  const maxLat = userCoords.lat + PAD;
  const minLng = userCoords.lng - PAD;
  const maxLng = userCoords.lng + PAD;

  const spawnPct = coordToPercent(
    spawnRef.current.lat, spawnRef.current.lng,
    minLat, maxLat, minLng, maxLng,
  );
  const userPct = coordToPercent(
    userCoords.lat, userCoords.lng,
    minLat, maxLat, minLng, maxLng,
  );

  useEffect(() => {
    setPos(spawnPct);

    const animate = (ts: number) => {
      if (!startRef.current) startRef.current = ts;
      const elapsed  = ts - startRef.current;
      const progress = Math.min(elapsed / TRAVEL_DURATION_MS, 1);
      // ease-in-out
      const ease = progress < 0.5
        ? 2 * progress * progress
        : -1 + (4 - 2 * progress) * progress;

      const x = spawnPct.x + (userPct.x - spawnPct.x) * ease;
      const y = spawnPct.y + (userPct.y - spawnPct.y) * ease;
      setPos({ x, y });
      setEta(Math.max(0, Math.round(((1 - progress) * TRAVEL_DURATION_MS) / 1000)));

      if (progress < 1) {
        rafRef.current = requestAnimationFrame(animate);
      } else {
        setArrived(true);
      }
    };

    rafRef.current = requestAnimationFrame(animate);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, []); // eslint-disable-line

  const mapSrc = `https://www.openstreetmap.org/export/embed.html?bbox=${minLng}%2C${minLat}%2C${maxLng}%2C${maxLat}&layer=mapnik`;

  return (
    <div className="border-2 border-black bg-white overflow-hidden">
      {/* ── Header bar ── */}
      <div
        className="flex items-center justify-between px-4 py-2"
        style={{ background: cfg.color }}
      >
        <div className="flex items-center gap-2">
          <span className="text-xl">{cfg.emoji}</span>
          <div>
            <div className="text-xs font-black uppercase text-white tracking-wide">
              {vehicleLabel} Dispatched
            </div>
            <div className="text-[10px] text-white/80">{incidentType}</div>
          </div>
        </div>
        <div
          className="border-2 border-white px-3 py-1 text-center"
          style={{ minWidth: 64 }}
        >
          {arrived ? (
            <div className="text-xs font-black uppercase text-white">Arrived!</div>
          ) : (
            <>
              <div className="text-lg font-black text-white leading-none">{eta}s</div>
              <div className="text-[9px] font-bold uppercase text-white/80">ETA</div>
            </>
          )}
        </div>
      </div>

      {/* ── Map + overlay ── */}
      <div className="relative" style={{ height: 220 }}>
        {/* OSM iframe */}
        <iframe
          title="vehicle-map"
          src={mapSrc}
          className="absolute inset-0 h-full w-full border-0"
          style={{ pointerEvents: "none" }}
        />

        {/* Overlay canvas for vehicle + destination */}
        <div className="absolute inset-0 pointer-events-none">

          {/* Pulsing destination ring at user location */}
          <div
            className="absolute"
            style={{
              left: `${userPct.x}%`,
              top:  `${userPct.y}%`,
              transform: "translate(-50%, -50%)",
            }}
          >
            {/* Outer pulse */}
            <div
              className="absolute rounded-full opacity-40 animate-ping"
              style={{
                width: 36, height: 36,
                background: cfg.color,
                top: "50%", left: "50%",
                transform: "translate(-50%, -50%)",
              }}
            />
            {/* Inner dot */}
            <div
              className="relative rounded-full border-2 border-white shadow-lg flex items-center justify-center"
              style={{
                width: 22, height: 22,
                background: cfg.color,
              }}
            >
              <span style={{ fontSize: 10 }}>📍</span>
            </div>
          </div>

          {/* Vehicle emoji — moves along interpolated path */}
          {!arrived && (
            <div
              className="absolute transition-none"
              style={{
                left: `${pos.x}%`,
                top:  `${pos.y}%`,
                transform: "translate(-50%, -50%)",
                fontSize: 26,
                filter: "drop-shadow(0 2px 4px rgba(0,0,0,0.5))",
              }}
            >
              {cfg.emoji}
            </div>
          )}

          {/* Arrived badge */}
          {arrived && (
            <div
              className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 border-2 border-white px-4 py-2 text-sm font-black uppercase text-white shadow-xl"
              style={{ background: cfg.color }}
            >
              {cfg.emoji} On Scene
            </div>
          )}
        </div>
      </div>

      {/* ── Status bar ── */}
      <div
        className="px-4 py-2 text-xs font-semibold"
        style={{ background: cfg.bg, color: cfg.color }}
      >
        {arrived
          ? `${vehicleLabel} has arrived at your location.`
          : `${vehicleLabel} is en route — ${eta} seconds away. Stay calm and follow instructions.`}
      </div>
    </div>
  );
}
