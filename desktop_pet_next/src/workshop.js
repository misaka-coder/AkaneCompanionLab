import { invoke } from "@tauri-apps/api/core";
import { emit, emitTo, listen } from "@tauri-apps/api/event";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { createWorkshopApplication } from "./workshop/application.js";
import "./workshop.css";

const isTauriRuntime = Boolean(window.__TAURI_INTERNALS__);
const application = createWorkshopApplication({
  platform: {
    invoke, emit, emitTo, listen, tauriFetch, isTauriRuntime,
    appWindow: isTauriRuntime ? getCurrentWindow() : null,
  },
});

application.start();
