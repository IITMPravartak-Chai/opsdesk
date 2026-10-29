// Post-U8 (U8-03): the workspace metadata request must never leave the user on an endless "Loading your workspace..."
// placeholder. Each attempt is aborted after WORKSPACE_TIMEOUT_MS; a timeout is retried automatically at most
// WORKSPACE_AUTO_RETRIES times (bounded: no retry storm), then reported as an error with a manual "Try again".
// A request that fails (HTTP error, network error, expired session) is reported at once and not retried here:
// authentication and permission outcomes are never masked. Pure and dependency-injected so it can be unit tested.
export const WORKSPACE_TIMEOUT_MS = 15000;
export const WORKSPACE_AUTO_RETRIES = 1;

/** Error raised after the final attempt timed out; `timedOut` selects the "taking longer than expected" message. */
export class WorkspaceTimeout extends Error {
  constructor() { super("Workspace request timed out"); this.timedOut = true; }
}

/**
 * @param apiFn      the API client (called as apiFn("/meta/", { signal }))
 * @param opts       { timeoutMs, autoRetries, isCancelled } (isCancelled() true after unmount: stop retrying)
 */
export async function loadWorkspaceMeta(apiFn, { timeoutMs = WORKSPACE_TIMEOUT_MS, autoRetries = WORKSPACE_AUTO_RETRIES, isCancelled = () => false } = {}) {
  for (let attempt = 0; attempt <= autoRetries; attempt += 1) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      return await apiFn("/meta/", { signal: ctl.signal });
    } catch (e) {
      if (!ctl.signal.aborted || isCancelled()) throw e; // a real failure (or the view went away): report it as it is
    } finally {
      clearTimeout(timer);
    }
  }
  throw new WorkspaceTimeout();
}
