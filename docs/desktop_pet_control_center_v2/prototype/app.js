(() => {
  "use strict";

  const root = document.documentElement;
  const shell = document.querySelector(".shell");
  const toast = document.querySelector("#toast");
  const sample = Object.freeze({
    avatar: "/desktop_pet_next/src/assets/characters/%E7%8C%AB%E5%A8%98/%E6%AD%A3%E5%B8%B8.png",
    portrait: "/desktop_pet_next/src/assets/characters/%E7%8C%AB%E5%A8%98/%E6%AD%A3%E5%B8%B8.png",
    background: "/desktop_pet_next/src/assets/control-center-lab/backgrounds/sky-city-balcony.png",
    accent: "#79a6ff",
    opacity: "76",
    shade: "34",
    blur: "22",
    font: "system"
  });
  let toastTimer = 0;
  let mediaPending = false;
  let playing = false;
  let portraitVisible = true;
  const objectUrls = new Set();

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
    document.querySelector("#page-title").textContent = page === "overview" ? "今天想让她做什么？" : "把这里变成她的空间";
  }

  document.querySelectorAll("[data-page-target]").forEach((button) => {
    button.addEventListener("click", () => setPage(button.dataset.pageTarget));
  });

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

  function fileToPreview(file, targets) {
    if (!file || !file.type.startsWith("image/")) {
      showToast("请选择 PNG、JPEG 或 WebP 图片。");
      return;
    }
    const url = URL.createObjectURL(file);
    objectUrls.add(url);
    targets.forEach((selector) => {
      const element = document.querySelector(selector);
      if (element) element.src = url;
    });
    showToast(`已在当前会话预览 ${file.name}；没有写入角色包。`);
  }

  document.querySelector("#avatar-file").addEventListener("change", (event) => {
    fileToPreview(event.target.files?.[0], ["#asset-avatar-preview", "#hero-avatar", "#rail-avatar"]);
  });
  document.querySelector("#portrait-file").addEventListener("change", (event) => {
    fileToPreview(event.target.files?.[0], ["#hero-portrait", "#preview-portrait"]);
  });
  document.querySelector("#background-file").addEventListener("change", (event) => {
    fileToPreview(event.target.files?.[0], ["#scene-background", "#preview-background"]);
  });

  document.querySelector("#toggle-portrait").addEventListener("click", (event) => {
    portraitVisible = !portraitVisible;
    document.querySelector("#preview-portrait").classList.toggle("is-hidden", !portraitVisible);
    document.querySelector("#hero-portrait").style.opacity = portraitVisible ? "1" : "0";
    event.currentTarget.textContent = portraitVisible ? "隐藏立绘" : "显示立绘";
  });

  function hexToRgb(hex) {
    const clean = String(hex).replace("#", "");
    const value = Number.parseInt(clean, 16);
    return `${(value >> 16) & 255} ${(value >> 8) & 255} ${value & 255}`;
  }

  function applyTheme() {
    const accent = document.querySelector("#accent-control").value;
    const opacity = document.querySelector("#opacity-control").value;
    const shade = document.querySelector("#shade-control").value;
    const blur = document.querySelector("#blur-control").value;
    const font = document.querySelector("#font-control").value;
    const fonts = {
      system: 'Inter, "Microsoft YaHei UI", "PingFang SC", system-ui, sans-serif',
      soft: '"Microsoft YaHei UI", "PingFang SC", "Segoe UI", system-ui, sans-serif',
      serif: 'Georgia, "Noto Serif CJK SC", "Songti SC", serif'
    };
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
  }

  ["accent-control", "opacity-control", "shade-control", "blur-control", "font-control"].forEach((id) => {
    document.querySelector(`#${id}`).addEventListener("input", applyTheme);
  });

  document.querySelector("#reset-theme").addEventListener("click", () => {
    document.querySelector("#accent-control").value = sample.accent;
    document.querySelector("#opacity-control").value = sample.opacity;
    document.querySelector("#shade-control").value = sample.shade;
    document.querySelector("#blur-control").value = sample.blur;
    document.querySelector("#font-control").value = sample.font;
    ["#scene-background", "#preview-background"].forEach((selector) => { document.querySelector(selector).src = sample.background; });
    ["#hero-portrait", "#preview-portrait"].forEach((selector) => { document.querySelector(selector).src = sample.portrait; });
    ["#asset-avatar-preview", "#hero-avatar", "#rail-avatar"].forEach((selector) => { document.querySelector(selector).src = sample.avatar; });
    portraitVisible = true;
    document.querySelector("#preview-portrait").classList.remove("is-hidden");
    document.querySelector("#hero-portrait").style.opacity = "1";
    document.querySelector("#toggle-portrait").textContent = "隐藏立绘";
    applyTheme();
    showToast("已恢复样例主题；没有修改生产设置。");
  });

  window.addEventListener("beforeunload", () => objectUrls.forEach((url) => URL.revokeObjectURL(url)));
  applyTheme();
})();
