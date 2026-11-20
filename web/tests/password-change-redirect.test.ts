import assert from "node:assert/strict";
import test from "node:test";

import {
  PASSWORD_CHANGE_HEADER,
  needsPasswordChangeRedirect,
} from "../shared/api/client";

// Backend M7: an account still holding an administrator-assigned password gets
// 403 + this header on everything but the change-password surface. The client
// sends it to /profile, and never from /profile itself (no redirect loop).

function gated(status = 403, flagged = true): Response {
  const headers = new Headers();
  if (flagged) headers.set(PASSWORD_CHANGE_HEADER, "required");
  return new Response(null, { status, headers });
}

test("a flagged 403 leaves any page for the password form", () => {
  assert.equal(needsPasswordChangeRedirect(gated(), "/learning/books"), true);
  assert.equal(needsPasswordChangeRedirect(gated(), "/"), true);
});

test("on the password form itself the response is handled in place", () => {
  assert.equal(needsPasswordChangeRedirect(gated(), "/profile"), false);
  assert.equal(needsPasswordChangeRedirect(gated(), "/profile/"), false);
});

test("an ordinary 403 or another status does not redirect", () => {
  assert.equal(needsPasswordChangeRedirect(gated(403, false), "/space"), false);
  assert.equal(needsPasswordChangeRedirect(gated(401), "/space"), false);
  assert.equal(needsPasswordChangeRedirect(gated(200), "/space"), false);
});
