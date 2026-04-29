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
        settings: resolve(__dirname, "settings.html"),
        workspace: resolve(__dirname, "workspace.html")
      }
    }
  }
});
