import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/control-center-v2");
const entry = path.join(root, "index.js"), namespace = path.join(root, "app");
const inside = file => file.startsWith(namespace + path.sep);
const graph = new Map();
const contracts = new Set(["model-service.js", "view-model.js", "presentation-preferences.js", "chat-auto-previews.js",
  "chat-timeline.js", "chat-history.js", "components/shell.js"].map(name => path.join(root, name)));
function visit(file) {
  if (graph.has(file)) return;
  const source = fs.readFileSync(file, "utf8");
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  assert.equal(tree.parseDiagnostics.length, 0, `invalid module: ${path.relative(root, file)}`);
  assert.ok(source.trimEnd().split(/\r?\n/).length <= (file === entry ? 90 : 250), `responsibilities grew too large: ${file}`);
  const dependencies = new Set(); graph.set(file, dependencies);
  for (const node of tree.statements) {
    if ((!ts.isImportDeclaration(node) && !ts.isExportDeclaration(node)) || !node.moduleSpecifier) continue;
    const module = node.moduleSpecifier.text;
    assert.ok(module.startsWith("."), `application controllers must use the injected bridge: ${module}`);
    const dependency = path.resolve(path.dirname(file), module);
    assert.ok(fs.existsSync(dependency), `missing dependency: ${module}`);
    if (inside(file) && !inside(dependency)) assert.ok(contracts.has(dependency), `unexpected controller dependency: ${module}`);
    if (inside(dependency)) { dependencies.add(dependency); visit(dependency); }
  }
}
visit(entry);
const active = new Set(), done = new Set();
function check(file) {
  assert.ok(!active.has(file), `dependency cycle: ${path.relative(root, file)}`);
  if (done.has(file)) return;
  active.add(file); for (const dependency of graph.get(file)) check(dependency);
  active.delete(file); done.add(file);
}
check(entry);
for (const name of fs.readdirSync(namespace).filter(name => name.endsWith(".js"))) {
  assert.ok(graph.has(path.join(namespace, name)), `orphan controller: ${name}`);
}
const program = ts.createProgram([...graph.keys()], { allowJs: true, checkJs: true, noEmit: true, noResolve: true,
  target: ts.ScriptTarget.Latest, module: ts.ModuleKind.ESNext });
for (const file of graph.keys()) {
  const unbound = program.getSemanticDiagnostics(program.getSourceFile(file)).filter(item => item.code === 2304 || item.code === 2552);
  assert.deepEqual(unbound.map(item => ts.flattenDiagnosticMessageText(item.messageText, " ")), [], `unbound context: ${file}`);
}
console.log(`control-center application modules passed: ${graph.size - 1} modules, acyclic imports, bound identifiers, injected bridge`);
