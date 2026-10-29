// Thin API client with JWT refresh. The short-lived access token and the
// rotating refresh token are kept in localStorage so the installed PWA stays
// signed in across launches; the server's CSP limits XSS exposure.
const KEY = "opsdesk.auth";
let auth = (() => { try { return JSON.parse(localStorage.getItem(KEY)) || null; } catch { return null; } })();
let refreshing = null;
const listeners = new Set();

export const getAuth = () => auth;
export const onAuthChange = (fn) => (listeners.add(fn), () => listeners.delete(fn));

function setAuth(next) {
  auth = next;
  try { next ? localStorage.setItem(KEY, JSON.stringify(next)) : localStorage.removeItem(KEY); } catch { /* private mode */ }
  listeners.forEach((fn) => fn(next));
}

export class ApiError extends Error {
  constructor(status, data) {
    super((data && (data.detail || Object.values(data).flat().join(" "))) || `Request failed (${status})`);
    this.status = status;
    this.data = data;
  }
}

async function refresh() {
  if (!auth?.refresh) throw new ApiError(401, { detail: "Session expired" });
  refreshing ||= fetch("/api/auth/refresh/", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh: auth.refresh }),
  }).then(async (r) => {
    if (!r.ok) { setAuth(null); throw new ApiError(401, { detail: "Your session ended. Sign in again." }); }
    const d = await r.json();
    setAuth({ ...auth, access: d.access, refresh: d.refresh || auth.refresh });
  }).finally(() => { refreshing = null; });
  return refreshing;
}

const isMobile = () => window.matchMedia("(display-mode: standalone)").matches || /Android|iPhone|iPad/i.test(navigator.userAgent);

/** `headers` adds request headers (e.g. If-Match for the administration APIs, U7-A2/A3). `signal` is an optional AbortSignal (post-U8: workspace load timeout). */
export async function api(path, { method = "GET", body, form, retry = true, headers: extra, signal } = {}) {
  const headers = { ...extra, "X-Client": isMobile() ? "mobile" : "web" };
  if (auth?.access) headers.Authorization = `Bearer ${auth.access}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(`/api${path}`, { method, headers, body: payload, signal });
  if (res.status === 401 && retry && auth?.refresh) {
    await refresh();
    return api(path, { method, body, form, retry: false, headers: extra, signal });
  }
  const text = await res.text();
  const data = text ? (() => { try { return JSON.parse(text); } catch { return { detail: text }; } })() : null;
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

export async function login(email, password, otp) {
  const res = await fetch("/api/auth/login/", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password, otp }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data);
  setAuth({ access: data.access, refresh: data.refresh, user: data.user });
  return data.user;
}

export function logout() {
  const refreshToken = auth?.refresh;
  setAuth(null);
  if (refreshToken) {  // best effort: revoke the refresh token on the server
    fetch("/api/auth/logout/", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh: refreshToken }), keepalive: true }).catch(() => {});
  }
}
export const updateUser = (user) => setAuth({ ...auth, user });

export async function download(url, filename) {
  const res = await fetch(url, { headers: { Authorization: `Bearer ${auth?.access}` } });
  if (res.redirected || res.headers.get("content-type")?.includes("json")) {
    const d = await res.json().catch(() => null);
    if (d?.detail) throw new ApiError(res.status, d);
  }
  const blob = await res.blob();
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: filename });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
