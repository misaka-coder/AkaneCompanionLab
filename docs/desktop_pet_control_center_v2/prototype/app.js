(() => {
  "use strict";

  const root = document.documentElement;
  const shell = document.querySelector(".shell");
  const toast = document.querySelector("#toast");
  const roleImage = "/desktop_pet_next/src/assets/characters/%E7%8C%AB%E5%A8%98/%E6%AD%A3%E5%B8%B8.png";
  const sceneImage = "/desktop_pet_next/src/assets/control-center-lab/backgrounds/sky-city-balcony.png";
  const sample = Object.freeze({ avatar: roleImage, portrait: roleImage, background: sceneImage, userAvatar: "", accent: "#79a6ff", opacity: "76", shade: "34", blur: "22", font: "system", themeMode: "dark" });
  const currentAssets = { avatar: sample.avatar, portrait: sample.portrait, background: sample.background, userAvatar: sample.userAvatar };
  const framing = {
    avatar: { x: 0, y: 0, scale: 1 },
    portrait: { x: 0, y: 0, scale: 1 },
    background: { x: 0, y: 0, scale: 1.02 },
    userAvatar: { x: 0, y: 0, scale: 1 }
  };
  const slotLabels = { avatar: "角色头像", portrait: "主立绘", background: "控制中心背景", userAvatar: "用户头像" };
  const pageTitles = { overview: "今天想让她做什么？", appearance: "把这里变成她的空间", chat: "聊天记录与实时状态" };
  const presenceStates = {
    listening: { label: "专注聆听", phase: "正在聆听", symbol: "◌" },
    thinking: { label: "正在思考", phase: "组织回复中", symbol: "…" },
    tool: { label: "正在使用工具", phase: "任务执行中", symbol: "✦" },
    happy: { label: "心情不错", phase: "回复已完成", symbol: "♡" }
  };
  let toastTimer = 0;
  let mediaPending = false;
  let playing = false;
  let portraitVisible = true;
  let themeMode = sample.themeMode;
  let activeCalibration = "avatar";
  let draftFrame = { ...framing.avatar };
  let dragPoint = null;
  let chatPending = false;
  const objectUrls = new Set();
  const systemTheme = window.matchMedia("(prefers-color-scheme: light)");

  function clamp(value, min, max) { return Math.min(max, Math.max(min, value)); }

  function showToast(message) {
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.add("is-visible");
    toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 2600);
  }

  function setPage(page) {
    const target = document.querySelector(`#page-${page}`);
    if (!target) return;
    document.querySelectorAll(".page").forEach((item) => item.classList.toggle("is-active", item === target));
    document.querySelectorAll("[data-page-target]").forEach((button) => {
      const active = button.dataset.pageTarget === page;
      button.classList.toggle("is-active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    shell.dataset.page = page;
    document.querySelector("#page-title").textContent = pageTitles[page] || pageTitles.overview;
  }

  document.querySelectorAll("[data-page-target]").forEach((button) => button.addEventListener("click", () => setPage(button.dataset.pageTarget)));
  document.querySelectorAll("[data-sim-action]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.classList.contains("is-pending")) return;
      const label = button.dataset.simAction;
      const original = button.innerHTML;
      button.classList.add("is-pending");
      button.setAttribute("aria-busy", "true");
      button.textContent = `${label}处理中…`;
      document.querySelector("#activity-label").textContent = `${label}请求中`;
      window.setTimeout(() => {
        button.classList.remove("is-pending");
        button.removeAttribute("aria-busy");
        button.innerHTML = original;
        document.querySelector("#activity-label").textContent = "空闲，随时可以开始";
        showToast(`交互原型：生产接入时由真实桥接确认“${label}”结果。`);
      }, 720);
    });
  });

  const playToggle = document.querySelector("#play-toggle");
  playToggle.addEventListener("click", () => {
    if (mediaPending) return;
    mediaPending = true;
    playToggle.classList.add("is-pending");
    playToggle.setAttribute("aria-busy", "true");
    document.querySelector("#music-status").textContent = playing ? "暂停请求中" : "播放请求中";
    window.setTimeout(() => {
      mediaPending = false;
      playing = !playing;
      playToggle.classList.remove("is-pending");
      playToggle.classList.toggle("is-playing", playing);
      playToggle.removeAttribute("aria-busy");
      playToggle.setAttribute("aria-pressed", String(playing));
      playToggle.setAttribute("aria-label", playing ? "暂停" : "播放");
      document.querySelector("#music-status").textContent = playing ? "正在播放" : "已暂停";
      showToast(playing ? "播放状态已确认。" : "暂停状态已确认。");
    }, 520);
  });

  function updateElementsSource(selectors, url) {
    selectors.forEach((selector) => document.querySelectorAll(selector).forEach((element) => { element.src = url; }));
  }

  function setUserAvatar(url) {
    document.querySelectorAll("[data-user-avatar]").forEach((image) => { image.src = url; image.hidden = !url; });
    document.querySelectorAll("[data-user-avatar-fallback]").forEach((fallback) => { fallback.hidden = Boolean(url); });
  }

  function fileToPreview(file, assetKey, selectors) {
    if (!file || !file.type.startsWith("image/")) { showToast("请选择 PNG、JPEG 或 WebP 图片。"); return; }
    const url = URL.createObjectURL(file);
    objectUrls.add(url);
    currentAssets[assetKey] = url;
    if (selectors.length) updateElementsSource(selectors, url);
    if (assetKey === "userAvatar") setUserAvatar(url);
    showToast(`已在当前会话预览 ${file.name}；没有写入角色包。`);
  }

  document.querySelector("#avatar-file").addEventListener("change", (event) => fileToPreview(event.target.files?.[0], "avatar", ["#asset-avatar-preview", "#hero-avatar", "#rail-avatar", "#chat-role-avatar", "[data-role-avatar]"]));
  document.querySelector("#portrait-file").addEventListener("change", (event) => fileToPreview(event.target.files?.[0], "portrait", ["#hero-portrait", "#preview-portrait", "#chat-presence-portrait"]));
  document.querySelector("#background-file").addEventListener("change", (event) => fileToPreview(event.target.files?.[0], "background", ["#scene-background", "#preview-background", ".presence-background"]));
  document.querySelector("#user-avatar-file").addEventListener("change", (event) => fileToPreview(event.target.files?.[0], "userAvatar", []));

  document.querySelector("#toggle-portrait").addEventListener("click", (event) => {
    portraitVisible = !portraitVisible;
    document.querySelector("#preview-portrait").classList.toggle("is-hidden", !portraitVisible);
    document.querySelector("#hero-portrait").style.opacity = portraitVisible ? "1" : "0";
    event.currentTarget.textContent = portraitVisible ? "隐藏立绘" : "显示立绘";
  });

  function hexToRgb(hex) {
    const value = Number.parseInt(String(hex).replace("#", ""), 16);
    return `${(value >> 16) & 255} ${(value >> 8) & 255} ${value & 255}`;
  }

  function applyThemeMode() {
    const resolved = themeMode === "system" ? (systemTheme.matches ? "light" : "dark") : themeMode;
    root.dataset.theme = resolved;
    document.querySelectorAll("[data-theme-mode]").forEach((button) => {
      const active = button.dataset.themeMode === themeMode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }

  function applyTheme() {
    const accent = document.querySelector("#accent-control").value;
    const opacity = document.querySelector("#opacity-control").value;
    const shade = document.querySelector("#shade-control").value;
    const blur = document.querySelector("#blur-control").value;
    const font = document.querySelector("#font-control").value;
    const fonts = { system: 'Inter, "Microsoft YaHei UI", "PingFang SC", system-ui, sans-serif', soft: '"Microsoft YaHei UI", "PingFang SC", "Segoe UI", system-ui, sans-serif', serif: 'Georgia, "Noto Serif CJK SC", "Songti SC", serif' };
    root.style.setProperty("--accent", accent);
    root.style.setProperty("--accent-rgb", hexToRgb(accent));
    root.style.setProperty("--accent-strong", accent);
    root.style.setProperty("--panel-alpha", String(Number(opacity) / 100));
    root.style.setProperty("--scene-shade", String(Number(shade) / 100));
    root.style.setProperty("--glass-blur", `${blur}px`);
    root.style.setProperty("--font-ui", fonts[font] || fonts.system);
    document.querySelector("#opacity-output").textContent = `${opacity}%`;
    document.querySelector("#shade-output").textContent = `${shade}%`;
    document.querySelector("#blur-output").textContent = `${blur}px`;
    applyThemeMode();
  }

  document.querySelectorAll("[data-theme-mode]").forEach((button) => button.addEventListener("click", () => {
    themeMode = button.dataset.themeMode;
    applyThemeMode();
    showToast(themeMode === "system" ? "界面将跟随系统明暗主题。" : `已切换为${themeMode === "light" ? "浅色" : "深色"}预览。`);
  }));
  systemTheme.addEventListener("change", () => { if (themeMode === "system") applyThemeMode(); });
  ["accent-control", "opacity-control", "shade-control", "blur-control", "font-control"].forEach((id) => document.querySelector(`#${id}`).addEventListener("input", applyTheme));

  function applyFraming() {
    const avatar = framing.avatar;
    const portrait = framing.portrait;
    const background = framing.background;
    const userAvatar = framing.userAvatar;
    root.style.setProperty("--avatar-x", `${avatar.x}%`);
    root.style.setProperty("--avatar-y", `${avatar.y}%`);
    root.style.setProperty("--avatar-scale", String(avatar.scale));
    root.style.setProperty("--portrait-x", `${portrait.x}%`);
    root.style.setProperty("--portrait-y", `${portrait.y}%`);
    root.style.setProperty("--portrait-scale", String(portrait.scale));
    root.style.setProperty("--presence-scale", String(1.58 + (portrait.scale - 1) * 0.4));
    root.style.setProperty("--background-x", `${background.x}%`);
    root.style.setProperty("--background-y", `${background.y}%`);
    root.style.setProperty("--background-scale", String(background.scale));
    root.style.setProperty("--user-avatar-x", `${userAvatar.x}%`);
    root.style.setProperty("--user-avatar-y", `${userAvatar.y}%`);
    root.style.setProperty("--user-avatar-scale", String(userAvatar.scale));
  }

  function resetFraming() {
    Object.assign(framing.avatar, { x: 0, y: 0, scale: 1 });
    Object.assign(framing.portrait, { x: 0, y: 0, scale: 1 });
    Object.assign(framing.background, { x: 0, y: 0, scale: 1.02 });
    Object.assign(framing.userAvatar, { x: 0, y: 0, scale: 1 });
    applyFraming();
  }

  document.querySelector("#reset-theme").addEventListener("click", () => {
    document.querySelector("#accent-control").value = sample.accent;
    document.querySelector("#opacity-control").value = sample.opacity;
    document.querySelector("#shade-control").value = sample.shade;
    document.querySelector("#blur-control").value = sample.blur;
    document.querySelector("#font-control").value = sample.font;
    themeMode = sample.themeMode;
    Object.assign(currentAssets, { avatar: sample.avatar, portrait: sample.portrait, background: sample.background, userAvatar: "" });
    updateElementsSource(["#scene-background", "#preview-background", ".presence-background"], sample.background);
    updateElementsSource(["#hero-portrait", "#preview-portrait", "#chat-presence-portrait"], sample.portrait);
    updateElementsSource(["#asset-avatar-preview", "#hero-avatar", "#rail-avatar", "#chat-role-avatar", "[data-role-avatar]"], sample.avatar);
    setUserAvatar("");
    portraitVisible = true;
    document.querySelector("#preview-portrait").classList.remove("is-hidden");
    document.querySelector("#hero-portrait").style.opacity = "1";
    document.querySelector("#toggle-portrait").textContent = "隐藏立绘";
    resetFraming();
    applyTheme();
    showToast("已恢复样例主题与构图；没有修改生产设置。");
  });

  const calibrationModal = document.querySelector("#calibration-modal");
  const calibrationPreview = document.querySelector("#calibration-preview");
  const calibrationImage = document.querySelector("#calibration-image");
  const calibrationZoom = document.querySelector("#calibration-zoom");

  function renderCalibration() {
    calibrationPreview.dataset.slot = activeCalibration;
    calibrationImage.src = currentAssets[activeCalibration];
    calibrationPreview.style.setProperty("--calibration-x", `${draftFrame.x}%`);
    calibrationPreview.style.setProperty("--calibration-y", `${draftFrame.y}%`);
    calibrationPreview.style.setProperty("--calibration-scale", String(draftFrame.scale));
    calibrationZoom.value = String(Math.round(draftFrame.scale * 100));
    document.querySelector("#calibration-zoom-output").textContent = `${Math.round(draftFrame.scale * 100)}%`;
    document.querySelector("#calibration-x").textContent = String(Math.round(draftFrame.x));
    document.querySelector("#calibration-y").textContent = String(Math.round(draftFrame.y));
    document.querySelector("#calibration-slot-label").textContent = slotLabels[activeCalibration];
  }

  function openCalibration(slot) {
    if (!currentAssets[slot]) { showToast("请先为这个槽位选择一张图片，再调整构图。"); return; }
    activeCalibration = slot;
    draftFrame = { ...framing[slot] };
    calibrationModal.hidden = false;
    document.body.classList.add("has-modal");
    renderCalibration();
  }
  function closeCalibration() { calibrationModal.hidden = true; document.body.classList.remove("has-modal"); dragPoint = null; }

  document.querySelectorAll("[data-calibrate]").forEach((button) => button.addEventListener("click", () => openCalibration(button.dataset.calibrate)));
  document.querySelectorAll("[data-close-calibration]").forEach((button) => button.addEventListener("click", closeCalibration));
  document.querySelector("#calibration-reset").addEventListener("click", () => { draftFrame = { x: 0, y: 0, scale: activeCalibration === "background" ? 1.02 : 1 }; renderCalibration(); });
  document.querySelector("#calibration-apply").addEventListener("click", () => {
    framing[activeCalibration] = { ...draftFrame };
    applyFraming();
    closeCalibration();
    showToast(`已应用${slotLabels[activeCalibration]}构图；原图片没有被裁剪。`);
  });
  calibrationZoom.addEventListener("input", () => { draftFrame.scale = Number(calibrationZoom.value) / 100; renderCalibration(); });
  calibrationPreview.addEventListener("pointerdown", (event) => { dragPoint = { x: event.clientX, y: event.clientY }; calibrationPreview.setPointerCapture(event.pointerId); });
  calibrationPreview.addEventListener("pointermove", (event) => {
    if (!dragPoint) return;
    const rect = calibrationPreview.getBoundingClientRect();
    draftFrame.x = clamp(draftFrame.x + ((event.clientX - dragPoint.x) / rect.width) * 100, -45, 45);
    draftFrame.y = clamp(draftFrame.y + ((event.clientY - dragPoint.y) / rect.height) * 100, -45, 45);
    dragPoint = { x: event.clientX, y: event.clientY };
    renderCalibration();
  });
  calibrationPreview.addEventListener("pointerup", () => { dragPoint = null; });
  calibrationPreview.addEventListener("pointercancel", () => { dragPoint = null; });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !calibrationModal.hidden) closeCalibration(); });

  function setPresence(state) {
    const next = presenceStates[state] || presenceStates.listening;
    document.querySelector(".presence-card").dataset.state = state;
    document.querySelector("#presence-phase").textContent = next.phase;
    document.querySelector("#expression-label").textContent = next.label;
    document.querySelector("#expression-symbol").textContent = next.symbol;
    document.querySelectorAll("[data-presence-state]").forEach((button) => button.classList.toggle("is-active", button.dataset.presenceState === state));
  }
  document.querySelectorAll("[data-presence-state]").forEach((button) => button.addEventListener("click", () => setPresence(button.dataset.presenceState)));
  document.querySelector("#toggle-presence").addEventListener("click", (event) => {
    const collapsed = document.querySelector("#chat-layout").classList.toggle("is-presence-collapsed");
    event.currentTarget.textContent = collapsed ? "展开角色镜头" : "收起角色镜头";
  });

  function currentTime() { return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date()); }
  function createMessage(role, text) {
    const article = document.createElement("article");
    article.className = `message-row ${role === "user" ? "is-user" : "is-assistant"}`;
    const avatar = document.createElement("span");
    avatar.className = "message-avatar";
    if (role === "user" && !currentAssets.userAvatar) {
      const fallback = document.createElement("span");
      fallback.className = "user-avatar-fallback";
      fallback.textContent = "你";
      avatar.append(fallback);
    } else {
      const image = document.createElement("img");
      image.src = role === "user" ? currentAssets.userAvatar : currentAssets.avatar;
      image.alt = role === "user" ? "用户头像" : "角色头像";
      image.dataset[role === "user" ? "userAvatar" : "roleAvatar"] = "";
      avatar.append(image);
    }
    const content = document.createElement("div");
    const name = document.createElement("span");
    name.className = "sender-name";
    name.textContent = role === "user" ? "你" : "当前角色";
    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    bubble.textContent = text;
    const time = document.createElement("time");
    time.textContent = currentTime();
    content.append(name, bubble, time);
    article.append(avatar, content);
    return article;
  }

  const chatForm = document.querySelector("#chat-form");
  const chatInput = document.querySelector("#chat-input");
  const messageScroll = document.querySelector("#chat-messages");
  const typingRow = document.querySelector("#typing-row");
  chatInput.addEventListener("input", () => { chatInput.style.height = "auto"; chatInput.style.height = `${Math.min(chatInput.scrollHeight, 120)}px`; });
  chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = chatInput.value.trim();
    if (!text || chatPending) return;
    messageScroll.insertBefore(createMessage("user", text), typingRow);
    chatInput.value = "";
    chatInput.style.height = "auto";
    chatPending = true;
    typingRow.hidden = false;
    setPresence("thinking");
    messageScroll.scrollTop = messageScroll.scrollHeight;
    window.setTimeout(() => {
      typingRow.hidden = true;
      messageScroll.insertBefore(createMessage("assistant", "这是一条聊天区交互预览：正式接入后会显示真实流式回复，并与桌宠气泡共享同一轮内容。"), typingRow);
      chatPending = false;
      setPresence("happy");
      messageScroll.scrollTop = messageScroll.scrollHeight;
      window.setTimeout(() => setPresence("listening"), 1500);
    }, 1050);
  });

  window.addEventListener("beforeunload", () => objectUrls.forEach((url) => URL.revokeObjectURL(url)));
  applyTheme();
  applyFraming();
  setPresence("listening");
})();
