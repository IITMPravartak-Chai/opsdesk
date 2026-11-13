// Two-step sign-in on the Profile page: turn a failed request into a message the person can act on.
// Nothing here ever reports success; only a confirmed server response does that.

// `turnOn` is the change being made; `phase` is "start" (email me a code) or "confirm" (check the code).
export function mfaErrorMessage(err, phase, turnOn = true) {
  const status = err?.status;
  const still = `Two-step sign-in is still ${turnOn ? "off" : "on"}`;
  const change = `turn ${turnOn ? "on" : "off"} two-step sign-in`;
  if (status === 401) return `Your session has ended. Sign in again, then ${change}.`;
  if (status === 403) return "Two-step sign-in isn't available for your account. Contact your administrator.";
  if (status === 409) return `Two-step sign-in is already ${turnOn ? "on" : "off"} for your account.`;
  if (status === 400 && phase === "confirm") return "That code didn't match or has expired. Enter the newest 6-digit code from your email.";
  if (status === 429) return "Too many attempts. Wait a minute, then try again.";
  if (typeof status === "number" && status >= 500) {
    return phase === "confirm"
      ? `The server couldn't confirm the code. ${still}; try again.`
      : `The server couldn't email a code. ${still}; try again.`;
  }
  return phase === "confirm"
    ? `Couldn't confirm the code. ${still}; try again.`
    : "Couldn't send the code. Check your connection and try again.";
}

/** Text on the button that starts turning two-step sign-in on or off. */
export function twoStepButtonLabel({ busy, turnOn }) {
  if (busy === "start") return "Sending code…";
  return turnOn ? "Turn on two-step sign-in" : "Turn off two-step sign-in";
}

export const isSixDigitCode = (code) => /^\d{6}$/.test(String(code || "").trim());
