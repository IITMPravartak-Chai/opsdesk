// The team form: which fields to send when saving, the save itself, and the button label. Pure functions (the save
// takes the api client as an argument) so `node --test` covers them.
import { ifMatch } from "./adminHelpers.js";

export const emptyTeamForm = () => ({ name: "", email: "", assignment_strategy: "manual", team_lead_id: null, manager_id: null });

/** Form values for a team the server returned. */
export const teamToForm = (team) => ({
  name: team.name,
  email: team.email || "",
  assignment_strategy: team.assignment_strategy,
  team_lead_id: team.team_lead?.id ?? null,
  manager_id: team.manager?.id ?? null,
});

/**
 * Only the fields that differ from the saved team, so an untouched form sends nothing and a save never overwrites what
 * someone else changed. An empty object means there is nothing to save.
 */
export function teamPatch(form, saved) {
  const patch = {};
  const name = form.name.trim();
  if (name !== saved.name) patch.name = name;
  if (form.email !== (saved.email || "")) patch.email = form.email;
  if (form.assignment_strategy !== saved.assignment_strategy) patch.assignment_strategy = form.assignment_strategy;
  if (form.team_lead_id !== (saved.team_lead?.id ?? null)) patch.team_lead_id = form.team_lead_id;
  if (form.manager_id !== (saved.manager?.id ?? null)) patch.manager_id = form.manager_id;
  return patch;
}

/** Text on the save button. */
export function saveLabel({ busy, editing }) {
  if (busy) return "Saving…";
  return editing ? "Save changes" : "Create team";
}

/**
 * Create the team, or send only what changed on an existing one together with its version, so a concurrent edit is
 * refused instead of overwritten. With nothing changed there is nothing to send and the saved team is returned as is.
 */
export async function saveTeam(api, { team, form, saved }) {
  if (!team) return api("/admin/teams/", { method: "POST", body: { ...form, name: form.name.trim() } });
  const body = teamPatch(form, saved);
  if (Object.keys(body).length === 0) return saved;
  return api(`/admin/teams/${team.id}/`, { method: "PATCH", body, headers: ifMatch(saved.version) });
}
