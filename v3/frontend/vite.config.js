import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// In development the API runs on port 8002; the built app is served by FastAPI on the same origin.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5175, proxy: { "/api": "http://localhost:8002" } },
});
