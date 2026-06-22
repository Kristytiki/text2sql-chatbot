import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy backend calls. UI uses relative paths like /chat/... and Vite forwards
// them to the FastAPI server in dev. In prod, set VITE_API_BASE or serve UI
// from the same origin as the API.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    // Accept the random *.trycloudflare.com hostname the tunnel assigns, so
    // the dev server doesn't reject tunneled requests with a host-check 403.
    allowedHosts: [".trycloudflare.com"],
    proxy: {
      "/chat":   "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
});
