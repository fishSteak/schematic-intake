import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import { normalizeBasePath } from './src/urls.js';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  const base = normalizeBasePath(env.VITE_BASE_PATH || '/');
  const prefix = base.replace(/\/$/, '');
  const proxy = Object.fromEntries(['/api', '/reports/', '/verdicts.json', '/evidence', '/stress_paths', '/sch', '/delivery_coverage.json'].map(path => [
    prefix + path,
    { target: 'http://127.0.0.1:8765', rewrite: value => prefix ? value.slice(prefix.length) : value },
  ]));
  return {
    base,
    plugins: [vue()],
    server: {
      port: 5173,
      proxy,
    },
  };
});
