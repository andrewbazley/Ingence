// The Groupings page draws a word as its files wrote it from its first drawing.
// Run it with:  node --test tests/web_js/groups_words.test.mjs
// (tests/test_map_outline_geometry.py runs it for the Python suite when Node is installed.)
//
// groups.js is loaded into a context that holds a small fake page, and the real drawing runs on an
// invented lattice whose words are Dictionary terms (``terms``) with the spelling the files wrote
// beside each (``terms_shown``). The stacks, the line over them and the sets listed apart must show
// the spelling, never the term, and a later drawing must show its own spellings at once, not the
// last drawing's. Every word is invented.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

import { Node, pageHelpers, settle, words } from "./fake_page.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, "..", "..", "src", "ingence", "web", "groups.js"), "utf8");

const labels = (count) => Array.from({ length: count }, (_, number) => `Example_Lab/run_${number}`);

/* A stack of two sets (a base and one block adding a word) and one set listed apart, in terms. */
function lattice(spelling) {
  const set = (terms, shown, extra) => ({
    terms, terms_shown: shown, base: [], added: terms, parents: [], experiments: labels(5),
    state: "asked", target: `group:${terms.join(" ")}`, question: null, decided_by: null,
    decided_at: null, answered_as: null, read_without: [], read_for: [], answered_by: null,
    answered_yes: null, ...extra,
  });
  return {
    mode: "lattice", resolving_rules_in_run: true, minimum_core_terms: 2, minimum_experiments: 5,
    maximum_concepts: 5000, bound_reached: false, experiments: 7,
    sets: [
      set(["dror", "quen_stain"], ["dror", spelling], {}),
      set(["dror", "mond", "quen_stain"], ["dror", "mond", spelling], {
        base: ["dror", "quen_stain"], added: ["mond"], parents: [["dror", "quen_stain"]],
        experiments: labels(3), state: "waiting",
      }),
      set(["trel", "zath_stain"], ["trel", spelling === "quen" ? "zath" : "zathal"], {
        experiments: labels(2), state: "not_asked",
      }),
    ],
  };
}

function page(first) {
  const window = {};
  const { document, el, fill } = pageHelpers();
  const answers = [first];
  const context = {
    window, document, el, fill, Promise, setTimeout, clearTimeout,
    api: async () => answers.at(-1),
    write: async () => ({}),
    askActor: async () => true,
    me: { actor: "dana" },
    navigate() {},
    matchMedia: () => ({ matches: false }),
  };
  vm.runInNewContext(source, context);
  return { window, answers };
}

const drawn = async ({ window }, holder = new Node("holder")) => {
  window.groupLattice(holder);
  await settle();
  return holder;
};

test("the first drawing shows the words as the files wrote them, never the terms", async () => {
  const shown = await drawn(page(lattice("quen")));
  const text = words(shown);
  assert.ok(text.includes("quen"), "the stack's words are the spelling");
  assert.ok(text.includes("+ mond"), "an addition is still drawn as an addition");
  assert.ok(
    text.some((line) => line.endsWith("share dror, quen")),
    "the line over the stack says the spelling",
  );
  assert.ok(text.includes("dror quen") || text.some((line) => line.includes("trel zath")));
  assert.ok(
    !text.some((line) => line.includes("quen_stain") || line.includes("zath_stain")),
    "no term the files never wrote is drawn",
  );
});

test("a later drawing shows its own spellings at once, not the last drawing's", async () => {
  const world = page(lattice("quen"));
  const holder = await drawn(world);
  assert.ok(words(holder).includes("quen"));
  world.answers.push(lattice("quenal"));
  const again = await drawn(world, new Node("holder"));
  const text = words(again);
  assert.ok(text.includes("quenal"), "the second lattice's spelling is drawn");
  assert.ok(!text.includes("quen"), "the first lattice's spelling is gone");
  assert.ok(!text.some((line) => line.includes("quen_stain")));
});

test("a word the files wrote as the term itself is drawn as it is", async () => {
  const same = lattice("quen_stain");
  const text = words(await drawn(page(same)));
  assert.ok(text.includes("quen_stain"));
});
