import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // Self-contained server bundle, so the runtime image needs no node_modules.
  output: "standalone",
  // NOTE: MARKETRADAR_API_URL is deliberately NOT declared under `env`. That option
  // *inlines* values at build time, which would bake the build machine's API URL into the
  // image. Pages are server components, so `process.env.MARKETRADAR_API_URL` in lib/api.ts
  // is read from the real environment on each request — which is what lets the same image
  // run against a local backend, a container network, or a deployed one.
};

export default config;
