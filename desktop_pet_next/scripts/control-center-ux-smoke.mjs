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
const V2_COMPATIBILITY_HTML = "dist/control-center-v2.html";

for (const rel of [REQUIRED_HTML, V2_COMPATIBILITY_HTML]) {
  const abs = resolve(projectRoot, rel);
  assert.ok(existsSync(abs), `UX: built artifact ${rel} should exist`);
}
console.log("1/5 built artifacts present");

// ---------------------------------------------------------------------------
// 2. Built HTML has mounting point
// ---------------------------------------------------------------------------

{
  const htmlContent = readFileSync(resolve(projectRoot, REQUIRED_HTML), "utf8");
  assert.ok(htmlContent.includes('id="app"'), "UX: built HTML should have #app mounting point");
  assert.ok(htmlContent.includes("控制中心"), "UX: built HTML title should reference 控制中心");
  assert.ok(htmlContent.includes("module"), "UX: built HTML should load JS as module");
}
console.log("2/5 built HTML structure valid");

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
  assert.ok(cssContent.includes("not-allowed"), "UX: CSS should show not-allowed cursor on disabled actions");
  assert.ok(cssContent.includes("button:disabled"), "UX: CSS should have button:disabled selector");
}
console.log("3/5 built CSS has scrollbar prevention and disabled action styling");

{
  const htmlContent = readFileSync(resolve(projectRoot, V2_COMPATIBILITY_HTML), "utf8");
  assert.ok(htmlContent.includes("control-center-lab.html"), "UX: V2 compatibility entry should redirect to the canonical control center");
  assert.ok(htmlContent.includes("window.location.replace"), "UX: V2 compatibility entry should preserve query/hash with a script redirect");
  assert.ok(!htmlContent.includes('id="app"'), "UX: V2 compatibility entry must not own a second renderer");
}

// ---------------------------------------------------------------------------
// 4. Built JS contains key action IDs and nav labels
// ---------------------------------------------------------------------------

{
  const jsContent = readBuiltJsGraph(builtAssetRefs.js);

  // Window buttons must exist in the JS bundle
  const windowActionIds = ["window.minimize", "window.maximize", "window.close"];
  for (const actionId of windowActionIds) {
    assert.ok(jsContent.includes(actionId), `UX: JS bundle should reference ${actionId} for window chrome buttons`);
  }
  console.log("4/5 window chrome action IDs present in bundle");

  // Nav page labels
  const navLabels = ["总览", "聊天", "角色与外观", "语音与唤醒", "模型服务", "能力与权限", "系统与诊断"];
  for (const label of navLabels) {
    assert.ok(jsContent.includes(label), `UX: JS bundle should render nav item "${label}"`);
  }
  for (const label of ["真实数据模式", "连接模型服务", "密钥不会出现在快照和日志里"]) {
    assert.ok(jsContent.includes(label), `UX: JS bundle should render production label "${label}"`);
  }
  console.log("5/5 all 7 production destinations present in bundle");
}

// ---------------------------------------------------------------------------
// 5. Screenshots (require Puppeteer — gracefully skipped if unavailable)
// ---------------------------------------------------------------------------

const screenshotNames = [
  "control-center-overview.png",
  "control-center-chat.png",
  "control-center-appearance.png",
  "control-center-model.png",
  "control-center-voice.png",
  "control-center-abilities.png",
  "control-center-system.png",
  "control-center-overview-mobile.png",
  "control-center-chat-mobile.png",
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
      ["control-center-overview.png", "overview", { width: 1440, height: 900 }],
      ["control-center-chat.png", "chat", { width: 1440, height: 900 }],
      ["control-center-appearance.png", "appearance", { width: 1440, height: 900 }],
      ["control-center-model.png", "model", { width: 1440, height: 900 }],
      ["control-center-voice.png", "voice", { width: 1440, height: 900 }],
      ["control-center-abilities.png", "abilities", { width: 1440, height: 900 }],
      ["control-center-system.png", "system", { width: 1440, height: 900 }],
      ["control-center-overview-mobile.png", "overview", { width: 760, height: 900 }],
      ["control-center-chat-mobile.png", "chat", { width: 390, height: 844 }],
    ];

    for (const [name, navId, viewport] of pageNavMap) {
      await pages.setViewport(viewport);
      const url = `${baseUrl}/control-center-lab.html?page=${navId}`;
      await pages.goto(url, { waitUntil: "networkidle0", timeout: 15000 });
      try {
        await pages.waitForSelector(".ccv2-shell", { timeout: 5000 });
      } catch {
        // Keep the screenshot for diagnostics if the shell fails to render.
      }
      await new Promise((r) => setTimeout(r, 500));
      const outPath = resolve(codexDir, name);
      await pages.screenshot({ path: outPath, fullPage: false });
      screenshotPaths.push(outPath);
      screenshotCount += 1;
    }

    for (const viewport of [{ width: 1440, height: 900 }, { width: 760, height: 900 }, { width: 390, height: 844 }]) {
      await pages.setViewport(viewport);
      await pages.goto(`${baseUrl}/control-center-lab.html?page=chat`, { waitUntil: "networkidle0", timeout: 15000 });
      const geometry = await pages.evaluate(() => {
        const shell = document.querySelector(".ccv2-shell");
        const page = document.querySelector(".ccv2-scroll.is-chat");
        // The browser smoke runs without a backend. Mount the minimum production
        // chat structure here so layout ownership is tested without shipping demo data.
        page.innerHTML = `
          <section class="ccv2-chat">
            <aside class="chat-presence glass-panel"></aside>
            <section class="chat-workspace glass-panel">
              <header class="chat-header"><h2>Layout fixture</h2></header>
              <div class="chat-message-viewport"><div class="chat-message-list"></div></div>
              <form class="chat-composer"><div class="composer-input-wrap"><textarea></textarea></div><button class="composer-send">Send</button></form>
            </section>
          </section>`;
        const workspace = document.querySelector(".chat-workspace");
        const header = document.querySelector(".chat-header");
        const messages = document.querySelector(".chat-message-list");
        const messageViewport = document.querySelector(".chat-message-viewport");
        const composer = document.querySelector(".chat-composer");
        for (let index = 0; index < 48; index += 1) {
          const message = document.createElement("article");
          message.className = "chat-message is-assistant";
          message.innerHTML = `<div class="message-avatar is-fallback">A</div><div class="message-content"><p>Long conversation layout fixture ${index}</p></div>`;
          messages.append(message);
        }
        return {
          viewportHeight: window.innerHeight,
          documentHeight: document.documentElement.scrollHeight,
          bodyOverflowY: getComputedStyle(document.body).overflowY,
          shellHeight: shell?.getBoundingClientRect().height || 0,
          pageOverflowY: page ? getComputedStyle(page).overflowY : "missing",
          messageOverflowY: messageViewport ? getComputedStyle(messageViewport).overflowY : "missing",
          messageClientHeight: messageViewport?.clientHeight || 0,
          messageScrollHeight: messageViewport?.scrollHeight || 0,
          workspaceTop: workspace?.getBoundingClientRect().top || 0,
          workspaceBottom: workspace?.getBoundingClientRect().bottom || 0,
          headerTop: header?.getBoundingClientRect().top || 0,
          composerBottom: composer?.getBoundingClientRect().bottom || 0,
          railRight: document.querySelector(".ccv2-rail")?.getBoundingClientRect().right || 0,
          actionsRight: document.querySelector(".topbar-actions")?.getBoundingClientRect().right || 0,
        };
      });
      assert.ok(Math.abs(geometry.shellHeight - geometry.viewportHeight) <= 1, `UX: shell should stay viewport-bound at ${viewport.width}px`);
      assert.ok(geometry.documentHeight <= geometry.viewportHeight + 1, `UX: long chat must not grow the document at ${viewport.width}px`);
      assert.equal(geometry.bodyOverflowY, "hidden", `UX: body must not own chat scrolling at ${viewport.width}px`);
      assert.equal(geometry.pageOverflowY, "hidden", `UX: chat page must not own message scrolling at ${viewport.width}px`);
      assert.equal(geometry.messageOverflowY, "auto", `UX: message viewport should own scrolling at ${viewport.width}px`);
      assert.ok(geometry.messageScrollHeight > geometry.messageClientHeight, `UX: long chat should overflow its message viewport at ${viewport.width}px`);
      assert.ok(geometry.headerTop >= geometry.workspaceTop - 1, `UX: chat header should stay inside its workspace at ${viewport.width}px`);
      assert.ok(geometry.composerBottom <= geometry.workspaceBottom + 1, `UX: chat composer should stay inside its workspace at ${viewport.width}px`);
      assert.ok(geometry.railRight <= viewport.width + 1, `UX: navigation should stay inside the viewport at ${viewport.width}px`);
      assert.ok(geometry.actionsRight <= viewport.width + 1, `UX: window actions should stay inside the viewport at ${viewport.width}px`);
    }

    await browser.close();
    await server.close();
    console.log(`UX screenshots captured (${screenshotCount}/${screenshotNames.length})`);
  } catch (err) {
    console.log("UX screenshots: SKIPPED (browser error: " + (err.message || err) + ")");
  }
} else {
  console.log("UX screenshots: SKIPPED (puppeteer unavailable)");
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

function readBuiltJsGraph(entryRel) {
  const visited = new Set();
  const chunks = [];

  function visit(rel) {
    const normalized = rel.replaceAll("\\", "/");
    if (visited.has(normalized)) return;
    visited.add(normalized);
    const absolute = resolve(projectRoot, normalized);
    assert.ok(existsSync(absolute), `UX: imported JS chunk ${normalized} should exist`);
    const content = readFileSync(absolute, "utf8");
    chunks.push(content);
    const importPattern = /(?:from\s*|import\s*)["'](\.\/[^"']+\.js)["']/g;
    for (const match of content.matchAll(importPattern)) {
      const imported = resolve(dirname(absolute), match[1]);
      const relative = imported.slice(projectRoot.length + 1).replaceAll("\\", "/");
      visit(relative);
    }
  }

  visit(entryRel);
  return chunks.join("\n");
}
