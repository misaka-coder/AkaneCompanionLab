import assert from "node:assert/strict";
import test from "node:test";
import { loadPetRuntime, createEnvironment, mockBackend, tick } from "./pet-runtime-harness.mjs";

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

test("panel visual settings convergence", async (t) => {
  const runtime = await loadPetRuntime();
  t.after(() => runtime.close());

  async function createApp(options = {}) {
    const backend = mockBackend();
    const env = createEnvironment({ ...backend, tauri: true, ...options });
    const app = runtime.createPetApplication({
      ...env,
      bundledCharacterAssets: { "/synthetic/normal.png": "synthetic.png" }
    });
    try {
      await app.start();
      for (let i = 0; i < 50 && !env.nativeHandlers.has("panel:action"); i++) {
        await tick();
      }
      const onPanelAction = env.nativeHandlers.get("panel:action");
      assert.ok(typeof onPanelAction === "function", "panel:action listener must be registered");
      return { app, env, onPanelAction };
    } catch (err) {
      app.dispose();
      throw err;
    }
  }

  await t.test("1. 缩放接入原生提交：面板调整后状态正确，debounce 到期后发起 set_visual_scale", async () => {
    const { app, env, onPanelAction } = await createApp();
    try {
      env.nativeCalls.length = 0;
      onPanelAction({ payload: { action: "set-scale", value: 1.2 } });
      assert.equal(app.state.scale, 1.2);
      assert.equal(env.elements.scale.value, "1.2");

      await sleep(180);

      const scaleCalls = env.nativeCalls.filter(c => c.command === "set_visual_scale");
      assert.equal(scaleCalls.length, 1, "应发起一次 set_visual_scale 原生调用");
      assert.deepEqual(scaleCalls[0].payload, { scale: 1.2 });
      const saves = env.nativeCalls.filter(c => c.command === "save_pet_state");
      assert.equal(saves.length, 1, "共用缩放提交应保存一次状态");
      assert.equal(saves[0].payload.state.scale, 1.2);
    } finally {
      app.dispose();
    }
  });

  await t.test("2. 连续缩放：debounce 内发送多个值，最终提交最新值，不形成多次原生提交", async () => {
    const { app, env, onPanelAction } = await createApp();
    try {
      env.nativeCalls.length = 0;
      onPanelAction({ payload: { action: "set-scale", value: 1.1 } });
      await sleep(40);
      onPanelAction({ payload: { action: "set-scale", value: 1.25 } });
      await sleep(40);
      onPanelAction({ payload: { action: "set-scale", value: 1.35 } });

      assert.equal(app.state.scale, 1.35);

      await sleep(180);

      const scaleCalls = env.nativeCalls.filter(c => c.command === "set_visual_scale");
      assert.equal(scaleCalls.length, 1, "多次连续输入应在 debounce 后仅提交一次最终缩放");
      assert.deepEqual(scaleCalls[0].payload, { scale: 1.35 });
    } finally {
      app.dispose();
    }
  });

  await t.test("3. 透明度接入共用行为：状态正确，走已有调度，不额外发起原生缩放", async () => {
    const { app, env, onPanelAction } = await createApp();
    try {
      env.nativeCalls.length = 0;
      onPanelAction({ payload: { action: "set-opacity", value: 0.75 } });
      assert.equal(app.state.opacity, 0.75);
      assert.equal(env.elements.opacity.value, "0.75");

      await sleep(580);

      const scaleCalls = env.nativeCalls.filter(c => c.command === "set_visual_scale");
      assert.equal(scaleCalls.length, 0, "透明度调整不应触发 set_visual_scale");
      const saves = env.nativeCalls.filter(c => c.command === "save_pet_state");
      assert.equal(saves.length, 1, "透明度应沿已有保存调度保存一次");
      assert.equal(saves[0].payload.state.opacity, 0.75);
    } finally {
      app.dispose();
    }
  });

  await t.test("4. 数值边界：核对正常值和低于/高于上下限的值，与窗口方法一致，非数字不更新", async () => {
    const { app, env, onPanelAction } = await createApp();
    try {
      // 下限 clamp (SCALE_MIN = 0.75)
      onPanelAction({ payload: { action: "set-scale", value: 0.1 } });
      assert.equal(app.state.scale, 0.75);

      // 上限 clamp (SCALE_MAX = 1.45)
      onPanelAction({ payload: { action: "set-scale", value: 3.0 } });
      assert.equal(app.state.scale, 1.45);

      // 透明度下限 clamp (0.55)
      onPanelAction({ payload: { action: "set-opacity", value: 0.2 } });
      assert.equal(app.state.opacity, 0.55);

      // 透明度上限 clamp (1.0)
      onPanelAction({ payload: { action: "set-opacity", value: 1.5 } });
      assert.equal(app.state.opacity, 1.0);

      // 保持原有类型检查：非数字不触发
      const scaleBefore = app.state.scale;
      onPanelAction({ payload: { action: "set-scale", value: "invalid" } });
      assert.equal(app.state.scale, scaleBefore);

      const opacityBefore = app.state.opacity;
      onPanelAction({ payload: { action: "set-opacity", value: null } });
      assert.equal(app.state.opacity, opacityBefore);
    } finally {
      app.dispose();
    }
  });

  await t.test("5. 退出后的待提交：连续缩放 debounce 尚未到期时 dispose，不残留并取消提交", async () => {
    const { app, env, onPanelAction } = await createApp();
    try {
      env.nativeCalls.length = 0;
      onPanelAction({ payload: { action: "set-scale", value: 1.4 } });
      assert.equal(app.state.scale, 1.4);

      // 在 120ms debounce 到期前立即 dispose
      await sleep(20);
      app.dispose();

      // 跨越原本的 debounce 期限
      await sleep(180);

      const scaleCalls = env.nativeCalls.filter(c => c.command === "set_visual_scale");
      assert.equal(scaleCalls.length, 0, "dispose 后应清除 timer，不应再发起原生提交");
    } finally {
      app.dispose();
    }
  });
});
