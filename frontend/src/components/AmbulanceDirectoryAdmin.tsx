import { useEffect, useState } from "react";
import { BadgeCheck, Plus, Trash2 } from "lucide-react";
import {
  type AmbulanceServiceItem,
  createService,
  deleteService,
  listServices,
  updateService,
  verifyService,
} from "@/lib/ambulanceApi";

/**
 * Compact admin for the ambulance/hospital directory (the dial targets).
 * Add / verify / deactivate / delete. The seeded "DEMO RECEPTION NUMBER —
 * TEAM CONTROLLED" entry appears here and is clearly flagged.
 */
export function AmbulanceDirectoryAdmin() {
  const [rows, setRows] = useState<AmbulanceServiceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const [showModal, setShowModal] = useState(false);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [city, setCity] = useState("");
  const [isDemo, setIsDemo] = useState(false);
  const [busy, setBusy] = useState(false);
  const [modalErr, setModalErr] = useState("");

  const refresh = () => {
    setLoading(true);
    listServices(true)
      .then((r) => {
        setRows(r);
        setErr("");
      })
      .catch((e) => setErr(e?.message || "Could not load services"))
      .finally(() => setLoading(false));
  };

  useEffect(refresh, []);

  const add = async () => {
    if (!name.trim() || !phone.trim()) {
      setModalErr("Name and phone are required.");
      return;
    }
    setBusy(true);
    setModalErr("");
    try {
      await createService({ name: name.trim(), phone: phone.trim(), city: city.trim() || undefined, is_demo: isDemo });
      setShowModal(false);
      setName("");
      setPhone("");
      setCity("");
      setIsDemo(false);
      refresh();
    } catch (e: any) {
      setModalErr(e?.message || "Could not add service");
    } finally {
      setBusy(false);
    }
  };

  const doVerify = async (id: string) => {
    try {
      await verifyService(id);
      refresh();
    } catch (e: any) {
      setErr(e?.message || "Could not verify");
    }
  };

  const toggleActive = async (s: AmbulanceServiceItem) => {
    try {
      await updateService(s.id, { active: !s.active });
      refresh();
    } catch (e: any) {
      setErr(e?.message || "Could not update");
    }
  };

  const remove = async (id: string) => {
    try {
      await deleteService(id);
      refresh();
    } catch (e: any) {
      setErr(e?.message || "Could not delete");
    }
  };

  return (
    <section className="border-2 border-black bg-white p-4">
      <div className="mb-1 text-xs font-black uppercase">Ambulance / Hospital Directory</div>
      <p className="mb-3 text-[11px] text-black/60">
        Configured reception numbers ResQAI may call. Only an active + verified (or demo) number
        can be dialed. Numbers are shown masked.
      </p>
      {err && (
        <div className="mb-2 border-2 border-[#C62828] bg-white px-3 py-2 text-[11px] font-semibold text-[#C62828]">
          {err}
        </div>
      )}
      <div className="space-y-2">
        {loading && <div className="text-xs text-black/50">Loading…</div>}
        {!loading && rows.length === 0 && (
          <div className="border-2 border-dashed border-black/40 px-3 py-2 text-[11px] text-black/50">
            No services configured. Add one (e.g. the team demo reception number).
          </div>
        )}
        {rows.map((s) => (
          <div key={s.id} className="border-2 border-black bg-white p-2">
            <div className="flex items-center gap-2">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-bold">{s.name}</div>
                <div className="truncate font-mono text-[11px] text-black/60">{s.phone}</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-2">
                  <span
                    className={`text-[9px] font-bold uppercase ${s.active ? "text-green-700" : "text-black/40"}`}
                  >
                    {s.active ? "Active" : "Inactive"}
                  </span>
                  {s.is_demo ? (
                    <span className="border border-amber-500 bg-amber-100 px-1 text-[9px] font-black uppercase text-amber-800">
                      Demo — team controlled
                    </span>
                  ) : s.verified ? (
                    <span className="text-[9px] font-bold uppercase text-green-700">✓ Verified</span>
                  ) : (
                    <span className="text-[9px] font-bold uppercase text-amber-600">Unverified</span>
                  )}
                </div>
              </div>
              {!s.verified && !s.is_demo && (
                <button
                  onClick={() => doVerify(s.id)}
                  title="Mark verified"
                  className="flex h-8 shrink-0 items-center gap-1 border-2 border-black bg-white px-2 text-[10px] font-black uppercase"
                >
                  <BadgeCheck size={13} /> Verify
                </button>
              )}
              <button
                onClick={() => toggleActive(s)}
                className={`flex h-8 shrink-0 items-center justify-center border-2 border-black px-2 text-[10px] font-black uppercase ${s.active ? "bg-green-600 text-white" : "bg-white text-black"}`}
              >
                {s.active ? "ON" : "OFF"}
              </button>
              <button
                onClick={() => remove(s.id)}
                className="flex h-8 w-8 shrink-0 items-center justify-center border-2 border-black bg-white text-[#C62828]"
              >
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        ))}
        <button
          onClick={() => setShowModal(true)}
          className="flex w-full items-center justify-center gap-1 border-2 border-dashed border-black bg-white py-3 text-xs font-black uppercase hover:bg-black/5"
        >
          <Plus size={14} /> Add Service
        </button>
      </div>

      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
          <div className="w-full max-w-sm border-2 border-black bg-white p-6">
            <div className="mb-4 text-sm font-black uppercase">Add Ambulance Service</div>
            {modalErr && (
              <div className="mb-3 border-2 border-[#C62828] bg-white px-3 py-2 text-xs font-semibold text-[#C62828]">
                {modalErr}
              </div>
            )}
            <div className="space-y-3">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Service / hospital name"
                className="w-full border-2 border-black bg-white px-3 py-2 text-sm font-semibold placeholder:text-black/30"
              />
              <input
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="Phone (E.164, e.g. +9198...)"
                className="w-full border-2 border-black bg-white px-3 py-2 text-sm font-semibold placeholder:text-black/30"
              />
              <input
                value={city}
                onChange={(e) => setCity(e.target.value)}
                placeholder="City (optional)"
                className="w-full border-2 border-black bg-white px-3 py-2 text-sm font-semibold placeholder:text-black/30"
              />
              <label className="flex items-center gap-2 text-[11px] font-bold">
                <input
                  type="checkbox"
                  checked={isDemo}
                  onChange={(e) => setIsDemo(e.target.checked)}
                  className="h-4 w-4 border-2 border-black"
                />
                This is a DEMO reception number (team-controlled)
              </label>
            </div>
            <div className="mt-4 flex gap-2">
              <button
                onClick={() => setShowModal(false)}
                className="flex-1 border-2 border-black bg-white py-2 text-xs font-black uppercase"
              >
                Cancel
              </button>
              <button
                onClick={add}
                disabled={busy}
                className="flex-1 border-2 border-black bg-[#C62828] py-2 text-xs font-black uppercase text-white disabled:opacity-60"
              >
                {busy ? "Adding…" : "Add"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
