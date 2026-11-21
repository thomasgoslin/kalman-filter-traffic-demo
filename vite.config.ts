import {cloudflare} from "@cloudflare/vite-plugin";
import {defineConfig} from "vite";

export default defineConfig({
  publicDir: "outputs/classroom",
  plugins: [cloudflare()],
});
