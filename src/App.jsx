import React, { Suspense, createContext, lazy, useCallback, useContext, useEffect, useRef, useState } from "react";
import { isAdminRole } from "./roles.js";
import { api, getAuth, logout, onAuthChange, updateUser } from "./api.js";
import { IconBook, IconChart, IconPlus, IconQueue, IconUser, IconCatalog, IconCheckCircle, IconMore, IconLogout } from "./components/Icons.jsx";
import { cx, initials } from "./util.js";
import useDialogFocus from "./components/useDialogFocus.js";
import {
  GUARD_REDIRECT, ADMIN_HOME, adminOnlyPathAllowed, createTab, railViews, csatToken, customerGuard, homePath, isAdministrationOnly, isCreateSection,
  isMoreSection, landsOnDashboard, loadsWorkspaceData, moreItems, primaryAction, sectionOf, workspaceLoadError,
} from "./navModel.js";
import { FEATURES, MOVED, isDisabledPath } from "./features.js";
import { loadWorkspaceMeta } from "./workspaceMeta.js";
import Login from "./pages/Login.jsx";
import Queue from "./pages/Queue.jsx";
import NewTicket from "./pages/NewTicket.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Knowledge from "./pages/Knowledge.jsx";
import Profile from "./pages/Profile.jsx";
import Csat from "./pages/Csat.jsx";
import Catalog from "./pages/Catalog.jsx";
import Approvals from "./pages/Approvals.jsx";
import CmdbExplorer from "./pages/CmdbExplorer.jsx";
import AuditLog from "./pages/AuditLog.jsx";
// U7 administration pages load on first use, so other roles never download them.
const AdminDashboard = lazy(() => import("./pages/AdminDashboard.jsx"));
const Users = lazy(() => import("./pages/Users.jsx"));
const Teams = lazy(() => import("./pages/Teams.jsx"));
const SlaConfig = lazy(() => import("./pages/SlaConfig.jsx"));
const Configuration = lazy(() => import("./pages/Configuration.jsx"));
const lazyPage = (el) => <Suspense fallback={<div className="placeholder" role="status">Loading…</div>}>{el}</Suspense>;
import { settingsHome, settingsTabs } from "./pages/adminHelpers.js";
import CommandPalette from "./components/CommandPalette.jsx";
import NotificationsBell from "./components/NotificationsBell.jsx";
import { NotificationsProvider } from "./components/useNotificationFeed.js";

const Ctx = createContext(null);
export const useApp = () => useContext(Ctx);

function useLocationState() {
  const read = () => ({ path: window.location.pathname, search: new URLSearchParams(window.location.search) });
  const [loc, setLoc] = useState(read);
  useEffect(() => {
    const on = () => setLoc(read());
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  const navigate = useCallback((to, { replace } = {}) => {
    window.history[replace ? "replaceState" : "pushState"]({}, "", to);
    setLoc(read());
  }, []);
  return [loc, navigate];
}

export const AGENT_VIEWS = [
  { key: "mine", label: "Assigned to me", params: { assignee: "me", open: 1, ordering: "due" } },
  { key: "unassigned", label: "Unassigned", params: { assignee: "none", open: 1, ordering: "oldest" } },
  { key: "sla", label: "At risk or breached", params: { sla_status: "at_risk,breached", open: 1, ordering: "due" }, hot: true },
  { key: "major", label: "Major incidents", params: { major: 1, open: 1 }, hot: true },
  { key: "open", label: "All open", params: { open: 1, ordering: "updated" } },
  { key: "done", label: "Resolved and closed", params: { state_category: "resolved,closed,cancelled", ordering: "updated" } },
];
export const CUSTOMER_VIEWS = [
  { key: "open", label: "Open requests", params: { open: 1, ordering: "updated" } },
  { key: "done", label: "Resolved", params: { state_category: "resolved,closed,cancelled", ordering: "updated" } },
];

const MORE_ICONS = {
  new: IconPlus, catalog: IconCatalog, dashboard: IconChart, approvals: IconCheckCircle, cmdb: IconCatalog, settings: IconBook, profile: IconUser,
  overview: IconChart, users: IconUser, teams: IconQueue, sla: IconChart, config: IconCatalog, "audit-log": IconBook,
};

/**
 * MoreSheet — B1 mobile navigation More slide-over (U2.4, UI-DEC-011).
 * Holds the secondary destinations, the agent availability toggle and Sign out,
 * none of which have a home on the bottom bar. Focus handling is shared with Modal
 * (useDialogFocus): trap, Escape from anywhere, and focus returned to the More tab on close.
 */
function MoreSheet({ isOpen, onClose, navigate, user, section, onToggleAvailability, availabilityError }) {
  const sheetRef = useRef(null);
  useDialogFocus(sheetRef, onClose, isOpen);

  if (!isOpen) return null;

  const go = (to) => { navigate(to); onClose(); };

  return (
    <div className="more-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={sheetRef} className="more-sheet" role="dialog" aria-modal="true" aria-labelledby="more-sheet-title" tabIndex={-1}>
        <div className="more-sheet-handle" aria-hidden="true" />
        <h2 className="more-sheet-title" id="more-sheet-title">More</h2>
        <nav className="more-sheet-nav" aria-label="More navigation">
          {moreItems(user).map(({ key, to, label }) => {
            const Icon = MORE_ICONS[key];
            return (
              <button key={key} type="button" className={cx("more-sheet-item", (section === key || window.location.pathname.startsWith(to)) && "active")}
                onClick={() => go(to)} aria-current={section === key || window.location.pathname.startsWith(to) ? "page" : undefined}>
                <Icon />
                <span>{label}</span>
              </button>
            );
          })}
        </nav>
        <div className="more-sheet-prefs">
          {/* U5.5: the rail (and its bell) is hidden below 768px, so notifications are reached here */}
          {!isAdministrationOnly(user) && <NotificationsBell navigate={navigate} variant="sheet" onNavigate={onClose} />}
          {user.role === "employee" && (
            <label className="toggle more-sheet-toggle">
              <input type="checkbox" checked={user.is_available} onChange={onToggleAvailability} />
              <span>Available for new tickets</span>
            </label>
          )}
          {availabilityError && <div className="error" role="alert">{availabilityError}</div>}
          <button type="button" className="more-sheet-item" onClick={logout}>
            <IconLogout />
            <span>Sign out</span>
          </button>
        </div>
        <button type="button" className="btn sm ghost more-sheet-close" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}

const ADMIN_TAB_ICONS = { users: IconUser, teams: IconQueue, sla: IconChart, config: IconCatalog, "audit-log": IconBook };
function AdminTabIcon({ tab }) {
  const Icon = ADMIN_TAB_ICONS[tab] || IconBook;
  return <Icon />;
}

/** Standalone public layout for /csat/:token (U2.5, UI-DEC-010): no workspace chrome or user data. */
function PublicShell({ children }) {
  return (
    <div className="public-shell">
      <header className="public-shell-h">
        <img src="/icon.svg" alt="" />
        <span>OpsDesk</span>
      </header>
      <main className="public-shell-main">{children}</main>
    </div>
  );
}

/** Notice shown while a customer is steered off a staff page (UI-DEC-005, UX aid only). */
function GuardNotice({ message, navigate }) {
  return (
    <div className="page">
      <h1 className="sr-only">Page not available</h1>
      <div className="notice bad" role="status">
        {message} Taking you to your tickets…{" "}
        <a href={GUARD_REDIRECT} onClick={(e) => { e.preventDefault(); navigate(GUARD_REDIRECT, { replace: true }); }}>
          Go now
        </a>
      </div>
    </div>
  );
}

export default function App() {
  const [auth, setAuthState] = useState(getAuth());
  const [loc, navigate] = useLocationState();
  const [meta, setMeta] = useState(null);
  const [metaError, setMetaError] = useState("");
  const [metaAttempt, setMetaAttempt] = useState(0);
  const [counts, setCounts] = useState({});
  const [tick, setTick] = useState(0);
  const [cmdOpen, setCmdOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [availabilityError, setAvailabilityError] = useState("");
  const refreshCounts = useCallback(() => setTick((n) => n + 1), []);
  const closeMore = useCallback(() => setMoreOpen(false), []);

  // U2.3: customer route guard (UI-DEC-005). The redirect is scheduled once per guarded visit and
  // cancelled if the user leaves first; the guarded page itself is never mounted. UX aid only —
  // the backend still authorizes every request.
  const guardNotice = customerGuard(loc.path, auth?.user);
  useEffect(() => {
    if (!guardNotice) return undefined;
    const id = setTimeout(() => navigate(GUARD_REDIRECT, { replace: true }), 1500);
    return () => clearTimeout(id);
  }, [guardNotice, navigate]);

  // Signing out returns to "/", so the next sign-in starts from the role's home screen. Visiting a deep link while
  // signed out is not a sign-out, so that link is kept and still opens after sign-in.
  const wasSignedIn = useRef(Boolean(auth));
  useEffect(() => {
    if (wasSignedIn.current && !auth && loc.path !== "/") navigate("/", { replace: true });
    wasSignedIn.current = Boolean(auth);
  }, [auth, loc.path, navigate]);

  // A route of a switched-off feature (old bookmark, typed URL) goes home instead of rendering a stray page.
  const featureOff = isDisabledPath(loc.path);
  useEffect(() => {
    if (featureOff) navigate(auth ? homePath(auth.user) : "/", { replace: true });
  }, [featureOff, auth, navigate]);

  const movedTo = MOVED[loc.path];
  useEffect(() => {
    if (movedTo) navigate(movedTo + window.location.search, { replace: true });
  }, [movedTo, navigate]);

  // Management roles open on the Dashboard rather than the ticket queue (replace: Back doesn't bounce to "/").
  const landing = auth ? homePath(auth.user) : "/";
  useEffect(() => {
    if (loc.path === "/" && landing !== "/") navigate(landing, { replace: true });
  }, [loc.path, landing, navigate]);

  // The super admin only has Administration and a profile: tickets, dashboard and knowledge base links go back to it.
  const adminOnly = isAdministrationOnly(auth?.user);
  const adminOffLimits = adminOnly && !adminOnlyPathAllowed(loc.path);
  useEffect(() => {
    if (adminOffLimits) navigate(landing, { replace: true });
  }, [adminOffLimits, landing, navigate]);

  useEffect(() => onAuthChange(setAuthState), []);
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    // Client-side navigation for in-app links (keeps the PWA shell loaded).
    const onClick = (e) => {
      const a = e.target.closest("a[href]");
      if (!a || a.target || a.hasAttribute("download") || e.metaKey || e.ctrlKey || e.shiftKey || e.defaultPrevented) return;
      const url = new URL(a.href, window.location.href);
      // blob: URLs share the page origin, so the protocol check keeps file saves (exports, attachments) out of routing.
      if (!/^https?:$/.test(url.protocol) || url.origin !== window.location.origin || url.pathname.startsWith("/api")) return;
      e.preventDefault();
      navigate(url.pathname + url.search);
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [navigate]);
  // Workspace data is only requested off public routes, so a staff session left in the browser
  // makes no authenticated calls while a /csat/:token survey is open (U2.5, UI-DEC-010).
  const workspace = loadsWorkspaceData(auth, loc.path);
  useEffect(() => {
    if (!workspace) { setMeta(null); setMetaError(""); return; }
    let alive = true;
    setMetaError("");
    loadWorkspaceMeta(api, { isCancelled: () => !alive }).then((m) => alive && setMeta(m)).catch((e) => alive && setMetaError(workspaceLoadError(e)));
    return () => { alive = false; };
  }, [workspace, auth?.user?.id, metaAttempt]);
  useEffect(() => {
    if (!workspace) return;
    let alive = true;
    const load = () => api("/tickets/counts/").then((c) => alive && setCounts(c)).catch(() => {});
    load();
    const id = setInterval(load, 60000);
    return () => { alive = false; clearInterval(id); };
  }, [workspace, auth?.user?.id, tick]);

  // U2.5: Public CSAT shell — matched before the auth check, isolated from authenticated chrome
  const csat = csatToken(loc.path);
  if (csat) return <PublicShell><Csat token={csat} /></PublicShell>;
  if (!auth) return <Login />;
  if (!meta && metaError) {
    return (
      <div className="placeholder">
        <div className="notice bad stack" role="alert">
          <span>{metaError}</span>
          <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
            <button type="button" className="btn sm" autoFocus onClick={() => setMetaAttempt((n) => n + 1)}>Try again</button>
            <button type="button" className="btn ghost sm" onClick={logout}>Sign out</button>
          </div>
        </div>
      </div>
    );
  }
  if (!meta) return <div className="placeholder" role="status">Loading your workspace...</div>;

  const user = auth.user;
  const views = user.is_agent ? AGENT_VIEWS : CUSTOMER_VIEWS;
  const ctx = { user, meta, navigate, loc, counts, refreshCounts, views };

  let page;
  const p = loc.path;
  const ticketMatch = p.match(/^\/tickets\/(\d+)/);

  if (adminOffLimits) page = <div className="placeholder" role="status">Opening Administration…</div>;
  else if (guardNotice) page = <GuardNotice message={guardNotice} navigate={navigate} />;
  else if (p === "/new") page = <NewTicket />;
  else if (p === "/catalog" && FEATURES.catalog) page = <Catalog />;
  else if (p === "/approvals" && FEATURES.approvals) page = <Approvals />;
  else if (p === "/dashboard" && user.is_agent) page = <Dashboard />;
  else if (p.startsWith("/cmdb") && FEATURES.cmdb && user.is_agent) page = <CmdbExplorer />;
  // U7 Administration. Each page shows a forbidden state to roles the server refuses; the server checks every request.
  else if (p.startsWith("/settings") && !isAdminRole(user)) page = <GuardNotice message="Administration is only available to the super admin." navigate={navigate} />;
  else if (p === "/settings/overview") page = lazyPage(<AdminDashboard />);
  else if (p === "/settings/audit-log") page = <AuditLog />;
  else if (/^\/settings\/users(\/|$)/.test(p)) page = lazyPage(<Users />);
  else if (/^\/settings\/teams(\/|$)/.test(p)) page = lazyPage(<Teams />);
  else if (/^\/settings\/sla(\/|$)/.test(p)) page = lazyPage(<SlaConfig />);
  else if (/^\/settings\/config(\/|$)/.test(p)) page = lazyPage(<Configuration />);
  else if (p.startsWith("/kb")) page = <Knowledge id={p.split("/")[2]} />;
  else if (p === "/profile") page = <Profile />;
  else page = <Queue ticketId={ticketMatch ? Number(ticketMatch[1]) : null} />;

  const view = loc.search.get("view") || views[0].key;
  const go = (to) => (e) => { e.preventDefault(); navigate(to); };
  const section = sectionOf(p);
  const primary = primaryAction(user);
  const createDest = createTab(user);

  // Only a successful PATCH changes the toggle; a failure leaves it as it was and says so.
  const toggleAvailability = async () => {
    setAvailabilityError("");
    try {
      const u = await api("/auth/me/", { method: "PATCH", body: { is_available: !user.is_available } });
      updateUser(u);
    } catch {
      setAvailabilityError("Couldn't update your availability. Try again.");
    }
  };

  const isInternal = user.role !== "customer";

  const topFirst = landsOnDashboard(user);
  const dashboardLink = user.is_agent && !adminOnly && (
    <a href="/dashboard" onClick={go("/dashboard")} className={cx("nav-link", section === "dashboard" && "active")} title="Dashboard"><IconChart /><span className="rail-label">Dashboard</span></a>
  );
  const queuesNav = (
    <>
          <div className="nav-group rail-section-label">{user.is_agent ? "Queues" : "My requests"}</div>
          {(user.is_agent ? railViews(views) : views).map((v) => {
            const n = counts[v.key];
            return (
              <a key={v.key} href={`/tickets?view=${v.key}`} onClick={go(`/tickets?view=${v.key}`)}
                className={cx("nav-link", section === "tickets" && view === v.key && "active")}
                title={v.label}
              >
                <IconQueue />
                <span className="rail-label">{v.label}</span>
                {n !== undefined && <span className={cx("count", v.hot && n > 0 && "hot")}>{n}</span>}
              </a>
            );
          })}
    </>
  );
  const workspaceNav = (
    <>
          {!adminOnly && <div className="nav-group rail-section-label">Workspace</div>}
          {adminOnly && (
            <a href={ADMIN_HOME} onClick={go(ADMIN_HOME)} className={cx("nav-link", p === ADMIN_HOME && "active")} title="Dashboard">
              <IconChart /><span className="rail-label">Dashboard</span>
            </a>
          )}
          {adminOnly && settingsTabs(user).map((t) => (
            <a key={t.key} href={t.to} onClick={go(t.to)} className={cx("nav-link", p === t.to || p.startsWith(`${t.to}/`) ? "active" : "")} title={t.label}>
              <AdminTabIcon tab={t.key} /><span className="rail-label">{t.label}</span>
            </a>
          ))}
          {topFirst && dashboardLink}
          {!adminOnly && FEATURES.catalog && isInternal && (
            <a href="/catalog" onClick={go("/catalog")} className={cx("nav-link", section === "catalog" && "active")} title="Service catalog">
              <IconCatalog />
              <span className="rail-label">Service catalog</span>
            </a>
          )}
          {!adminOnly && FEATURES.approvals && isInternal && (
            <a href="/approvals" onClick={go("/approvals")} className={cx("nav-link", section === "approvals" && "active")} title="Approvals">
              <IconCheckCircle />
              <span className="rail-label">Approvals</span>
            </a>
          )}
          {!adminOnly && FEATURES.cmdb && user.is_agent && <a href="/cmdb" onClick={go("/cmdb")} className={cx("nav-link", section === "cmdb" && "active")} title="CMDB Explorer"><IconCatalog /><span className="rail-label">CMDB Explorer</span></a>}
          {!topFirst && dashboardLink}
          {!adminOnly && settingsHome(user) && <a href={settingsHome(user)} onClick={go(settingsHome(user))} className={cx("nav-link", section === "settings" && "active")} title="Administration"><IconBook /><span className="rail-label">Administration</span></a>}
          {!adminOnly && <a href="/kb" onClick={go("/kb")} className={cx("nav-link", section === "kb" && "active")} title="Knowledge base"><IconBook /><span className="rail-label">Knowledge base</span></a>}
    </>
  );

  return (
    <Ctx.Provider value={ctx}>
      {/* U5: one notification feed (count poll + live stream) shared by every entry point */}
      <NotificationsProvider>
      <div className="shell">
        {/* U2.3: Desktop/tablet rail — 240px full (≥1024px), 64px compact (768-1023px) */}
        <nav className="rail" aria-label="Main navigation">
          <div className="brand">
            <img src="/icon.svg" alt="" />
            {/* Hidden on compact rail (64px) via CSS; visible on full rail */}
            <div className="brand-text">OpsDesk<small>{user.tenant}</small></div>
          </div>
          <div className="rail-scroll">
          {!adminOnly && (
            <a href={primary.to} onClick={go(primary.to)} className="btn primary rail-new" title={primary.label}>
              <IconPlus />
              <span className="rail-label">{primary.label}</span>
            </a>
          )}
          {adminOnly ? workspaceNav : topFirst ? <>{workspaceNav}{queuesNav}</> : <>{queuesNav}{workspaceNav}</>}
          </div>
          <div className="rail-foot">
            {!adminOnly && (
              <div className="rail-foot-row">
                <span className="small muted rail-label">Notifications</span>
                <NotificationsBell navigate={navigate} />
              </div>
            )}
            {user.role === "employee" && (
              <label className="toggle rail-availability" title="Available for new tickets">
                <input type="checkbox" checked={user.is_available} onChange={toggleAvailability} />
                <span className="rail-label">Available for new tickets</span>
              </label>
            )}
            {availabilityError && <div className="error small rail-label" role="alert">{availabilityError}</div>}
            <a href="/profile" onClick={go("/profile")} className="me" style={{ color: "inherit" }} title="My profile">
              <span className="avatar" aria-hidden="true">{initials(user.name)}</span>
              <span className="me-info">
                <span className="me-name">{user.name}</span>
                <span className="small muted">{user.account?.name || user.role}</span>
              </span>
            </a>
            <button type="button" className="btn ghost sm rail-signout" onClick={logout} title="Sign out">
              <IconLogout />
              <span className="rail-label">Sign out</span>
            </button>
          </div>
        </nav>

        <main className="main">{page}</main>

        {/* U2.4: B1 Mobile bottom navigation — 4 fixed destinations, below 768px (UI-DEC-011) */}
        <nav className="tabbar" aria-label="Main navigation">
          {/* Tab 1: Tickets */}
          {!adminOnly && <button
            type="button"
            className={cx("tabbar-btn", section === "tickets" && "active")}
            onClick={() => { navigate(`/tickets?view=${views[0].key}`); if (section === "tickets") refreshCounts(); }}
            aria-current={section === "tickets" ? "page" : undefined}
          >
            <IconQueue />
            <span>Tickets</span>
          </button>}
          {/* Tab 2: New (/new) for customers and staff */}
          {!adminOnly && <button
            type="button"
            className={cx("tabbar-btn", isCreateSection(section) && "active")}
            onClick={() => navigate(createDest.to)}
            aria-label={createDest.name}
            aria-current={section === sectionOf(createDest.to) ? "page" : undefined}
          >
            {createDest.to === "/catalog" ? <IconCatalog /> : <IconPlus />}
            <span>{createDest.label}</span>
          </button>}
          {/* Tab 3: Knowledge */}
          {!adminOnly && <button
            type="button"
            className={cx("tabbar-btn", section === "kb" && "active")}
            onClick={() => navigate("/kb")}
            aria-label="Knowledge base"
            aria-current={section === "kb" ? "page" : undefined}
          >
            <IconBook />
            <span>Knowledge</span>
          </button>}
          {/* Tab 4: More — active on screens housed in the sheet, or while it is open */}
          <button
            type="button"
            className={cx("tabbar-btn", (moreOpen || isMoreSection(section)) && "active")}
            onClick={() => setMoreOpen(true)}
            aria-label="More navigation options"
            aria-haspopup="dialog"
            aria-expanded={moreOpen}
            aria-current={isMoreSection(section) ? "page" : undefined}
          >
            <IconMore />
            <span>More</span>
          </button>
        </nav>

        {/* U2.4: More sheet */}
        <MoreSheet
          isOpen={moreOpen}
          onClose={closeMore}
          navigate={navigate}
          user={user}
          section={section}
          onToggleAvailability={toggleAvailability}
          availabilityError={availabilityError}
        />

        <CommandPalette
          isOpen={cmdOpen}
          onClose={() => setCmdOpen(false)}
          navigate={navigate}
          views={adminOnly ? [] : views}
          user={user}
        />
      </div>
      </NotificationsProvider>
    </Ctx.Provider>
  );
}
