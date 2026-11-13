// The satisfaction survey page: the text on its button. Pure, so `node --test` covers it.

/** Text on the survey button: sending, a first rating, or a change to a rating already given. */
export function ratingButtonLabel({ busy, sent }) {
  if (busy) return "Sending…";
  return sent ? "Update rating" : "Send rating";
}

/** Says the rating is saved while the stars on screen are the ones the server holds; "" once they are changed. */
export const savedNote = (info, score) => (info?.score && info.score === score ? " Your rating is saved." : "");
