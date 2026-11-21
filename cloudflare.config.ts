import {bindings, defineConfig} from "cf/config";

export default defineConfig({
  worker: {
    name: "kalman-filter",
    compatibilityDate: "2026-11-21",
    entrypoint: "./classroom_edge/pin.js",
    workersDev: true,
    previewUrls: false,
    assets: {notFoundHandling: "single-page-application", runWorkerFirst: true},
    env: {
      ASSETS: bindings.assets(),
      CLASSROOM_PIN: bindings.secret(),
      CLASSROOM_SESSION_KEY: bindings.secret(),
      PIN_ATTEMPTS: bindings.rateLimit({namespace: "83021642", simple: {limit: 30, period: 60}}),
    },
  },
});
