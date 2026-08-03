import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";

export default defineConfig({
  base: "./",
  plugins: [react()],
  resolve: {
    extensions: [".js", ".jsx", ".ts", ".tsx", ".json"],
    alias: {
      "vaul@1.1.2": "vaul",
      "sonner@2.0.3": "sonner",
      "recharts@2.15.2": "recharts",
      "react-resizable-panels@2.1.7": "react-resizable-panels",
      "react-hook-form@7.55.0": "react-hook-form",
      "react-day-picker@8.10.1": "react-day-picker",
      "next-themes@0.4.6": "next-themes",
      "lucide-react@0.487.0": "lucide-react",
      "input-otp@1.4.2": "input-otp",
      "embla-carousel-react@8.6.0": "embla-carousel-react",
      "cmdk@1.1.1": "cmdk",
      "class-variance-authority@0.7.1": "class-variance-authority",
      "@radix-ui/react-tooltip@1.1.8": "@radix-ui/react-tooltip",
      "@radix-ui/react-toggle@1.1.2": "@radix-ui/react-toggle",
      "@radix-ui/react-toggle-group@1.1.2": "@radix-ui/react-toggle-group",
      "@radix-ui/react-tabs@1.1.3": "@radix-ui/react-tabs",
      "@radix-ui/react-switch@1.1.3": "@radix-ui/react-switch",
      "@radix-ui/react-slot@1.1.2": "@radix-ui/react-slot",
      "@radix-ui/react-slider@1.2.3": "@radix-ui/react-slider",
      "@radix-ui/react-separator@1.1.2": "@radix-ui/react-separator",
      "@radix-ui/react-select@2.1.6": "@radix-ui/react-select",
      "@radix-ui/react-scroll-area@1.2.3": "@radix-ui/react-scroll-area",
      "@radix-ui/react-radio-group@1.2.3": "@radix-ui/react-radio-group",
      "@radix-ui/react-progress@1.1.2": "@radix-ui/react-progress",
      "@radix-ui/react-popover@1.1.6": "@radix-ui/react-popover",
      "@radix-ui/react-navigation-menu@1.2.5":
        "@radix-ui/react-navigation-menu",
      "@radix-ui/react-menubar@1.1.6": "@radix-ui/react-menubar",
      "@radix-ui/react-label@2.1.2": "@radix-ui/react-label",
      "@radix-ui/react-hover-card@1.1.6": "@radix-ui/react-hover-card",
      "@radix-ui/react-dropdown-menu@2.1.6": "@radix-ui/react-dropdown-menu",
      "@radix-ui/react-dialog@1.1.6": "@radix-ui/react-dialog",
      "@radix-ui/react-context-menu@2.2.6": "@radix-ui/react-context-menu",
      "@radix-ui/react-collapsible@1.1.3": "@radix-ui/react-collapsible",
      "@radix-ui/react-checkbox@1.1.4": "@radix-ui/react-checkbox",
      "@radix-ui/react-avatar@1.1.3": "@radix-ui/react-avatar",
      "@radix-ui/react-aspect-ratio@1.1.2": "@radix-ui/react-aspect-ratio",
      "@radix-ui/react-alert-dialog@1.1.6": "@radix-ui/react-alert-dialog",
      "@radix-ui/react-accordion@1.2.3": "@radix-ui/react-accordion",
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    target: "esnext",
    outDir: "build",
    rollupOptions: {
      output: {
        /**
         * Split the vendor libraries out of the app chunk.
         *
         * Honest scope note: this app ships as a Tauri bundle, loading assets from
         * the local filesystem rather than over a network. So unlike a web app,
         * this does NOT reduce download size or benefit from CDN caching, and the
         * total bytes parsed at startup are essentially unchanged — every chunk
         * below is a static import of the entry, so all of them load eagerly.
         *
         * What it does buy:
         *  - React and Radix churn far less often than app code, so incremental
         *    rebuilds and dev-server reloads touch much less output;
         *  - it clears Vite's >500 kB chunk warning, which was masking genuine
         *    regressions in the noise;
         *  - it makes `npm run build` output legible, so a future dependency
         *    bloat shows up against a named chunk instead of vanishing into one
         *    570 kB blob.
         *
         * The real startup win came from F5 (lazy modals), which removes code
         * from the eager graph entirely rather than merely relabelling it.
         */
        manualChunks(id) {
          // ONLY the React runtime is split out.
          //
          // Measured, not assumed: an earlier version of this config also split
          // @radix-ui and a catch-all "vendor" chunk. That made startup WORSE.
          // Radix modules used only by the lazily-loaded modals had been living
          // inside those lazy chunks; hoisting them into an eagerly-loaded vendor
          // chunk pulled them back into the startup path:
          //
          //   eager bytes  563.46 kB -> 593.43 kB  (+29.97 kB)
          //   eager gzip   169.36 kB -> 177.38 kB  (+8.02 kB)
          //
          // React is different: it is genuinely needed eagerly by every entry
          // path, so naming it costs nothing and keeps it out of the app chunk's
          // rebuild surface. Everything else is left to Rollup, which correctly
          // keeps modal-only dependencies inside the modal chunks.
          if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) {
            return "vendor-react";
          }
          return undefined;
        },
      },
    },
  },
  server: {
    port: 3000,
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
});
