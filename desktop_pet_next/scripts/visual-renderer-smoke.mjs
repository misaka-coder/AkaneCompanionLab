import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { createVisualRenderer } from "../src/visual-renderer.js";
import { observeBubbleLayout } from "../src/bubble-layout.js";

const loads = [];
globalThis.Image = class {
  naturalWidth = 100;
  naturalHeight = 200;
  set src(value) { this.url = value; loads.push(this); }
  decode() { return Promise.resolve(); }
};
globalThis.window = { queueMicrotask };
const stage = { dataset: {}, style: { setProperty() {}, removeProperty() {} } };
const image = {
  dataset: {}, style: {}, naturalWidth: 100,
  getAttribute() { return ""; }, addEventListener() {}, removeEventListener() {},
  removeAttribute(name) { if (name === "src") delete this.src; }
};
const errors = [];
const renderer = createVisualRenderer({ stage, image, onImageLoadError: (error) => errors.push(error) });
const normal = { id: "normal", url: "/one/normal.png" };
const happy = { id: "happy", url: "/one/happy.png" };
const tick = () => new Promise((resolve) => setImmediate(resolve));
renderer.setExpression(normal);
loads.at(-1).onload();
await tick();
assert.equal(image.src, normal.url);
renderer.setExpression(happy);
const staleHappy = loads.at(-1);
renderer.setExpression(normal);
staleHappy.onload();
await tick();
assert.equal(image.src, normal.url, "returning to the visible expression cancels the pending image");
assert.equal(stage.dataset.expressionPending, undefined);

const secondNormal = { id: "normal", url: "/two/normal.png" };
renderer.setExpression(secondNormal);
const secondLoad = loads.at(-1);
const count = loads.length;
renderer.setExpression(secondNormal);
assert.equal(loads.length, count, "repeat requests reuse the pending decode");
secondLoad.onload();
await tick();
assert.equal(image.src, secondNormal.url, "same expression id from another outfit replaces the image");

renderer.setExpression(happy);
loads.at(-1).onerror();
await tick();
assert.equal(image.src, secondNormal.url);
assert.equal(image.dataset.imageState, "ready", "a failure preserves the last good portrait");
assert.equal(errors.length, 1);
renderer.setExpression(happy);
assert.equal(stage.dataset.expressionPending, "happy", "failed loads can retry");

renderer.setLayout({ portrait: { scale: 0.8, offset_x: 12, offset_y: -20 } });
const transform = image.style.transform;
for (const motion of ["idle", "speaking", "click", "dragging", "land", "thinking"]) {
  renderer.setMotion(motion);
  assert.equal(image.style.transform, transform);
}
renderer.setLayout(null);
assert.equal(image.style.transform, "");
const clearedLoad = loads.at(-1);
renderer.clearExpression();
clearedLoad.onload();
await tick();
assert.equal(image.src, undefined, "clearing a preview invalidates its pending image");
assert.equal(image.dataset.imageState, "empty");
renderer.setExpression(normal);
const disposedLoad = loads.at(-1);
renderer.dispose();
disposedLoad.onload();
await tick();
assert.equal(image.src, undefined, "disposed preview cannot receive a late decode");
console.log("visual-renderer smoke passed: stale loads, outfit identity, failure/retry, layout reset");

const properties = new Map();
let observe, disconnected = false, syncs = 0;
globalThis.ResizeObserver = class {
  constructor(callback) { observe = callback; }
  observe() {}
  disconnect() { disconnected = true; }
};
const bubble = new EventTarget();
Object.assign(bubble, { offsetWidth: 240, offsetHeight: 90 });
const disconnect = observeBubbleLayout({
  stage: { style: { setProperty: (key, value) => properties.set(key, value) } },
  bubble, onResize: () => syncs++
});
assert.equal(properties.get("--bubble-half-width"), "120px");
bubble.offsetWidth = 300;
observe();
assert.equal(properties.get("--bubble-half-width"), "150px");
const event = new Event("transitionend");
event.propertyName = "transform";
bubble.dispatchEvent(event);
assert.equal(syncs, 3, "native hit bounds refresh after the entrance transition");
disconnect();
assert.equal(disconnected, true);
bubble.dispatchEvent(event);
assert.equal(syncs, 3, "cleanup removes the transition listener");

// Execute the actual main-window collector: an interactive bubble must also
// exist in the native hit region list, and disappear from it when hidden.
const source = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
const collector = source.slice(source.indexOf("function collectHitRegions()"), source.indexOf("function buildPetHitRegion()"));
let visible = true;
const context = vm.createContext({
  els: { bubble: { classList: { contains: () => visible } }, chatForm: { hidden: true }, menu: { hidden: true } },
  buildPetHitRegion: () => ({ kind: "pet" }),
  addElementHitRegion: (regions, el, kind) => { if (el) regions.push({ kind }); }
});
vm.runInContext(collector, context);
assert.equal(vm.runInContext('collectHitRegions().some(r => r.kind === "speech-bubble")', context), true);
visible = false;
assert.equal(vm.runInContext('collectHitRegions().some(r => r.kind === "speech-bubble")', context), false);
console.log("bubble layout smoke passed: resize, transition, cleanup, actual native hit-region collector");
