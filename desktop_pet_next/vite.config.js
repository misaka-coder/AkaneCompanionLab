import { resolve } from "node:path";

import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const creatorKitDir = resolve(__dirname, "../desktop_pet_creator_kit");

export default defineConfig({
  plugins: [vue()],
  server: {
    fs: {
      allow: [resolve(__dirname), creatorKitDir]
    },
    watch: {
      ignored: ["**/src-tauri/target/**"]
    }
  },
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        scene: resolve(__dirname, "scene.html"),
        panel: resolve(__dirname, "panel.html"),
        controlCenterLab: resolve(__dirname, "control-center-lab.html"),
        controlCenterV2CompatibilityRedirect: resolve(__dirname, "control-center-v2.html"),
        settingsCompatibilityRedirect: resolve(__dirname, "settings.html"),
        shop: resolve(__dirname, "shop.html"),
        workshop: resolve(__dirname, "workshop.html"),
        workspace: resolve(__dirname, "workspace.html")
      }
    }
  }
});
