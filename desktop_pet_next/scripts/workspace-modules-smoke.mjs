import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src");
const entry = path.join(root, "workspace.js"), namespace = path.join(root, "workspace"), host = path.join(namespace, "host.js");
const graph = new Map();
function visit(file) {
  if (graph.has(file)) return;
  const source = fs.readFileSync(file, "utf8"), tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  assert.equal(tree.parseDiagnostics.length, 0, `invalid workspace source: ${file}`);
  assert.ok(source.trimEnd().split(/\r?\n/).length <= (file === entry ? 15 : 250), `workspace owner grew too large: ${file}`);
  const dependencies = new Set(); graph.set(file, dependencies);
  function inspect(node) {
    if (ts.isIdentifier(node) && ["window", "navigator"].includes(node.text)
      && !(ts.isPropertyAccessExpression(node.parent) && node.parent.name === node)) {
      assert.equal(file, host, `global platform access belongs in host.js: ${file}`);
    }
    ts.forEachChild(node, inspect);
  }
  inspect(tree);
  for (const node of tree.statements) {
    if ((!ts.isImportDeclaration(node) && !ts.isExportDeclaration(node)) || !node.moduleSpecifier) continue;
    const name = node.moduleSpecifier.text;
    if (!name.startsWith(".")) {
      assert.ok(file === host && name.startsWith("@tauri-apps/"), `native imports belong in host.js: ${file}`); continue;
    }
    const dependency = path.resolve(path.dirname(file), name);
    assert.ok(fs.existsSync(dependency), `missing workspace dependency: ${name}`);
    if (dependency.startsWith(namespace + path.sep)) { dependencies.add(dependency); visit(dependency); }
  }
}
visit(entry);
const active = new Set(), done = new Set();
function check(file) {
  assert.ok(!active.has(file), `workspace dependency cycle: ${file}`);
  if (done.has(file)) return;
  active.add(file); for (const dependency of graph.get(file)) check(dependency);
  active.delete(file); done.add(file);
}
check(entry);
for (const name of fs.readdirSync(namespace).filter(name => name.endsWith(".js"))) {
  assert.ok(graph.has(path.join(namespace, name)), `orphan workspace module: ${name}`);
}
const program = ts.createProgram([...graph.keys()], { allowJs: true, checkJs: true, noEmit: true, noResolve: true,
  target: ts.ScriptTarget.Latest, module: ts.ModuleKind.ESNext });
for (const file of graph.keys()) {
  const unbound = program.getSemanticDiagnostics(program.getSourceFile(file)).filter(item => item.code === 2304 || item.code === 2552);
  assert.deepEqual(unbound.map(item => ts.flattenDiagnosticMessageText(item.messageText, " ")), [], `unbound workspace context: ${file}`);
}
console.log(`workspace boundaries passed: ${graph.size - 1} reachable modules, acyclic imports, bound identifiers, native host boundary`);
