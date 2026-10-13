// Enforce the extracted module boundaries and check identifiers that Vite cannot catch.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const directory = fileURLToPath(new URL("../src/pet/", import.meta.url));
const files = fs.readdirSync(directory, { recursive: true }).filter(name => name.endsWith(".js")).map(name => path.join(directory, name));
const sources = files.map(file => ts.createSourceFile(file, fs.readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.JS));
const ports = new Map();
const controllerFiles = new Set();
for (const source of sources) {
  for (const node of source.statements) {
    if (!ts.isFunctionDeclaration(node) || !/^create.*Controller$/.test(node.name?.text)) continue;
    controllerFiles.add(path.resolve(source.fileName));
    const label = node.name.text.slice(6, -10);
    const port = label[0].toLowerCase() + label.slice(1) + "Port";
    const exported = node.body.statements.findLast(statement => ts.isReturnStatement(statement));
    assert.ok(ts.isObjectLiteralExpression(exported?.expression), `${source.fileName}: controller must expose a named interface`);
    ports.set(port, new Set(exported.expression.properties.map(property => property.name?.text)));
  }
}

for (const source of sources) {
  const name = path.relative(directory, source.fileName).replaceAll("\\", "/");
  for (const statement of source.statements) {
    if (ts.isImportDeclaration(statement)) {
      const target = statement.moduleSpecifier.text;
      assert.ok(!target.startsWith("@tauri-apps/"), `${name}: native transport belongs in platform/main`);
      if (target.startsWith(".")) {
        const relative = path.relative(directory, path.resolve(path.dirname(source.fileName), target)).replaceAll("\\", "/");
        if (!relative.startsWith("../") && name !== "application.js") {
          const featureComposition = ["voice", "music"].some(feature =>
            (name === `${feature}.js` || name.startsWith(`${feature}/`)) && relative.startsWith(`${feature}/`));
          const importsController = controllerFiles.has(path.resolve(path.dirname(source.fileName), target));
          assert.ok(!importsController || featureComposition, `${name}: compose sibling controllers in application.js and inject their named ports`);
        }
      }
    }
    if (ts.isVariableStatement(statement)) assert.ok(statement.declarationList.flags & ts.NodeFlags.Const, `${name}: mutable runtime state belongs inside its controller`);
  }
  function walk(node) {
    if (ts.isPropertyAccessExpression(node) && ts.isIdentifier(node.expression) && ports.has(node.expression.text)) {
      assert.ok(ports.get(node.expression.text).has(node.name.text), `${name}: missing ${node.expression.text}.${node.name.text}`);
    }
    if (ts.isBinaryExpression(node) && node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && node.operatorToken.kind <= ts.SyntaxKind.LastAssignment) {
      let root = node.left;
      while (ts.isPropertyAccessExpression(root) || ts.isElementAccessExpression(root)) root = root.expression;
      assert.ok(!ts.isIdentifier(root) || !ports.has(root.text), `${name}: state changes must use the owner's methods: ${node.getText(source)}`);
    }
    ts.forEachChild(node, walk);
  }
  walk(source);
}

const names = new Set(files.map(file => path.resolve(file).toLowerCase()));
const program = ts.createProgram(files, {
  allowJs: true, checkJs: true, noEmit: true, noUnusedLocals: true, noUnusedParameters: true,
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext,
  moduleResolution: ts.ModuleResolutionKind.Bundler, skipLibCheck: true
});
const identifierCodes = new Set([2304, 2552, 18004, 2588, 2540, 6133, 6192]);
const diagnostics = [...program.getSyntacticDiagnostics(), ...program.getSemanticDiagnostics()].filter(item =>
  item.file && names.has(path.resolve(item.file.fileName).toLowerCase()) &&
  (identifierCodes.has(item.code) || item.category === ts.DiagnosticCategory.Error && item.code < 2000));
assert.equal(diagnostics.length, 0, diagnostics.map(item => `${path.basename(item.file.fileName)}: ${ts.flattenDiagnosticMessageText(item.messageText, " ")}`).join("\n"));
console.log(`pet runtime checks: ${files.length} modules; named interfaces, state ownership, import boundaries, syntax and identifier checks passed`);
