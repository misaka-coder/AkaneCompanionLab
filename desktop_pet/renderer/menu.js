import { ContextMenu } from "./ui/ContextMenu.js";

const root = document.getElementById("menu-root");
let settings = {};
let unsubSettings = null;

const menu = new ContextMenu(root, {
  getSettings: () => settings,
  onAction: (action, value) => {
    window.akaneAPI?.menuAction?.(action, value);
  },
});

function renderWithSettings(s) {
  settings = s || {};
  if (menu.isVisible()) {
    menu.hide();
  }
  menu.showPinned();
}

window.akaneAPI?.onMenuInit?.((initSettings) => {
  renderWithSettings(initSettings);

  if (!unsubSettings) {
    unsubSettings = window.akaneAPI?.onSettingsChanged?.((newSettings) => {
      renderWithSettings(newSettings);
    });
  }
});
