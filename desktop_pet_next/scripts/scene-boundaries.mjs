import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../src/scene/', import.meta.url));
const files = [];
async function walk(dir) {
  for (const item of await readdir(dir, { withFileTypes: true })) {
    const target = path.join(dir, item.name);
    if (item.isDirectory()) await walk(target);
    else if (/\.(ts|vue|css)$/.test(item.name) && !item.name.includes('.generated.')) files.push(target);
  }
}
await walk(root);
const graph = new Map();
for (const file of files) {
  const source = await readFile(file, 'utf8');
  const relative = path.relative(root, file).replaceAll('\\', '/');
  const lines = source.trimEnd().split('\n').length;
  const maximum = ['app/App.vue', 'app/main.ts'].includes(relative) ? 150 : 400;
  if (lines > maximum) throw Error(`${relative}: ${lines} lines exceeds ${maximum}`);
  if (!relative.startsWith('bridges/') && /from ["']@tauri|\bfetch\(/.test(source)) {
    throw Error(`${relative}: host I/O belongs in bridges`);
  }
  if (!relative.startsWith('stage/pixi/') && /from ["']pixi.js/.test(source)) {
    throw Error(`${relative}: renderer dependency escaped stage/pixi`);
  }
  const edges = [];
  for (const match of source.matchAll(/(?:from\s+|import\s*\()["'](\.[^"']+)["']/g)) {
    const base = path.resolve(path.dirname(file), match[1]);
    const resolved = files.find(f => f === base || f === base + '.ts' || f === base + '.vue');
    if (resolved) edges.push(resolved);
  }
  graph.set(file, edges);
}
const visiting = new Set(), done = new Set();
function visit(file) {
  if (visiting.has(file)) throw Error(`Circular dependency: ${path.relative(root, file)}`);
  if (done.has(file)) return;
  visiting.add(file);
  for (const edge of graph.get(file) || []) visit(edge);
  visiting.delete(file); done.add(file);
}
for (const file of files) visit(file);
console.log(`Scene boundaries: ${files.length} files, no import cycles, size limits satisfied.`);
