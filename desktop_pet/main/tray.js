const { Tray, Menu, nativeImage, app } = require("electron");
const path = require("path");
const { loadSettings, updateSettings, PET_SCALE_VALUES } = require("./settings-store");

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
  const petScale = Number(settings.petScale || 1);
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
    {
      label: "状态预览器",
      click: () => {
        if (!mainWindow.isVisible()) mainWindow.show();
        sendRendererEvent(mainWindow, "debug-panel-toggle");
      },
    },
    {
      label: "设置",
      click: () => {
        if (!mainWindow.isVisible()) mainWindow.show();
        sendRendererEvent(mainWindow, "settings-panel-toggle");
      },
    },
    {
      label: "大小",
      submenu: PET_SCALE_VALUES.map((value) => ({
        label: `${Math.round(value * 100)}%`,
        type: "radio",
        checked: Math.abs(petScale - value) < 0.001,
        click: () => {
          const next = updateSettings({ petScale: value });
          if (onSettingsChanged) onSettingsChanged(next);
          if (refreshMenu) refreshMenu();
        },
      })),
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

function sendRendererEvent(mainWindow, channel, payload) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send(channel, payload);
}

module.exports = { createTray };
