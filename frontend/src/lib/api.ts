// Strip any trailing slash(es) so a VITE_API_URL like "https://host/" doesn't
// produce double-slash request paths ("https://host//api/...") which 404.
const BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? "").replace(/\/+$/, "");

export const apiUrl = (path: string) => `${BASE}${path}`;

export const wsUrl = (path: string) => {
  if (BASE) {
    return BASE.replace(/^http/, "ws") + path;
  }
  if (typeof window === "undefined") return path;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${path}`;
};

// ── Session token ─────────────────────────────────────────────────────────────
// Issued by the backend at OTP verify and stored locally. It is the credential
// that authorizes per-user endpoints (contacts, SOS). Never a provider secret.
const TOKEN_KEY = "resq_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* ignore */
  }
}

export function clearToken() {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export function authHeader(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export async function apiFetch<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(apiUrl(path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeader(),
      ...(init?.headers || {}),
    },
  });
  const text = await res.text();
  let data: any;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: text };
  }
  if (!res.ok) {
    // FastAPI uses `detail`; our handlers sometimes use `error`.
    const msg = data?.detail || data?.error || `Request failed: ${res.status}`;
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    (err as any).status = res.status;
    throw err;
  }
  return data as T;
}
