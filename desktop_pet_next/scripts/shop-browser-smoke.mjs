import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { resolve, extname } from "node:path";
const { chromium } = await import(process.env.AKANE_PLAYWRIGHT_MODULE || "playwright");
const root = resolve("desktop_pet_next");
const server = createServer(async (req, res) => {
  try {
    const route = decodeURIComponent(new URL(req.url, "http://local").pathname);
    let file = resolve(root, "." + (route === "/" ? "/shop.html" : route));
    if (!file.startsWith(root)) { res.writeHead(403).end(); return; }
    let content = await readFile(file);
    if (route === "/") content = content.toString().replace("</head>", '<link rel="stylesheet" href="/src/shop.css"></head>');
    if (route === "/src/shop.js") content = content.toString()
      .replace(/import \{ invoke \} from "@tauri-apps\/api\/core";/, "const { invoke } = window.testBridge;")
      .replace(/import \{ emit, emitTo, listen \} from "@tauri-apps\/api\/event";/, "const { emit, emitTo, listen } = window.testBridge;")
      .replace('import "./shop.css";', "");
    res.setHeader("Content-Type", ({ ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript" })[extname(file)] || "application/octet-stream");
    res.end(content);
  } catch { res.writeHead(404).end(); }
});
await new Promise(r => server.listen(0, "127.0.0.1", r));
let browser;
try {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 820, height: 900 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    const listeners = new Map();
    const items = [
      { id: "tea", name: "温热玄米茶", description: "烘米的香气，适合安静的午后。", price: 8, effects: { hunger: 6, energy: 16, affection: 2 } },
      { id: "rice", name: "手作饭团", description: "刚刚捏好的饭团，认真吃饭才有力气。", price: 12, effects: { hunger: 24, energy: 8, affection: 4 } },
      { id: "cake", name: "草莓小蛋糕", description: "给平常的一天，留一点小小的甜。", price: 18, effects: { hunger: 12, energy: 5, affection: 10 } },
      { id: "apple", name: "清甜苹果", description: "清脆多汁，刚好的轻食补给。", price: 5, effects: { hunger: 10, energy: 4, affection: 2 } }
    ];
    const data = { character: { packId: "reimu", name: "博丽灵梦", care: { enabled: true, shopItems: items,
      work: { enabled: true, durationSeconds: 20, hungerCost: 12, energyCost: 25, rewardCoinsMin: 5, rewardCoinsMax: 10 },
      allowance: { enabled: true, coins: 4, maxCoins: 6 } } }, state: { boundBotId: "bot", backendUrl: "http://127.0.0.1:12001", care: { coins: 40, hunger: 72, energy: 86, affection: 38, inventory: { tea: 2 } } }, resource: { features: { care: { enabled: true, status: "enabled" } } } };
    const test = window.shopTest = { data, items: structuredClone(items), revision: "v1", updates: 0, creates: 0, mode: "ok", commands: [] };
    test.broadcast = () => { for (const [name, callback] of listeners) if (name.includes("snapshot")) callback({ payload: structuredClone(test.data) }); };
    window.testBridge = {
      listen: async (name, callback) => { listeners.set(name, callback); return () => listeners.delete(name); },
      emit: async () => {},
      emitTo: async (_target, _event, payload) => { test.commands.push(payload); if (payload.command === "requestSnapshot") setTimeout(test.broadcast, 0); },
      invoke: async (command, args) => {
        if (command !== "backend_admin_request") return {};
        const body = JSON.parse(args.request.body);
        if (body.action === "update") {
          test.updates++;
          if (test.mode === "slow") await new Promise(resolve => { test.release = resolve; });
          if (test.mode === "fail") return { ok: false, httpStatus: 500, body: "{}" };
          if (test.mode === "conflict") return { ok: false, httpStatus: 409, body: JSON.stringify({ reason: "shop_revision_conflict" }) };
          test.items = test.items.map(item => item.id === body.item_id
            ? { ...item, name: body.name, description: body.description || item.description, price: body.price, effects: body.effects, usable_in: body.usable_in }
            : item);
          test.revision = "v" + (test.updates + 1);
        }
        if (body.action === "create") {
          test.creates++;
          if (test.mode === "fail") return { ok: false, httpStatus: 500, body: "{}" };
          const id = "custom_" + String(test.creates).padStart(4, "0");
          test.items = [...test.items, { id, name: body.name, description: body.description || "",
            price: body.price, effects: body.effects, usable_in: body.usable_in, custom: true }];
          test.revision = "v" + (test.updates + test.creates + 1);
        }
        return { ok: true, httpStatus: 200, body: JSON.stringify({ ok: true, revision: test.revision, items: test.items, enabled: true }) };
      }
    };
  });
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.getByRole("button", { name: "调整食物" }).first().waitFor();
  await page.waitForFunction(() => !document.querySelector(".edit-button").disabled);
  const output = resolve("work/shop-dpi-20260912"); await mkdir(output, { recursive: true });
  await page.screenshot({ path: resolve(output, "shop.png"), fullPage: true });
  await page.getByRole("button", { name: "调整食物" }).first().click();
  await page.locator("#food-price").fill("13");
  await page.locator("#food-energy").fill("25");
  await page.screenshot({ path: resolve(output, "editor.png") });
  await page.getByRole("button", { name: "保存设置" }).click();
  await page.waitForFunction(() => !document.querySelector("dialog").open);
  assert.equal(await page.evaluate(() => shopTest.items[0].price), 13);
  assert.equal(await page.evaluate(() => shopTest.items[0].effects.energy), 25);
  await page.getByRole("button", { name: "调整食物" }).first().click();
  assert.equal(await page.locator("#food-price").inputValue(), "13");
  await page.evaluate(() => { shopTest.mode = "conflict"; });
  await page.getByRole("button", { name: "保存设置" }).click();
  await page.locator("#editor-error").waitFor({ state: "visible" });
  assert.match(await page.locator("#editor-error").textContent(), /已在其他地方更新/);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "新增商品" }).click();
  await page.locator("#food-name").fill("抹茶大福");
  await page.locator("#food-price").fill("9");
  await page.locator("#food-hunger").fill("14");
  await page.locator("#food-affection").fill("6");
  await page.locator("#food-description").fill("新做的和果子。");
  await page.locator("#food-qq").uncheck();
  await page.screenshot({ path: resolve(output, "editor-create.png") });
  await page.getByRole("button", { name: "新增到货架" }).click();
  await page.waitForFunction(() => !document.querySelector("dialog").open);
  await page.getByRole("heading", { name: "抹茶大福" }).waitFor();
  assert.equal(await page.evaluate(() => shopTest.creates), 1);
  assert.equal(await page.evaluate(() => shopTest.items.at(-1).name), "抹茶大福");
  assert.equal(await page.evaluate(() => shopTest.items.at(-1).price), 9);
  assert.deepEqual(await page.evaluate(() => shopTest.items.at(-1).usable_in), ["desktop_pet"]);
  assert.equal(await page.getByRole("button", { name: "新增商品" }).isDisabled(), false);
  await page.getByRole("button", { name: "新增商品" }).click();
  await page.locator("#food-name").fill("");
  await page.getByRole("button", { name: "新增到货架" }).click();
  assert.equal(await page.locator("dialog").evaluate(node => node.open), true);
  assert.equal(await page.evaluate(() => shopTest.creates), 1);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.evaluate(() => { shopTest.mode = "slow"; });
  await page.getByRole("button", { name: "调整食物" }).first().click();
  await page.getByRole("button", { name: "保存设置" }).click();
  assert.equal(await page.locator("#editor-save").isDisabled(), true);
  await page.evaluate(() => { shopTest.data.character.packId = "other"; shopTest.broadcast(); shopTest.release(); });
  await page.waitForFunction(() => !document.querySelector("dialog").open);
  await page.setViewportSize({ width: 480, height: 740 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: resolve(output, "shop-narrow.png"), fullPage: true });
  await page.evaluate(() => { shopTest.data.resource.features.care.enabled = false; shopTest.broadcast(); });
  assert.equal(await page.getByRole("button", { name: "购买", exact: true }).count(), 0);
  assert.deepEqual(errors, []);
  console.log("shop browser: save/reopen, conflict, create item, create validation, slow save, character switch, disabled state, narrow layout; 0 page errors");
} finally { await browser?.close(); await new Promise(r => server.close(r)); }
