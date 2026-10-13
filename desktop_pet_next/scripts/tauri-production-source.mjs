import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../src-tauri/src/", import.meta.url));

export function productionRustSource() {
  const pending = [path.join(root, "main.rs")], seen = new Set(), sources = [];
  while (pending.length) {
    const file = pending.pop();
    if (seen.has(file)) continue;
    seen.add(file);
    const source = fs.readFileSync(file, "utf8");
    sources.push(source);
    const directory = path.basename(file) === "main.rs" || path.basename(file) === "mod.rs"
      ? path.dirname(file) : file.slice(0, -3);
    const children = [];
    const declarations = /((?:^#\[[^\n]+\]\r?\n)*)^(?:pub(?:\([^)]*\))? )?mod (\w+);/gm;
    for (const [, attributes, name] of source.matchAll(declarations)) {
      if (["tests", "test_support"].includes(name) || name.endsWith("_tests") || attributes.includes("cfg(test)")) continue;
      const direct = path.join(directory, `${name}.rs`);
      children.push(fs.existsSync(direct) ? direct : path.join(directory, name, "mod.rs"));
    }
    pending.push(...children.reverse());
  }
  return sources.join("\n\n");
}

export function rustFunctionSource(name) {
  const source = productionRustSource();
  const start = new RegExp(`^(?:pub(?:\\([^)]*\\))? )?(?:async )?fn ${name}\\b`, "m").exec(source);
  if (!start) throw Error(`Production Rust function missing: ${name}`);
  const end = /^}/m.exec(source.slice(start.index));
  if (!end) throw Error(`Production Rust body missing: ${name}`);
  return source.slice(start.index, start.index + end.index + 1);
}
