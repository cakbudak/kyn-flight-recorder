import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: {
    outDir: "app",
    emptyOutDir: true,
    sourcemap: true,
    target: "es2022",
    rollupOptions: {
      output: {
        entryFileNames: "assets/studio-[hash].js",
        chunkFileNames: "assets/chunk-[hash].js",
        assetFileNames: "assets/studio-[hash][extname]",
        manualChunks(id) {
          if (id.includes("node_modules/@xyflow") || id.includes("node_modules/d3-")) return "flow-canvas";
          if (id.includes("node_modules/react")) return "react-runtime";
          return undefined;
        }
      }
    }
  }
});
