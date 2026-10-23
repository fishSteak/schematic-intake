import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/verdicts.json": "http://127.0.0.1:8765",
      "/evidence": "http://127.0.0.1:8765",
      "/stress_paths": "http://127.0.0.1:8765",
      "/sch": "http://127.0.0.1:8765",
      "/api": "http://127.0.0.1:8765",
    },
  },
});
