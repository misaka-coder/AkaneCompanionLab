import { execFileSync } from "node:child_process";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { compile } from "json-schema-to-typescript";

const root = fileURLToPath(new URL("../../", import.meta.url));
const output = new URL("../src/scene/contracts/", import.meta.url);
const schema = JSON.parse(execFileSync("python", ["-c",
  "import json; from companion_v01.scene.contracts import SceneWire; print(json.dumps(SceneWire.model_json_schema(mode='serialization'),ensure_ascii=True))"
], { cwd: root, encoding: "utf8" }));
const files = {
  "scene.schema.json": JSON.stringify(schema, null, 2) + "\n",
  "scene.generated.ts": await compile(schema, "SceneWire", {
    bannerComment: "/* Generated from companion_v01.scene.contracts; run npm run scene:contracts. */",
    additionalProperties: false,
  }),
};
await mkdir(output, { recursive: true });
for (const [name, contents] of Object.entries(files)) {
  const url = new URL(name, output);
  if (process.argv.includes("--check")) {
    if (await readFile(url, "utf8") !== contents) throw Error(`Contract drift: ${name}`);
  } else await writeFile(url, contents);
}
console.log("Scene contracts match Python schemas.");
