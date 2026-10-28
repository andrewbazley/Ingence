// The key a line of work is held under in a topic, made by the page as the server makes it.
// Run it with:  node --test tests/web_js/topic_line_key.test.mjs
// (tests/test_topic_views_source.py runs it for the Python suite when Node is installed.)
//
// topic_kit.js is loaded into an empty context (it keeps its pieces in an IIFE and puts them on
// ``window.topicKit``), and ``lineKey`` is asked for invented experiments. The expected values are
// the server's own, from ``api/routes/overlay.line_key`` (a Python test holds the same two): the
// first twelve hex digits of the SHA-256 of the experiments, each once, sorted, joined by U+001F.
// Every name is invented.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, "..", "..", "src", "ingence", "web", "topic_kit.js"), "utf8");

function kit(crypto) {
  const context = { window: {}, crypto, TextEncoder, globalThis: undefined };
  context.globalThis = context;
  vm.runInNewContext(source, context);
  return context.window.topicKit;
}

test("a line's key is the server's, whatever order and however many times its experiments are named", async () => {
  const held = kit(globalThis.crypto);
  assert.equal(await held.lineKey(["entry_a", "entry_b"]), "line:456065eaa676");
  assert.equal(await held.lineKey(["entry_b", "entry_a"]), "line:456065eaa676");
  assert.equal(await held.lineKey(["entry_b", "entry_a", "entry_b"]), "line:456065eaa676");
  assert.equal(await held.lineKey(["entry_a"]), "line:feca9834a05a");
});

test("where the browser offers no digest, there is no key and the line is counted as new", async () => {
  assert.equal(await kit(undefined).lineKey(["entry_a"]), null);
  assert.equal(await kit({}).lineKey(["entry_a"]), null);
});
