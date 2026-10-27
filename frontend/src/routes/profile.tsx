import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Bell, ShieldCheck, Trash2, X } from "lucide-react";
import { AuthGuard } from "@/components/AuthGuard";
import { BottomNav } from "@/components/BottomNav";
import { INDIAN_LANGUAGES } from "@/lib/types";
import { clearToken } from "@/lib/api";
import {
  createContact,
  deleteContact,
  listContacts,
  sendContactVerification,
  updateContact,
  type Contact,
} from "@/lib/emergencyApi";

export const Route = createFileRoute("/profile")({
  head: () => ({
    meta: [
      { title: "Profile — ResQ AI" },
      { name: "description", content: "Manage your medical information and preferences." },
    ],
  }),
  component: () => (
    <AuthGuard>
      <ProfilePage />
    </AuthGuard>
  ),
});

const BLOOD = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", "Unknown"];

function initials(name: string) {
  return name
    .split(" ")
    .map((w) => w[0] ?? "")
    .join("")
    .toUpperCase()
    .slice(0, 2);
}

function load<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v ? (JSON.parse(v) as T) : fallback;
  } catch {
    return fallback;
  }
}

function ProfilePage() {
  const navigate = useNavigate();

  // ── Personal ──────────────────────────────────────────────────────────────
  const [name, setName] = useState(() => load("resq_name", ""));
  const [phone, setPhone] = useState("");

  // ── Medical ───────────────────────────────────────────────────────────────
  const [blood, setBlood] = useState(() => load("resq_blood", "O+"));
  const [age, setAge] = useState<number>(() => load("resq_age", 28));
  const [allergies, setAllergies] = useState(() => load("resq_allergies", ""));
  const [conditions, setConditions] = useState(() => load("resq_conditions", ""));

  // ── Preferences ───────────────────────────────────────────────────────────
  const [language, setLanguage] = useState(() => load("resq_language", "English"));
  const [push, setPush] = useState(() => load("resq_push", true));

  // ── Emergency contacts (backend-owned, per-user, email + verification) ──────
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [contactsErr, setContactsErr] = useState("");
  const [contactsLoading, setContactsLoading] = useState(true);

  // ── Add-contact modal ─────────────────────────────────────────────────────
  const [saved, setSaved] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [newName, setNewName] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newRel, setNewRel] = useState("");
  const [modalErr, setModalErr] = useState("");
  const [modalBusy, setModalBusy] = useState(false);

  // ── Per-contact verification feedback ───────────────────────────────────────
  const [verifyBusy, setVerifyBusy] = useState<string | null>(null);
  const [verifyMsg, setVerifyMsg] = useState<{ id: string; text: string; devUrl?: string } | null>(
    null,
  );

  const refreshContacts = () => {
    setContactsLoading(true);
    listContacts()
      .then((rows) => {
        setContacts(rows);
        setContactsErr("");
      })
      .catch((e) => setContactsErr(e?.message || "Could not load contacts"))
      .finally(() => setContactsLoading(false));
  };

  useEffect(() => {
    setPhone(localStorage.getItem("resq_phone") || "");
    refreshContacts();
  }, []);

  const signOut = () => {
    localStorage.removeItem("resq_phone");
    clearToken();
    navigate({ to: "/auth" });
  };

  const removeContact = async (id: string) => {
    try {
      await deleteContact(id);
      setContacts((prev) => prev.filter((c) => c.id !== id));
    } catch (e: any) {
      setContactsErr(e?.message || "Could not remove contact");
    }
  };

  const toggleActive = async (c: Contact) => {
    try {
      const updated = await updateContact(c.id, { active: !c.active });
      setContacts((prev) => prev.map((x) => (x.id === c.id ? updated : x)));
    } catch (e: any) {
      setContactsErr(e?.message || "Could not update contact");
    }
  };

  const openModal = () => {
    setNewName("");
    setNewEmail("");
    setNewRel("");
    setModalErr("");
    setShowModal(true);
  };

  const addContact = async () => {
    if (!newName.trim()) {
      setModalErr("Name is required.");
      return;
    }
    if (!newEmail.trim()) {
      setModalErr("Email address is required.");
      return;
    }
    setModalBusy(true);
    setModalErr("");
    try {
      const created = await createContact({
        name: newName.trim(),
        email: newEmail.trim(),
        relationship: newRel.trim() || undefined,
      });
      setContacts((prev) => [...prev, created]);
      setShowModal(false);
    } catch (e: any) {
      setModalErr(e?.message || "Could not add contact");
    } finally {
      setModalBusy(false);
    }
  };

  const verifyContact = async (c: Contact) => {
    setVerifyBusy(c.id);
    setVerifyMsg(null);
    try {
      const r = await sendContactVerification(c.id);
      if (r.dev_verification_url) {
        // Dev/mock provider: no real inbox, so surface the link to complete the loop.
        setVerifyMsg({
          id: c.id,
          text: "Demo mode — open this link to confirm the contact:",
          devUrl: r.dev_verification_url,
        });
      } else if (r.sent) {
        setVerifyMsg({
          id: c.id,
          text: `Verification email sent to ${c.email}. Ask them to confirm.`,
        });
      } else {
        setVerifyMsg({ id: c.id, text: r.detail || "Could not send the verification email." });
      }
    } catch (e: any) {
      setVerifyMsg({ id: c.id, text: e?.message || "Could not send verification." });
    } finally {
      setVerifyBusy(null);
    }
  };

  // Contacts are saved immediately via the API; this persists the rest.
  const saveAll = () => {
    localStorage.setItem("resq_name", JSON.stringify(name));
    localStorage.setItem("resq_blood", JSON.stringify(blood));
    localStorage.setItem("resq_age", JSON.stringify(age));
    localStorage.setItem("resq_allergies", JSON.stringify(allergies));
    localStorage.setItem("resq_conditions", JSON.stringify(conditions));
    localStorage.setItem("resq_language", JSON.stringify(language));
    localStorage.setItem("resq_push", JSON.stringify(push));
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  const discardAll = () => {
    setName(load("resq_name", ""));
    setBlood(load("resq_blood", "O+"));
    setAge(load("resq_age", 28));
    setAllergies(load("resq_allergies", ""));
    setConditions(load("resq_conditions", ""));
    setLanguage(load("resq_language", "English"));
    setPush(load("resq_push", true));
    refreshContacts();
  };

  return (
    <div className="min-h-screen bg-white">
      {/* ── Header ── */}
      <header className="sticky top-0 z-20 flex items-center justify-between border-b-2 border-black bg-white px-4 py-3">
        <span className="text-lg font-black text-[#C62828]">RESQ AI</span>
        <div className="flex items-center gap-3">
          <Bell size={20} strokeWidth={2.5} />
          <div className="flex h-9 w-9 items-center justify-center border-2 border-black bg-white text-xs font-bold">
            {name ? initials(name) : (phone || "U").slice(-2)}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1140px] px-4 py-4">
        <div>
          <h1 className="text-xl font-black uppercase text-black">Profile & Settings</h1>
          <p className="mt-1 text-xs text-black/60">Manage your critical information.</p>
        </div>

        <div className="mt-4 grid gap-4 md:grid-cols-2">
          {/* ── Left column ── */}
          <div className="space-y-4">
            {/* Personal Info */}
            <section className="border-2 border-black bg-white p-4">
              <div className="mb-3 text-xs font-black uppercase">Personal Info</div>
              <div className="space-y-3">
                <div>
                  <label className="mb-1 block text-[10px] font-bold uppercase">Full Name</label>
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Your full name"
                    className="w-full border-2 border-black bg-white px-3 py-2 text-sm font-semibold placeholder:text-black/30"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[10px] font-bold uppercase">Phone Number</label>
                  <input
                    value={phone}
                    readOnly
                    className="w-full border-2 border-black bg-black/5 px-3 py-2 text-sm font-semibold text-black/60"
                  />
                  <p className="mt-1 text-[10px] text-black/40">
                    Verified via OTP — cannot be changed here.
                  </p>
                </div>
              </div>
            </section>

            {/* Medical Profile */}
            <section className="border-2 border-black bg-white p-4">
              <div className="mb-3 text-xs font-black uppercase">Medical Profile</div>
              <div className="space-y-3">
                <div>
                  <label className="mb-1 block text-[10px] font-bold uppercase">Blood Group</label>
                  <select
                    value={blood}
                    onChange={(e) => setBlood(e.target.value)}
                    className="w-full border-2 border-black bg-white px-3 py-2 text-sm font-semibold"
                  >
                    {BLOOD.map((b) => (
                      <option key={b}>{b}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-[10px] font-bold uppercase">Age</label>
                  <input
                    type="number"
                    value={age}
                    onChange={(e) => setAge(Number(e.target.value))}
                    className="w-full border-2 border-black bg-white px-3 py-2 text-sm font-semibold"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[10px] font-bold uppercase">Allergies</label>
                  <input
                    value={allergies}
                    onChange={(e) => setAllergies(e.target.value)}
                    placeholder="e.g. Peanuts, Penicillin"
                    className="w-full border-2 border-black bg-white px-3 py-2 text-sm font-semibold placeholder:text-black/30"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[10px] font-bold uppercase">
                    Chronic Conditions
                  </label>
                  <textarea
                    value={conditions}
                    onChange={(e) => setConditions(e.target.value)}
                    rows={3}
                    placeholder="e.g. Diabetes, Hypertension"
                    className="w-full border-2 border-black bg-white px-3 py-2 text-sm font-semibold placeholder:text-black/30"
                  />
                </div>
              </div>
            </section>
          </div>

          {/* ── Right column ── */}
          <div className="space-y-4">
            {/* Emergency Contacts */}
            <section className="border-2 border-black bg-white p-4">
              <div className="mb-1 text-xs font-black uppercase">Emergency Contacts (Email)</div>
              <p className="mb-3 text-[11px] text-black/60">
                These contacts receive an email with your location when you trigger SOS. A contact
                must confirm their email before it can be alerted — saving one never implies
                consent.
              </p>
              {contactsErr && (
                <div className="mb-2 border-2 border-[#C62828] bg-white px-3 py-2 text-[11px] font-semibold text-[#C62828]">
                  {contactsErr}
                </div>
              )}
              <div className="space-y-2">
                {contactsLoading && <div className="text-xs text-black/50">Loading contacts…</div>}
                {!contactsLoading && contacts.length === 0 && (
                  <div className="border-2 border-dashed border-black/40 px-3 py-2 text-[11px] text-black/50">
                    No contacts yet. Add one and have them confirm their email so SOS can alert
                    them.
                  </div>
                )}
                {contacts.map((c) => (
                  <div key={c.id} className="border-2 border-black bg-white p-2">
                    <div className="flex items-center gap-3">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center border-2 border-black bg-black text-xs font-black text-white">
                        {initials(c.name)}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-bold text-black">
                          {c.name}
                          {c.relationship && (
                            <span className="ml-1 text-[10px] font-normal text-black/50">
                              · {c.relationship}
                            </span>
                          )}
                        </div>
                        <div className="truncate text-[11px] text-black/60">{c.email}</div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-2">
                          <span
                            className={`text-[9px] font-bold uppercase ${c.active ? "text-green-700" : "text-black/40"}`}
                          >
                            {c.active ? "Alerts ON" : "Alerts OFF"}
                          </span>
                          <span className="text-[9px] font-bold uppercase text-black/40">
                            Priority {c.priority}
                          </span>
                          {c.suppressed ? (
                            <span className="text-[9px] font-bold uppercase text-[#C62828]">
                              Bounced / suppressed
                            </span>
                          ) : c.verified ? (
                            <span className="text-[9px] font-bold uppercase text-green-700">
                              ✓ Verified
                            </span>
                          ) : (
                            <span className="text-[9px] font-bold uppercase text-amber-600">
                              Unverified
                            </span>
                          )}
                        </div>
                      </div>
                      <button
                        onClick={() => toggleActive(c)}
                        title={c.active ? "Disable alerts" : "Enable alerts"}
                        className={`flex h-8 shrink-0 items-center justify-center border-2 border-black px-2 text-[10px] font-black uppercase ${c.active ? "bg-green-600 text-white" : "bg-white text-black"}`}
                      >
                        {c.active ? "ON" : "OFF"}
                      </button>
                      <button
                        onClick={() => removeContact(c.id)}
                        className="flex h-8 w-8 shrink-0 items-center justify-center border-2 border-black bg-white text-[#C62828]"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>

                    {(!c.verified || c.suppressed) && (
                      <button
                        onClick={() => verifyContact(c)}
                        disabled={verifyBusy === c.id}
                        className="mt-2 w-full border-2 border-black bg-white py-1.5 text-[10px] font-black uppercase text-black hover:bg-black/5 disabled:opacity-60"
                      >
                        {verifyBusy === c.id
                          ? "Sending…"
                          : c.suppressed
                            ? "Re-send verification"
                            : "Send verification email"}
                      </button>
                    )}
                    {verifyMsg && verifyMsg.id === c.id && (
                      <div className="mt-2 border-2 border-amber-500 bg-amber-50 px-2 py-1.5 text-[10px] text-amber-900">
                        {verifyMsg.text}
                        {verifyMsg.devUrl && (
                          <a
                            href={verifyMsg.devUrl}
                            target="_blank"
                            rel="noreferrer"
                            onClick={() => setTimeout(refreshContacts, 800)}
                            className="mt-1 block truncate font-bold text-[#C62828] underline"
                          >
                            {verifyMsg.devUrl}
                          </a>
                        )}
                      </div>
                    )}
                  </div>
                ))}
                <button
                  onClick={openModal}
                  className="w-full border-2 border-dashed border-black bg-white py-3 text-xs font-black uppercase text-black hover:bg-black/5"
                >
                  + Add Contact
                </button>
              </div>
            </section>

            {/* Preferences */}
            <section className="border-2 border-black bg-white p-4">
              <div className="mb-3 text-xs font-black uppercase">Preferences</div>
              <div className="space-y-3">
                <div>
                  <label className="mb-1 block text-[10px] font-bold uppercase">Language</label>
                  <select
                    value={language}
                    onChange={(e) => setLanguage(e.target.value)}
                    className="w-full border-2 border-black bg-white px-3 py-2 text-sm font-semibold"
                  >
                    {INDIAN_LANGUAGES.map((l) => (
                      <option key={l}>{l}</option>
                    ))}
                  </select>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold uppercase">Push Notifications</span>
                  <button
                    onClick={() => setPush((p) => !p)}
                    className={`relative h-7 w-12 border-2 border-black ${push ? "bg-[#C62828]" : "bg-white"}`}
                  >
                    <span
                      className={`absolute top-0.5 h-4 w-4 border-2 border-black bg-white transition-all ${push ? "left-6" : "left-0.5"}`}
                    />
                  </button>
                </div>
              </div>
            </section>

            {/* Account */}
            <section className="border-2 border-black bg-white p-4">
              <div className="mb-3 text-xs font-black uppercase">Account</div>
              <button
                onClick={signOut}
                className="w-full rounded border-2 border-[#C62828] bg-white px-4 py-2 text-sm font-black uppercase text-[#C62828]"
              >
                Sign Out
              </button>
            </section>
          </div>
        </div>

        {/* ── Save / Discard ── */}
        <div className="mt-4 flex gap-3">
          <button
            onClick={discardAll}
            className="flex-1 border-2 border-black bg-white px-4 py-3 text-sm font-black uppercase"
          >
            Discard
          </button>
          <button
            onClick={saveAll}
            className={`flex-1 border-2 border-black px-4 py-3 text-sm font-black uppercase text-white transition-colors ${
              saved ? "border-green-700 bg-green-600" : "bg-black"
            }`}
          >
            {saved ? "✓ Saved!" : "Save Changes"}
          </button>
        </div>
      </main>

      <BottomNav />

      {/* ── Add Contact Modal ── */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
          <div className="w-full max-w-sm border-2 border-black bg-white p-6">
            <div className="mb-4 flex items-center justify-between">
              <span className="text-sm font-black uppercase">Add Emergency Contact</span>
              <button onClick={() => setShowModal(false)}>
                <X size={18} />
              </button>
            </div>

            {modalErr && (
              <div className="mb-3 border-2 border-[#C62828] bg-white px-3 py-2 text-xs font-semibold text-[#C62828]">
                {modalErr}
              </div>
            )}

            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-[10px] font-bold uppercase">Full Name</label>
                <input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="Contact's full name"
                  className="w-full border-2 border-black bg-white px-3 py-2 text-sm font-semibold placeholder:text-black/30"
                />
              </div>
              <div>
                <label className="mb-1 block text-[10px] font-bold uppercase">Email Address</label>
                <input
                  type="email"
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                  placeholder="contact@email.com"
                  className="w-full border-2 border-black bg-white px-3 py-2 text-sm font-semibold placeholder:text-black/30"
                />
                <p className="mt-1 text-[10px] text-black/50">
                  Emergency alerts are sent to this address. They must confirm it before they can be
                  alerted.
                </p>
              </div>
              <div>
                <label className="mb-1 block text-[10px] font-bold uppercase">
                  Relationship (optional)
                </label>
                <input
                  value={newRel}
                  onChange={(e) => setNewRel(e.target.value)}
                  placeholder="e.g. Mother, Friend"
                  className="w-full border-2 border-black bg-white px-3 py-2 text-sm font-semibold placeholder:text-black/30"
                />
              </div>
              <div className="flex items-start gap-2 border-2 border-black/20 bg-black/5 px-3 py-2">
                <ShieldCheck size={16} className="mt-0.5 shrink-0 text-black/50" />
                <p className="text-[10px] text-black/60">
                  When you trigger SOS, this contact will receive an email with your name, a
                  timestamp, your location link (if available) and an incident reference. Only add
                  people who consent to being your emergency contact; they must confirm their email
                  first.
                </p>
              </div>
            </div>

            <div className="mt-4 flex gap-2">
              <button
                onClick={() => setShowModal(false)}
                className="flex-1 border-2 border-black bg-white py-2 text-xs font-black uppercase"
              >
                Cancel
              </button>
              <button
                onClick={addContact}
                disabled={modalBusy}
                className="flex-1 border-2 border-black bg-[#C62828] py-2 text-xs font-black uppercase text-white disabled:opacity-60"
              >
                {modalBusy ? "Adding…" : "Add"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
