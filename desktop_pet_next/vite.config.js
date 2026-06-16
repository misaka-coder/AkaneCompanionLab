import { resolve } from "node:path";

import { defineConfig } from "vite";

const creatorKitDir = resolve(__dirname, "../desktop_pet_creator_kit");

export default defineConfig({
  server: {
    fs: {
      allow: [resolve(__dirname), creatorKitDir]
    }
  },
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        controlCenterLab: resolve(__dirname, "control-center-lab.html"),
        settingsCompatibilityRedirect: resolve(__dirname, "settings.html"),
        workshop: resolve(__dirname, "workshop.html"),
        workspace: resolve(__dirname, "workspace.html")
      }
    }
  }
});
