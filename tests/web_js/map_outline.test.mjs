// The Map's outline geometry, run with Node's own test runner against map.js in a stubbed page.
// Run it with:  node --test tests/web_js/map_outline.test.mjs
// (tests/test_map_outline_geometry.py runs it for the Python suite when Node is installed.)
//
// map.js is an IIFE written for the Dashboard. Here it is loaded into a context that holds only what
// the file reads when it loads (the page's helpers as inert stand-ins), and the pure functions the
// file exposes at ``window.ingenceMap.geometry`` are tested. Every point is invented.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, "..", "..", "src", "ingence", "web", "map.js"), "utf8");

function loaded() {
  const window = {};
  const context = {
    window,
    document: {
      createElement() {
        return { set innerHTML(_) {}, content: { firstElementChild: { namespaceURI: "svg-namespace" } } };
      },
    },
    VIEWS: {},
    SINGLE_VIEWS: new Set(),
    VIEW_ALIASES: {},
    onLeave() {},
  };
  vm.runInNewContext(source, context);
  return { api: window.ingenceMap, window, context };
}

const { api } = loaded();
const { convexHull: hullOf, paddedOutline, overlapOf, areaOf } = api.geometry;

// Arrays made inside the page's context have that context's prototype; compare them as plain data.
const plain = (value) => JSON.parse(JSON.stringify(value));
const convexHull = (points) => plain(hullOf(points));

const square = (x, y, side) => [[x, y], [x + side, y], [x + side, y + side], [x, y + side]];

test("the page's script registers the outline functions", () => {
  assert.equal(typeof api.outline, "function");
  assert.equal(typeof api.clear, "function");
  assert.equal(typeof api.show, "function");
  assert.equal(typeof api.onDraw, "function");
  assert.equal(api.outlineColor(0), "var(--map-o0)");
  assert.equal(api.outlineColor(5), "var(--map-o1)", "colors repeat after four");
});

test("the hull of a cloud keeps its corners and drops what lies inside", () => {
  const cloud = [...square(0, 0, 10), [5, 5], [2, 7], [9, 1]];
  const hull = convexHull(cloud);
  assert.equal(hull.length, 4);
  assert.equal(areaOf(hull), 100);
});

test("one point is its own hull, a repeated point counts once, points on a line are its two ends", () => {
  assert.deepEqual(convexHull([[3, 4]]), [[3, 4]]);
  assert.deepEqual(convexHull([[3, 4], [3, 4], [3, 4]]), [[3, 4]]);
  const line = convexHull([[0, 0], [1, 1], [2, 2], [3, 3]]);
  assert.deepEqual(line, [[0, 0], [3, 3]]);
  assert.deepEqual(convexHull([]), []);
});

test("the padded outline of one point is a circle, of two a capsule, and holds every point", () => {
  const circle = paddedOutline([[50, 50]], 10, 32);
  assert.ok(Math.abs(areaOf(circle) - Math.PI * 100) < 8, `area ${areaOf(circle)}`);
  const capsule = paddedOutline([[0, 0], [40, 0]], 10, 32);
  assert.ok(areaOf(capsule) > 40 * 20, "longer than a circle");
  assert.deepEqual(plain(paddedOutline([])), []);
  const cloud = [[0, 0], [30, 5], [12, 40], [25, 22]];
  const around = paddedOutline(cloud, 8);
  // Every dot is inside the outline: it is on the inner side of each edge.
  for (const [x, y] of cloud) {
    for (let at = 0; at < around.length; at += 1) {
      const a = around[at];
      const b = around[(at + 1) % around.length];
      assert.ok((b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0]) >= -1e-9);
    }
  }
});

test("the overlap of two outlines is the part they share, and empty where they do not meet", () => {
  const first = convexHull(square(0, 0, 10));
  const second = convexHull(square(5, 5, 10));
  const shared = overlapOf(first, second);
  assert.equal(areaOf(shared), 25);
  assert.equal(areaOf(overlapOf(second, first)), 25, "the same from either side");
  assert.deepEqual(plain(overlapOf(first, convexHull(square(20, 20, 4)))), []);
  assert.equal(areaOf(overlapOf(first, first)), 100);
  // One inside the other: the overlap is the smaller.
  assert.equal(areaOf(overlapOf(first, convexHull(square(2, 2, 3)))), 9);
});

test("outlines asked for before the map is drawn are held, and clearing one leaves the others", () => {
  const { api: fresh } = loaded();
  assert.equal(fresh.outline(["a", "b"], "red", { key: "one", label: "first" }), "one");
  assert.equal(fresh.outline(["b", "c"], "blue", { key: "two", label: "second" }), "two");
  assert.deepEqual(plain(fresh.keys()), ["one", "two"]);
  fresh.clear("one");
  assert.deepEqual(plain(fresh.keys()), ["two"]);
  fresh.clear();
  assert.deepEqual(plain(fresh.keys()), []);
});

test("an outline belongs to its owner: the groups' own are cleared by clear() and no other owner's", () => {
  const { api: fresh } = loaded();
  fresh.outline(["a"], "red", { key: "words:x" });
  fresh.outline(["b"], "blue", { key: "topic:1", owner: "topics", tag: "" });
  fresh.outline(["c"], "green", { key: "topic:2", owner: "topics", tag: "" });
  assert.deepEqual(plain(fresh.keys("groups")), ["words:x"]);
  assert.deepEqual(plain(fresh.keys("topics")), ["topic:1", "topic:2"]);
  // Clearing with no key clears the groups' own and leaves every topic.
  fresh.clear();
  assert.deepEqual(plain(fresh.keys()), ["topic:1", "topic:2"]);
  // A topic is removed by its own key, or all of the owner's at once.
  fresh.clear("topic:1");
  assert.deepEqual(plain(fresh.keys()), ["topic:2"]);
  fresh.outline(["d"], "red", { key: "line:3" });
  fresh.clear(undefined, { owner: "topics" });
  assert.deepEqual(plain(fresh.keys()), ["line:3"]);
});

test("a key another owner holds is never taken over", () => {
  const { api: fresh } = loaded();
  fresh.outline(["a"], "blue", { key: "shared", owner: "topics", tag: "" });
  const made = fresh.outline(["b"], "red", { key: "shared" });
  assert.notEqual(made, "shared");
  assert.deepEqual(plain(fresh.keys("topics")), ["shared"]);
  assert.deepEqual(plain(fresh.keys("groups")), [made]);
});

test("a key made for an outline is never one another outline holds, even after a removal", () => {
  const { api: fresh } = loaded();
  const first = fresh.outline(["a"], "red");
  const second = fresh.outline(["b"], "red");
  fresh.clear(first);
  const third = fresh.outline(["c"], "red");
  assert.equal(new Set([first, second, third]).size, 3);
  assert.deepEqual(plain(fresh.keys()).sort(), [second, third].sort());
});

test("hearing each drawing can be stopped", () => {
  const { api: fresh } = loaded();
  const stop = fresh.onDraw(() => {});
  assert.equal(typeof stop, "function");
  stop();
  stop();
});
