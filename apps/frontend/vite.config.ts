import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

import { publicProjectConfig } from "./public-project-config";

export default defineConfig({
  ...publicProjectConfig(fileURLToPath(new URL("../../config", import.meta.url))),
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "@agent-py/api-contracts": fileURLToPath(
        new URL("../../packages/api-contracts/src/index.ts", import.meta.url)
      )
    }
  },
  test: {
    include: ["tests/**/*.test.ts"]
  }
});
