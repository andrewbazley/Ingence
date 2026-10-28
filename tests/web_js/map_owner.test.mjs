// An outline opened with an owner keeps its owner through everything the groups' own code does.
// Run it with:  node --test tests/web_js/map_owner.test.mjs
// (tests/test_map_outline_geometry.py runs it for the Python suite when Node is installed.)
//
// map.js is loaded into a context that holds a small fake page (elements that keep their children,
// attributes and handlers, and the helpers app.js gives it), and the real ``showMap`` and drawing
// run on invented experiments. An outline asked for through ``show()`` with ``owner: 'topics'`` must
// then still be there after "Clear outlines", after a comparison on the Map is opened and closed,
// and after a stack's "Show on the map" (a second ``show()``) replaced the groups' own outlines.
// Every name is invented.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

import { Node, fire, pageHelpers, plain, settle, walk, words } from "./fake_page.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, "..", "..", "src", "ingence", "web", "map.js"), "utf8");

/* Eight invented experiments in two lines of work, all with a place. */
const POINTS = Array.from({ length: 8 }, (_, number) => ({
  id: `exp_${number}`, label: `run_${number}`, folder: `Example_Lab/run_${number}`,
  x: 0.1 + 0.1 * number, y: 0.2 + 0.05 * (number % 4), no_place_reason: null, at_this_place: 1,
  theme: number < 4 ? 0 : 1, theme_label: null, theme_key: null, no_theme_reason: null,
  year: 2024, files: 3 + number, standing: null,
}));
const THEMES = [0, 1].map((theme) => ({
  theme, label: `line ${theme}`, person_label: null, target: `theme:${theme}`, experiments: 4, placed: 4,
  x: 0.3 + 0.4 * theme, y: 0.3, stability: 1,
}));
const MAP = {
  themes_only: false, layout_ready: true, method: "tsne", method_said: "Places.", seed: 0, count: 8, placed: 8,
  unplaced: 0, standing_computed: false, standing_said: "not read", themes: THEMES, experiments: POINTS,
};

function page() {
  const window = {};
  const root = new Node("pane");
  const { document, el, fill } = pageHelpers();
  const context = {
    window, setTimeout, clearTimeout, Promise, HTMLElement: class {}, Element: class {},
    document, el, fill,
    VIEWS: {}, SINGLE_VIEWS: new Set(), VIEW_ALIASES: {}, onLeave() {},
    listPane: root,
    turns: { claim: () => 1, owns: () => true },
    states: { loading() {}, failed() {}, say: () => new Node("card") },
    holdFocus() {}, stabilityAxis: () => new Node("axis"),
    api: async (address) => (address.includes("themes_only") ? { ...MAP, themes_only: true, experiments: [] } : MAP),
  };
  context.navigate = (view) => (view === "map" ? context.VIEWS.map() : Promise.resolve());
  vm.runInNewContext(source, context);
  return { api: window.ingenceMap, window, root, context };
}

const keys = (api) => ({ groups: plain(api.keys("groups")), topics: plain(api.keys("topics")) });
const topicEntry = { key: "topic:1", label: "a topic", tag: "", ids: ["exp_0", "exp_1", "exp_2"], owner: "topics" };
const stackEntry = {
  key: "words:aa|bb", label: "aa, bb", tag: "", folders: ["Example_Lab/run_4", "Example_Lab/run_5"], stack: ["aa", "bb"],
};

test("the legend writes nothing for outlines where no group is outlined, not even the word null", async () => {
  const { api, root } = page();
  await api.show({ fit: false, outlines: [] });
  assert.ok(!words(root).includes("null"), "a missing part was written as text");
  await api.show({ replace: false, fit: false, outlines: [topicEntry] });
  assert.ok(!words(root).includes("null"), "a topic alone leaves the legend as it was");
  assert.ok(!words(root).includes("Outlines"), "the groups' legend line stays out for a topic");
});

test("an outline opened through show() with an owner is held by that owner", async () => {
  const { api } = page();
  await api.show({ replace: false, fit: false, outlines: [topicEntry] });
  assert.deepEqual(keys(api), { groups: [], topics: ["topic:1"] });
  // Without an owner an entry is the groups' own.
  await api.show({ fit: false, outlines: [stackEntry] });
  assert.deepEqual(keys(api), { groups: ["words:aa|bb"], topics: ["topic:1"] });
});

test("a stack's Show on the map, a second show(), leaves the topic and replaces the groups' own", async () => {
  const { api } = page();
  await api.show({ replace: false, fit: false, outlines: [topicEntry] });
  await api.show({ fit: true, outlines: [stackEntry] });
  await api.show({ fit: true, outlines: [{ ...stackEntry, key: "words:cc|dd" }] });
  assert.deepEqual(keys(api), { groups: ["words:cc|dd"], topics: ["topic:1"] });
});

test("Clear outlines clears the groups' own and leaves the topic, which the card and legend do not list", async () => {
  const { api, root } = page();
  await api.show({ replace: false, fit: false, outlines: [topicEntry] });
  await api.show({ fit: false, outlines: [stackEntry] });
  const everything = walk(root);
  const clear = everything.find((node) => node.textContent === "Clear outlines");
  assert.ok(clear, "the legend has a Clear outlines button while a group is outlined");
  const listed = (nodes) => nodes.filter((node) => node.attrs && node.attrs["data-outline"]).map((node) => node.attrs["data-outline"]);
  assert.deepEqual(listed(everything), ["words:aa|bb"], "the legend lists the group and not the topic");
  fire(clear, "click");
  assert.deepEqual(keys(api), { groups: [], topics: ["topic:1"] });
  // With the groups' outlines gone the legend line and the card list nothing, topic or not.
  const after = walk(root);
  assert.equal(after.find((node) => node.textContent === "Clear outlines"), undefined);
  assert.equal(after.filter((node) => node.attrs && node.attrs["data-help"] === "map.groups").length, 0);
});

test("a comparison on the Map, opened and closed, leaves the topic alone", async () => {
  const { api, window, root } = page();
  let closeComparison = null;
  window.ingenceGroups = {
    fillHowTo() {},
    drawCompare() {},
    async openCompare(holder, specs, options) {
      closeComparison = options.onClose;
      options.onData({ groups: specs.map((spec, order) => ({
        key: spec, kind: "line", tag: "AB"[order], label: `line ${spec.slice(5)}`, words: [],
        experiments: POINTS.filter((point) => `line:${point.theme}` === spec).map((point) => ({ id: point.id })),
      })) });
    },
  };
  await api.show({ replace: false, fit: false, outlines: [topicEntry] });
  const tree = walk(root);
  const boxes = tree.filter((node) => node.tag === "input" && node.attrs.type === "checkbox");
  assert.equal(boxes.length, 2, "one tick for each line of work");
  for (const box of boxes) {
    box.checked = true;
    fire(box, "change");
  }
  const go = tree.find((node) => node.tag === "button" && node.textContent.startsWith("Compare 2"));
  assert.ok(go, "two lines ticked: the button says how many are compared");
  fire(go, "click");
  await settle();
  assert.deepEqual(keys(api), { groups: ["line:0", "line:1"], topics: ["topic:1"] });
  assert.equal(typeof closeComparison, "function");
  closeComparison();
  assert.deepEqual(keys(api), { groups: [], topics: ["topic:1"] });
});
