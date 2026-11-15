import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { commonjsOptions: { include: [/node_modules/, /response-validator\.js$/], transformMixedEsModules: true } },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});