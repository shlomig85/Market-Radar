import type { Config } from "tailwindcss";

/**
 * Palette for a research terminal: near-black ground, restrained accents, and colour used
 * to carry meaning (direction, provenance, stance) rather than decoration.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ground: "#07090c",
        panel: "#0d1117",
        raised: "#141b24",
        line: "#1e2731",
        ink: "#e6edf3",
        muted: "#8b98a5",
        faint: "#5a6672",
        signal: "#4fd1c5",
        warn: "#f0a04b",
        against: "#e5646e",
        favour: "#5ec98a",
        demo: "#a78bfa",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
        sans: ["ui-sans-serif", "system-ui", "Inter", "sans-serif"],
      },
    },
  },
  plugins: [],
};
export default config;
