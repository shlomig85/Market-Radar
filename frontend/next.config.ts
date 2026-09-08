import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // The API base URL is read at request time, not baked in at build time, so the same
  // image can run against different backends.
  env: { MARKETRADAR_API_URL: process.env.MARKETRADAR_API_URL ?? "http://localhost:8000" },
};

export default config;
