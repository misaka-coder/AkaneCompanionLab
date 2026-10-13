import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import * as source from "../src/control-center/data-sources.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/control-center");
const facade = path.join(root, "data-sources.js");
const namespace = path.join(root, "data-source");
const graph = new Map();
function inside(file) { return file.startsWith(namespace + path.sep); }
function visit(file) {
  if (graph.has(file)) return;
  const tree = ts.createSourceFile(file, fs.readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  assert.equal(tree.parseDiagnostics.length, 0, `invalid module: ${path.relative(root, file)}`);
  const dependencies = new Set();
  graph.set(file, dependencies);
  for (const node of tree.statements) {
    if ((!ts.isImportDeclaration(node) && !ts.isExportDeclaration(node)) || !node.moduleSpecifier) continue;
    const module = node.moduleSpecifier.text;
    if (!module.startsWith(".")) continue;
    const dependency = path.resolve(path.dirname(file), module);
    if (inside(file)) assert.notEqual(dependency, facade, "implementation must not import the facade");
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
  active.delete(file); done.add(file);
}
check(facade);
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
assert.deepEqual(Object.keys(source).sort(), [
  "CONTROL_CENTER_SOURCE_KIND", "buildCharacterRuntimePatchFromSettingsSnapshot", "buildMusicRuntimePatch",
  "createBackendControlCenterSource", "createControlCenterDataSource", "createControlCenterRuntimeSnapshot"
].sort());
assert.equal(source.createControlCenterDataSource({ fetchImpl: null }).kind, "backend");
assert.throws(() => source.createControlCenterDataSource({ kind: "mock" }), /unsupported_control_center_source:mock/);
console.log(`data-source module smoke passed: ${graph.size - 1} reachable implementation modules, acyclic dependencies, 6 stable exports`);
