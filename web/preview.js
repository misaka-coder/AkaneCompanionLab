const appEl = document.getElementById("preview-app");
const spriteEl = document.getElementById("preview-sprite");
const clockEl = document.getElementById("preview-clock");
const dialogueEl = document.getElementById("dialogue-line");
const speakerNameEl = document.getElementById("speaker-name");
const bgmLabelEl = document.getElementById("bgm-label");
const menuToggleEl = document.getElementById("menu-toggle");
const menuPanelEl = document.getElementById("menu-panel");
const inputToggleEl = document.getElementById("input-toggle");
const nextLineEl = document.getElementById("next-line");
const composerEl = document.getElementById("preview-form");
const inputEl = document.getElementById("preview-input");

const bgFrames = [
  document.querySelector(".vn-bg-a"),
  document.querySelector(".vn-bg-b"),
];

const scenes = {
  morning_classroom: {
    time: "morning",
    background: "/assets/backgrounds/morning.jpg",
    speaker: "Akane",
    bgm: "BGM: 晨光教室",
    lines: [
      "早呀，主人。今天的空气很干净，连阳光都像刚醒过来一样。",
      "要是你愿意，我们可以把第一句招呼，也说得像早晨一样轻一点。",
    ],
  },
  evening_classroom: {
    time: "evening",
    background: "/assets/backgrounds/evening.jpg",
    speaker: "Akane",
    bgm: "BGM: 黄昏教室",
    lines: [
      "喂，那边的笨蛋，总算肯回来了呀。今天的夕阳这么好看，不陪我多待一会儿吗？",
      "这种快要落下去的光，总会让我想把说出口的话也放慢一点。",
    ],
  },
  night_room: {
    time: "night",
    background: "/assets/backgrounds/night.jpg",
    speaker: "Akane",
    bgm: "BGM: 深夜房间",
    lines: [
      "已经很晚了喵。要是你愿意，我可以陪你把今天最后一点心事也慢慢说完。",
      "深夜最适合小声讲话，因为连空气都会替我们把秘密藏起来。",
    ],
  },
};

const sprites = {
  normal: "/assets/characters/normal.png",
  happy: "/assets/characters/happy.png",
  caring: "/assets/characters/caring.png",
  angry: "/assets/characters/angry.png",
};

let activeSceneKey = "evening_classroom";
let activeEmotionKey = "normal";
let activeBgIndex = 0;
let lineIndex = 0;
let typingToken = 0;
let sending = false;

function updateClock() {
  if (!clockEl) return;
  const now = new Date();
  clockEl.textContent = now.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function setBackground(url, timeKey) {
  appEl.dataset.time = timeKey;
  const nextIndex = activeBgIndex === 0 ? 1 : 0;
  bgFrames[nextIndex].style.backgroundImage = `url("${url}")`;
  bgFrames[nextIndex].classList.add("is-visible");
  bgFrames[activeBgIndex].classList.remove("is-visible");
  activeBgIndex = nextIndex;
}

async function typewrite(text) {
  typingToken += 1;
  const token = typingToken;
  dialogueEl.textContent = "";
  for (let i = 0; i < text.length; i += 1) {
    if (token !== typingToken) return;
    dialogueEl.textContent = text.slice(0, i + 1);
    await new Promise((resolve) => setTimeout(resolve, 18));
  }
}

function setSending(flag) {
  sending = flag;
  inputEl.disabled = flag;
  inputToggleEl.disabled = flag;
  nextLineEl.disabled = flag;
  menuToggleEl.disabled = flag;
  document.querySelectorAll(".scene-chip, .emotion-chip").forEach((button) => {
    button.disabled = flag;
  });
}

async function applyScene(sceneKey, resetLine = true) {
  const scene = scenes[sceneKey];
  if (!scene) return;
  activeSceneKey = sceneKey;
  if (resetLine) lineIndex = 0;
  setBackground(scene.background, scene.time);
  speakerNameEl.textContent = scene.speaker;
  bgmLabelEl.textContent = scene.bgm;
  document.querySelectorAll(".scene-chip").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.scene === sceneKey);
  });
  await typewrite(scene.lines[lineIndex] || scene.lines[0]);
}

function applyEmotion(emotionKey) {
  if (!sprites[emotionKey]) return;
  activeEmotionKey = emotionKey;
  spriteEl.src = sprites[emotionKey];
  document.querySelectorAll(".emotion-chip").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.emotion === emotionKey);
  });
}

async function nextLine() {
  const scene = scenes[activeSceneKey];
  lineIndex = (lineIndex + 1) % scene.lines.length;
  await typewrite(scene.lines[lineIndex]);
}

menuToggleEl.addEventListener("click", () => {
  const hidden = menuPanelEl.hasAttribute("hidden");
  if (hidden) {
    menuPanelEl.removeAttribute("hidden");
  } else {
    menuPanelEl.setAttribute("hidden", "");
  }
});

inputToggleEl.addEventListener("click", () => {
  inputEl.focus();
});

nextLineEl.addEventListener("click", async () => {
  await nextLine();
});

document.querySelectorAll(".scene-chip").forEach((button) => {
  button.addEventListener("click", async () => {
    await applyScene(button.dataset.scene);
  });
});

document.querySelectorAll(".emotion-chip").forEach((button) => {
  button.addEventListener("click", () => {
    applyEmotion(button.dataset.emotion);
  });
});

composerEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = inputEl.value.trim();
  if (!text || sending) return;
  setSending(true);
  inputEl.value = "";
  await typewrite("……Akane 正在听你说。");

  try {
    const response = await fetch(`/think_once?t=${Date.now()}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({
        user_id: "vn_preview_session",
        real_user_id: "vn_preview_user",
        message: text,
      }),
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const payload = await response.json();
    speakerNameEl.textContent = "Akane";
    if (payload.emotion) {
      applyEmotion(payload.emotion);
    }
    await typewrite(payload.speech || "喵呜，我刚才有点发呆了，你再和我说一次好不好？");
  } catch (error) {
    inputEl.value = text;
    await typewrite(`呜……这一句我没有顺利接住。(${error})`);
  } finally {
    setSending(false);
  }
});

updateClock();
if (clockEl) {
  setInterval(updateClock, 60_000);
}
applyEmotion(activeEmotionKey);
applyScene(activeSceneKey);
