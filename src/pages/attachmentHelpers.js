// Attachment checks done in the browser before anything is sent, so an oversized or blocked file is refused
// with a message instead of failing silently after the ticket or reply was already saved.
// Framework-free so `node --test` covers it. The server enforces the same limits (services/attachments.py,
// api/multipart_helper.py, nginx client_max_body_size); GET /api/meta/ reports them as `attachment_limits`.

const MB = 1024 * 1024;

/** Used when the server's meta response carries no limits (an older API). */
export const DEFAULT_LIMITS = { max_file_mb: 20, max_request_mb: 25, blocked_extensions: [] };

export const limitsFrom = (meta) => ({ ...DEFAULT_LIMITS, ...meta?.attachment_limits });

export function formatSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < MB) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / MB).toFixed(bytes < 10 * MB ? 1 : 0)} MB`;
}

const extension = (name) => {
  const i = (name || "").lastIndexOf(".");
  return i > 0 ? name.slice(i).toLowerCase() : "";
};

/** One message per problem; an empty list means the files can be sent. */
export function fileProblems(files, limits = DEFAULT_LIMITS) {
  const list = [...(files || [])];
  const problems = [];
  const blocked = new Set((limits.blocked_extensions || []).map((x) => x.toLowerCase()));
  for (const f of list) {
    if (blocked.has(extension(f.name))) problems.push(`${f.name}: files of type ${extension(f.name)} are not allowed.`);
    else if (f.size > limits.max_file_mb * MB) {
      problems.push(`${f.name} is ${formatSize(f.size)}. The largest file you can attach is ${limits.max_file_mb} MB.`);
    }
  }
  const total = list.reduce((sum, f) => sum + (f.size || 0), 0);
  if (!problems.length && list.length > 1 && total > limits.max_request_mb * MB) {
    problems.push(`These files are ${formatSize(total)} together. You can attach up to ${limits.max_request_mb} MB at a time.`);
  }
  return problems;
}

/** Message for an upload the server refused. 413 comes from the proxy as an HTML page, so it is never shown raw. */
export function uploadErrorMessage(err, limits = DEFAULT_LIMITS) {
  if (err?.status === 413) return `The files are too large to upload. The largest file you can attach is ${limits.max_file_mb} MB.`;
  const detail = err?.message || "";
  if (!detail || /<\/?[a-z][^>]*>/i.test(detail)) return "The files could not be uploaded.";
  return detail;
}

/**
 * Take the files just chosen in a file input: remember them, say what is wrong with them (nothing, if they are fine)
 * and clear the input so the same file can be chosen again after it was removed.
 */
export function pickFiles(input, { setFiles, setError, limits }) {
  const picked = [...input.files];
  setFiles(picked);
  setError(fileProblems(picked, limits).join(" "));
  input.value = "";
}
