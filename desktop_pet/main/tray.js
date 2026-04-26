const { Tray, Menu, nativeImage, app } = require("electron");
const path = require("path");
const { OPACITY_VALUES, loadSettings, updateSettings } = require("./settings-store");

function createTray(mainWindow, { onSettingsChanged } = {}) {
  const trayIcon = createTrayIcon();

  const tray = new Tray(trayIcon);

  const refreshMenu = () => {
    const contextMenu = buildContextMenu(mainWindow, { onSettingsChanged, refreshMenu });
    tray.setContextMenu(contextMenu);
    return contextMenu;
  };

  tray.setToolTip("Akane");
  tray.refreshMenu = refreshMenu;
  tray.popupMenu = () => {
    const contextMenu = refreshMenu();
    contextMenu.popup({ window: mainWindow });
  };
  refreshMenu();

  return tray;
}

function buildContextMenu(mainWindow, { onSettingsChanged, refreshMenu } = {}) {
  const settings = loadSettings();
  return Menu.buildFromTemplate([
    {
      label: "显示 / 隐藏",
      click: () => {
        if (mainWindow.isVisible()) {
          mainWindow.hide();
        } else {
          mainWindow.show();
          mainWindow.focus();
        }
        if (refreshMenu) refreshMenu();
      },
    },
    {
      label: "重载立绘",
      click: () => {
        sendRendererEvent(mainWindow, "reload-sprite");
      },
    },
    {
      label: "手边物品",
      click: () => {
        if (!mainWindow.isVisible()) mainWindow.show();
        mainWindow.focus();
        sendRendererEvent(mainWindow, "workspace-panel-toggle");
      },
    },
    { type: "separator" },
    {
      label: "设置后端地址",
      click: () => requestRendererPrompt(mainWindow, {
        key: "backendUrl",
        title: "设置后端地址",
        value: settings.backendUrl,
      }),
    },
    {
      label: "设置服装名",
      click: () => requestRendererPrompt(mainWindow, {
        key: "outfit",
        title: "设置服装名",
        value: settings.outfit,
      }),
    },
    {
      label: "透明度",
      submenu: OPACITY_VALUES.map((opacity) => ({
        label: `${Math.round(opacity * 100)}%`,
        type: "radio",
        checked: settings.opacity === opacity,
        click: () => {
          const nextSettings = updateSettings({ opacity });
          mainWindow.setOpacity(nextSettings.opacity);
          if (onSettingsChanged) onSettingsChanged(nextSettings);
          if (refreshMenu) refreshMenu();
        },
      })),
    },
    {
      label: "语音播放 开/关",
      type: "checkbox",
      checked: settings.voiceEnabled,
      click: () => {
        const nextSettings = updateSettings({ voiceEnabled: !settings.voiceEnabled });
        if (onSettingsChanged) onSettingsChanged(nextSettings);
        if (refreshMenu) refreshMenu();
      },
    },
    {
      label: "语音输入 开/关",
      type: "checkbox",
      checked: settings.voiceInputEnabled,
      click: () => {
        const nextSettings = updateSettings({ voiceInputEnabled: !settings.voiceInputEnabled });
        if (onSettingsChanged) onSettingsChanged(nextSettings);
        if (refreshMenu) refreshMenu();
      },
    },
    {
      label: "语音输入快捷键：Ctrl+Shift+Space",
      enabled: false,
    },
    { type: "separator" },
    {
      label: "桌面上下文 开/关",
      type: "checkbox",
      checked: settings.desktopContextEnabled,
      click: () => {
        const nextSettings = updateSettings({ desktopContextEnabled: !settings.desktopContextEnabled });
        if (onSettingsChanged) onSettingsChanged(nextSettings);
        if (refreshMenu) refreshMenu();
      },
    },
    {
      label: "剪贴板上下文 开/关",
      type: "checkbox",
      enabled: settings.desktopContextEnabled,
      checked: settings.clipboardContextEnabled,
      click: () => {
        const nextSettings = updateSettings({ clipboardContextEnabled: !settings.clipboardContextEnabled });
        if (onSettingsChanged) onSettingsChanged(nextSettings);
        if (refreshMenu) refreshMenu();
      },
    },
    {
      label: "上下文只在发送消息时临时附带",
      enabled: false,
    },
    { type: "separator" },
    {
      label: "退出",
      click: () => app.quit(),
    },
  ]);
}

function createTrayIcon() {
  const iconPath = path.join(__dirname, "..", "assets", "icon.ico");
  let trayIcon = nativeImage.createFromPath(iconPath);
  if (trayIcon.isEmpty()) {
    trayIcon = nativeImage.createFromDataURL(
      "data:image/svg+xml;charset=utf-8," +
        encodeURIComponent(
          '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"><rect width="16" height="16" rx="4" fill="#f6b08e"/><circle cx="8" cy="7" r="4" fill="#fff6f0"/><path d="M5.2 6.8h.01M10.8 6.8h.01" stroke="#6b3d34" stroke-width="1.4" stroke-linecap="round"/><path d="M6.1 9.4c1.1.8 2.7.8 3.8 0" fill="none" stroke="#6b3d34" stroke-width="1" stroke-linecap="round"/></svg>'
        )
    );
  }
  return trayIcon.isEmpty() ? nativeImage.createEmpty() : trayIcon.resize({ width: 16, height: 16 });
}

function requestRendererPrompt(mainWindow, payload) {
  if (!mainWindow.isVisible()) mainWindow.show();
  mainWindow.focus();
  sendRendererEvent(mainWindow, "settings-prompt", payload);
}

function sendRendererEvent(mainWindow, channel, payload) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send(channel, payload);
}

module.exports = { createTray };
