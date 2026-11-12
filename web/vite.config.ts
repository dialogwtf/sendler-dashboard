/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = fileURLToPath(new URL(".", import.meta.url));

// A static site: `vite build` → dist, no server part. The API lives at api.sendler.xyz and
// allows CORS for http://localhost:5173, so dev talks to prod directly — no proxy.
export default defineConfig({
    plugins: [tailwind(), react()],
    build: {
        target: "es2022",
        rollupOptions: {
            output: {
                // Only the core every page needs goes to `vendor`; other libraries follow the code
                // that imports them, so a lazy part (search dialog: cmdk) keeps its library out of
                // the first screen.
                manualChunks: (id) => {
                    if (!id.includes("node_modules")) return undefined;
                    if (id.includes("lightweight-charts") || id.includes("fancy-canvas")) return "vendor-chart";
                    if (id.includes("lucide-react")) return "vendor-icons";
                    if (id.includes("@tanstack")) return "vendor-query";
                    if (/node_modules\/(react|react-dom|scheduler|react-router|react-router-dom|i18next|react-i18next|tailwind-merge|clsx)\//.test(id.replaceAll("\\", "/"))) return "vendor";
                    return undefined;
                },
            },
        },
    },
    server: {
		port: 5173,
		strictPort: true,
		allowedHosts: ["sirenically-adust-asia.ngrok-free.dev"],
	},
    resolve: { alias: { "@": path.resolve(__dirname, "src") } },
    test: {
        environment: "node",
        include: ["src/**/*.test.ts"],
    },
});
