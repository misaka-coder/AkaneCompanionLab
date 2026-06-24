import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const __dirname = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(__dirname, "..");
const codexDir = resolve(projectRoot, ".codex");

// ---------------------------------------------------------------------------
// 1. Built artifacts exist
// ---------------------------------------------------------------------------

const REQUIRED_HTML = "dist/control-center-lab.html";

for (const rel of [REQUIRED_HTML]) {
  const abs = resolve(projectRoot, rel);
  assert.ok(existsSync(abs), `UX: built artifact ${rel} should exist`);
}
console.log("1/6 built artifacts present");

// ---------------------------------------------------------------------------
// 2. Built HTML has mounting point
// ---------------------------------------------------------------------------

{
  const htmlContent = readFileSync(resolve(projectRoot, REQUIRED_HTML), "utf8");
  assert.ok(htmlContent.includes('id="app"'), "UX: built HTML should have #app mounting point");
  assert.ok(htmlContent.includes("控制中心"), "UX: built HTML title should reference 控制中心");
  assert.ok(htmlContent.includes("module"), "UX: built HTML should load JS as module");
}
console.log("2/6 built HTML structure valid");

const builtAssetRefs = findBuiltControlCenterAssets();
for (const rel of [builtAssetRefs.css, builtAssetRefs.js]) {
  const abs = resolve(projectRoot, rel);
  assert.ok(existsSync(abs), `UX: built artifact ${rel} should exist`);
}

// ---------------------------------------------------------------------------
// 3. Built CSS has critical UX rules
// ---------------------------------------------------------------------------

{
  const cssContent = readFileSync(resolve(projectRoot, builtAssetRefs.css), "utf8");
  assert.ok(cssContent.includes("overflow:hidden"), "UX: CSS should have overflow:hidden on shell/body");
  assert.ok(cssContent.includes("overflow-x:hidden"), "UX: CSS should have overflow-x:hidden on body");
  assert.ok(cssContent.includes("action-unavailable"), "UX: CSS should style [data-action-unavailable]");
  assert.ok(cssContent.includes("not-allowed"), "UX: CSS should show not-allowed cursor on disabled actions");
  assert.ok(cssContent.includes("pointer-events:none"), "UX: CSS should disable pointer events on :disabled buttons");
  assert.ok(cssContent.includes("button:disabled"), "UX: CSS should have button:disabled selector");
}
console.log("3/6 built CSS has scrollbar prevention and disabled action styling");

// ---------------------------------------------------------------------------
// 4. Built JS contains key action IDs and nav labels
// ---------------------------------------------------------------------------

{
  const jsContent = readFileSync(resolve(projectRoot, builtAssetRefs.js), "utf8");

  // Window buttons must exist in the JS bundle
  const windowActionIds = ["window.minimize", "window.maximize", "window.close"];
  for (const actionId of windowActionIds) {
    assert.ok(jsContent.includes(actionId), `UX: JS bundle should reference ${actionId} for window chrome buttons`);
  }
  console.log("4/6 window chrome action IDs present in bundle");

  // Nav page labels
  const navLabels = ["总览", "模型", "角色", "语音", "音乐", "桌面感知", "能力", "高级"];
  for (const label of navLabels) {
    assert.ok(jsContent.includes(label), `UX: JS bundle should render nav item "${label}"`);
  }
  for (const label of ["产品化状态", "开源前验收", "这些不是要隐藏的功能"]) {
    assert.ok(jsContent.includes(label), `UX: JS bundle should render productization label "${label}"`);
  }
  for (const label of ["MCP 配置向导", "添加自定义 stdio", "保存并发现工具", "HTTP / Streamable", "默认不进提示词"]) {
    assert.ok(jsContent.includes(label), `UX: JS bundle should render MCP manager label "${label}"`);
  }
  console.log("5/6 all 8 nav page labels present in bundle");
}

// ---------------------------------------------------------------------------
// 5. Screenshots (require Puppeteer — gracefully skipped if unavailable)
// ---------------------------------------------------------------------------

const screenshotNames = [
  "control-center-overview.png",
  "control-center-model.png",
  "control-center-music.png",
  "control-center-voice.png",
  "control-center-advanced.png",
];

let screenshotCount = 0;
const screenshotPaths = [];
let puppeteer;

try {
  puppeteer = (await import("puppeteer")).default;
} catch {
  puppeteer = null;
}

if (puppeteer) {
  let browser;
  let server;
  try {
    browser = await puppeteer.launch({
      headless: true,
      args: ["--no-sandbox", "--disable-setuid-sandbox"],
    });
    const { createServer } = await import("vite");
    server = await createServer({
      root: projectRoot,
      server: { port: 0, host: "127.0.0.1" },
    });
    await server.listen();
    const baseUrl = `http://127.0.0.1:${server.config.server.port}`;

    const pages = await browser.newPage();
    await pages.setViewport({ width: 1440, height: 900 });

    const pageNavMap = [
      ["control-center-overview.png", "overview"],
      ["control-center-model.png", "model"],
      ["control-center-music.png", "music"],
      ["control-center-voice.png", "voice"],
      ["control-center-advanced.png", "advanced"],
    ];

    for (const [name, navId] of pageNavMap) {
      const url = `${baseUrl}/control-center-lab.html?page=${navId}&source=mock`;
      await pages.goto(url, { waitUntil: "networkidle0", timeout: 15000 });
      try {
        await pages.waitForSelector("#page-content", { timeout: 5000 });
      } catch {
        // page-content may not be rendered in headless mode without Tauri;
        // continue anyway to capture whatever is on screen.
      }
      await new Promise((r) => setTimeout(r, 500));
      const outPath = resolve(codexDir, name);
      await pages.screenshot({ path: outPath, fullPage: false });
      screenshotPaths.push(outPath);
      screenshotCount += 1;
    }

    await browser.close();
    await server.close();
    console.log(`6/6 screenshots captured (${screenshotCount}/${screenshotNames.length})`);
  } catch (err) {
    console.log("6/6 screenshots: SKIPPED (browser error: " + (err.message || err) + ")");
  }
} else {
  console.log("6/6 screenshots: SKIPPED (puppeteer unavailable)");
}

// ---------------------------------------------------------------------------
// Summary: list screenshot paths
// ---------------------------------------------------------------------------

if (screenshotPaths.length) {
  console.log("\n--- UX screenshot paths ---");
  for (const path of screenshotPaths) {
    console.log(`  file://${path.replace(/\\/g, "/")}`);
  }
} else {
  console.log("\n--- UX screenshot paths ---");
  console.log("  none captured in this environment");
}
console.log("\ncontrol-center UX smoke passed");

function findBuiltControlCenterAssets() {
  const htmlContent = readFileSync(resolve(projectRoot, REQUIRED_HTML), "utf8");
  const cssMatch = htmlContent.match(/href="(\/assets\/controlCenterLab-[^"]+\.css)"/);
  const jsMatch = htmlContent.match(/src="(\/assets\/controlCenterLab-[^"]+\.js)"/);
  assert.ok(cssMatch, "UX: built HTML should reference controlCenterLab CSS asset");
  assert.ok(jsMatch, "UX: built HTML should reference controlCenterLab JS asset");
  return {
    css: `dist${cssMatch[1]}`,
    js: `dist${jsMatch[1]}`,
  };
}
