import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { emit, emitTo, listen } from "@tauri-apps/api/event";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { currentMonitor, getCurrentWindow, primaryMonitor } from "@tauri-apps/api/window";
import voicePcmWorkletUrl from './voice-pcm-worklet.js?url&no-inline';
import { createPetApplication } from './pet/application.js';
import { createPlatform } from './pet/platform.js';
import './styles.css';

const bundledCharacterAssets = import.meta.glob("./assets/characters/猫娘/*.{png,jpg,jpeg,webp}", {
  eager: true,
  import: "default",
  query: "?url"
});

const platform = createPlatform({
  window, document, invoke, emit, emitTo, listen, tauriFetch,
  currentMonitor, getCurrentWindow, primaryMonitor, convertFileSrc, voicePcmWorkletUrl
});
const pet = createPetApplication({ platform, bundledCharacterAssets });
void pet.start();
