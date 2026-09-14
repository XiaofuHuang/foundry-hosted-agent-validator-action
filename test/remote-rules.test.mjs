import assert from "node:assert/strict";
import test from "node:test";

import {
  isPublicIPv4,
  parseRemoteRulesUrl,
} from "../scripts/remote-rules.mjs";

test("classifies public and non-public IPv4 addresses", () => {
  assert.equal(isPublicIPv4("8.8.8.8"), true);
  assert.equal(isPublicIPv4("10.0.0.1"), false);
  assert.equal(isPublicIPv4("127.0.0.1"), false);
  assert.equal(isPublicIPv4("169.254.169.254"), false);
  assert.equal(isPublicIPv4("203.0.113.1"), false);
  assert.equal(isPublicIPv4("::1"), false);
});

test("requires HTTPS URLs without embedded credentials", () => {
  assert.equal(
    parseRemoteRulesUrl("https://example.com/rules.yaml").hostname,
    "example.com",
  );
  assert.throws(
    () => parseRemoteRulesUrl("http://example.com/rules.yaml"),
    /must use HTTPS/,
  );
  assert.throws(
    () => parseRemoteRulesUrl("https://user:secret@example.com/rules.yaml"),
    /must not contain credentials/,
  );
});
