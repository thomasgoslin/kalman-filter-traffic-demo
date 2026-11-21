import {defineConfig} from "cf/config";

export default defineConfig({
  worker: {
    name: "kalman-filter-traffic-demo",
    compatibilityDate: "2026-11-21",
    workersDev: true,
    assets: {notFoundHandling: "single-page-application"},
  },
});
