import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import * as facadeExports from "../src/control-center-v2/view-model.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/control-center-v2");
const facade = path.join(root, "view-model.js");
const namespace = path.join(root, "view-model");
const graph = new Map();
const inside = file => file.startsWith(namespace + path.sep);
const projectionContracts = new Set([
  path.join(root, "model-service.js"), path.join(root, "qq-setup.js"),
  path.resolve(root, "../screen-observation.js"),
  path.resolve(root, "../control-center/settings-command-contract.js")
]);
function visit(file) {
  if (graph.has(file)) return;
  const tree = ts.createSourceFile(file, fs.readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  assert.equal(tree.parseDiagnostics.length, 0, `invalid module: ${path.relative(root, file)}`);
  const dependencies = new Set();
  graph.set(file, dependencies);
  for (const node of tree.statements) {
    if ((!ts.isImportDeclaration(node) && !ts.isExportDeclaration(node)) || !node.moduleSpecifier) continue;
    const module = node.moduleSpecifier.text;
    assert.ok(module.startsWith("."), `view models must not import host or framework services: ${module}`);
    const dependency = path.resolve(path.dirname(file), module);
    if (inside(file)) assert.notEqual(dependency, facade, "implementation must not import the facade");
    if (inside(file) && !inside(dependency)) {
      assert.ok(projectionContracts.has(dependency), `projection must not import a UI or transport owner: ${module}`);
    }
    if (inside(dependency)) { dependencies.add(dependency); visit(dependency); }
  }
}
visit(facade);
const active = new Set(), done = new Set();
function check(file) {
  assert.ok(!active.has(file), `dependency cycle: ${path.relative(root, file)}`);
  if (done.has(file)) return;
  active.add(file);
  for (const dependency of graph.get(file)) check(dependency);
  active.delete(file);
  done.add(file);
}
check(facade);
for (const name of fs.readdirSync(namespace).filter(name => name.endsWith(".js"))) {
  assert.ok(graph.has(path.join(namespace, name)), `orphan implementation: ${name}`);
}
const program = ts.createProgram([...graph.keys()], {
  allowJs: true, checkJs: true, noEmit: true, noResolve: true,
  target: ts.ScriptTarget.Latest, module: ts.ModuleKind.ESNext
});
for (const file of graph.keys()) {
  const unbound = program.getSemanticDiagnostics(program.getSourceFile(file))
    .filter(item => item.code === 2304 || item.code === 2552);
  assert.deepEqual(unbound.map(item => ts.flattenDiagnosticMessageText(item.messageText, " ")), [],
    `unbound context in ${path.relative(root, file)}`);
}
assert.deepEqual(Object.keys(facadeExports).sort(), [
  "createControlCenterViewModel", "isObservedActionConfirmation", "normalizeActionPresentation", "observedActionOutcome"
]);
console.log(`control-center view-model modules passed: ${graph.size - 1} implementations, acyclic dependencies, bound identifiers, 4 stable exports`);
