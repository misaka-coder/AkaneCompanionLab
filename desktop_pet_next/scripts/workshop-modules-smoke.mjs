import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src");
const entry = path.join(root, "workshop.js");
const namespace = path.join(root, "workshop");
const graph = new Map();
const inside = file => file.startsWith(namespace + path.sep);
function visit(file) {
  if (graph.has(file)) return;
  const tree = ts.createSourceFile(file, fs.readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  assert.equal(tree.parseDiagnostics.length, 0, `invalid workshop module: ${path.relative(root, file)}`);
  const dependencies = new Set();
  graph.set(file, dependencies);
  for (const node of tree.statements) {
    if ((!ts.isImportDeclaration(node) && !ts.isExportDeclaration(node)) || !node.moduleSpecifier) continue;
    const module = node.moduleSpecifier.text;
    if (!module.startsWith(".")) continue;
    const dependency = path.resolve(path.dirname(file), module);
    if (inside(file)) assert.notEqual(dependency, entry, "workshop implementation must not import its entry");
    if (inside(dependency)) { dependencies.add(dependency); visit(dependency); }
  }
}
visit(entry);
for (const name of fs.readdirSync(namespace).filter(name => name.endsWith(".js"))) {
  assert.ok(graph.has(path.join(namespace, name)), `orphan workshop module: ${name}`);
}
const active = new Set(), done = new Set();
function check(file) {
  assert.ok(!active.has(file), `workshop dependency cycle: ${path.relative(root, file)}`);
  if (done.has(file)) return;
  active.add(file);
  for (const dependency of graph.get(file)) check(dependency);
  active.delete(file); done.add(file);
}
check(entry);
const program = ts.createProgram([...graph.keys()], {
  allowJs: true, checkJs: true, noEmit: true, noResolve: true,
  target: ts.ScriptTarget.Latest, module: ts.ModuleKind.ESNext
});
for (const file of graph.keys()) {
  const unbound = program.getSemanticDiagnostics(program.getSourceFile(file))
    .filter(item => item.code === 2304 || item.code === 2552);
  assert.deepEqual(unbound.map(item => ts.flattenDiagnosticMessageText(item.messageText, " ")), [],
    `unbound workshop context: ${path.relative(root, file)}`);
}
console.log(`workshop module smoke passed: ${graph.size - 1} reachable modules, no orphans, acyclic dependencies, no unbound context`);
