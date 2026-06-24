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
        panel: resolve(__dirname, "panel.html"),
        controlCenterLab: resolve(__dirname, "control-center-lab.html"),
        settingsCompatibilityRedirect: resolve(__dirname, "settings.html"),
        shop: resolve(__dirname, "shop.html"),
        workshop: resolve(__dirname, "workshop.html"),
        workspace: resolve(__dirname, "workspace.html")
      }
    }
  }
});
