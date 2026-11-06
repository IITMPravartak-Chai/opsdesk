// Two-step sign-in on the Profile page: turn a failed request into a message the person can act on.
// Nothing here ever reports success; only a confirmed server response does that.

export function mfaErrorMessage(err, phase) {
  const status = err?.status;
  if (status === 401) return "Your session has ended. Sign in again, then turn on two-step sign-in.";
  if (status === 403) return "Two-step sign-in isn't available for your account. Contact your administrator.";
  if (status === 409) return "Two-step sign-in is already on for your account.";
  if (status === 400 && phase === "confirm") return "That code didn't match. Enter the newest 6-digit code from your authenticator app.";
  if (status === 429) return "Too many attempts. Wait a minute, then try again.";
  if (typeof status === "number" && status >= 500) {
    return phase === "confirm"
      ? "The server couldn't confirm the code. Two-step sign-in is still off; try again."
      : "The server couldn't start setup. Two-step sign-in is still off; try again.";
  }
  return phase === "confirm"
    ? "Couldn't confirm the code. Two-step sign-in is still off; try again."
    : "Couldn't start setup. Check your connection and try again.";
}

export const isSixDigitCode = (code) => /^\d{6}$/.test(String(code || "").trim());
