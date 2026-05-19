import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    hmr: {
      overlay: false,  // 断连时不弹黑色错误遮罩
    },
    proxy: {
      "/api": {
        target: "http://localhost:8002",
        changeOrigin: true,
        timeout: 120000,
      },
    },
  },
});
