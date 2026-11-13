import test from "node:test";
import assert from "node:assert/strict";
import { ratingButtonLabel, savedNote } from "./csatHelpers.js";

test("the survey button says send, then update once a rating was given", () => {
  assert.equal(ratingButtonLabel({ busy: true, sent: false }), "Sending…");
  assert.equal(ratingButtonLabel({ busy: true, sent: true }), "Sending…");
  assert.equal(ratingButtonLabel({ busy: false, sent: false }), "Send rating");
  assert.equal(ratingButtonLabel({ busy: false, sent: true }), "Update rating");
});

test("'saved' is shown only while the stars on screen are the ones the server holds", () => {
  assert.equal(savedNote({ score: 4 }, 4), " Your rating is saved.");
  assert.equal(savedNote({ score: 4 }, 2), "", "the person picked different stars and has not sent them yet");
  assert.equal(savedNote({ score: null }, 0), "", "nothing was rated yet");
  assert.equal(savedNote(null, 3), "");
});
