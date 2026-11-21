import {bindings, defineConfig} from "cf/config";

export default defineConfig({
  worker: {
    name: "kalman-filter",
    compatibilityDate: "2026-11-21",
    entrypoint: "./site_access/pin.js",
    workersDev: true,
    previewUrls: false,
    assets: {notFoundHandling: "single-page-application", runWorkerFirst: true},
    env: {
      ASSETS: bindings.assets(),
      SITE_PIN: bindings.secret(),
      SESSION_SIGNING_KEY: bindings.secret(),
      PIN_RATE_LIMITER: bindings.rateLimit({namespace: "83021642", simple: {limit: 30, period: 60}}),
    },
  },
});
