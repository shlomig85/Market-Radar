import type { Metadata } from "next";
import Link from "next/link";

import { api } from "@/lib/api";
import { DataModeBadge } from "@/components/primitives";
import "./globals.css";

export const metadata: Metadata = {
  title: "Market Radar",
  description: "Emerging-theme investment intelligence",
};

/**
 * Global provenance banner.
 *
 * If any capability is DEMO or UNAVAILABLE, every page says so before the user reads a
 * single number. Provenance is chrome-level, not a footnote.
 */
async function ProvenanceBar() {
  const providers = await api.providers();
  if (!providers) {
    return (
      <div className="border-b border-against/40 bg-against/10 px-4 py-1.5 text-xs text-against">
        API unreachable — start the backend with <code className="font-mono">make api</code>.
      </div>
    );
  }
  const demo = providers.filter((p) => p.mode === "DEMO");
  const missing = providers.filter((p) => p.mode === "UNAVAILABLE");

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line bg-raised px-4 py-1.5 text-xs">
      {demo.length > 0 && (
        <span className="flex items-center gap-2">
          <DataModeBadge mode="DEMO" />
          <span className="text-muted">
            {demo.length} of {providers.length} data capabilities are served by the synthetic
            development corpus. Companies, publishers and statements are invented.
          </span>
        </span>
      )}
      {missing.map((provider) => (
        <span key={provider.capability} className="flex items-center gap-2">
          <DataModeBadge mode="UNAVAILABLE" />
          <span className="text-faint">
            {provider.capability.replace("_", " ").toLowerCase()} not configured
          </span>
        </span>
      ))}
    </div>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="border-b border-line bg-panel">
          <div className="flex items-baseline gap-6 px-4 py-3">
            <Link href="/" className="flex items-baseline gap-2">
              <span className="text-sm font-semibold tracking-[0.2em] text-signal">
                MARKET RADAR
              </span>
              <span className="label">emerging-theme intelligence</span>
            </Link>
            <nav className="flex items-baseline gap-4 text-xs">
              <Link href="/trending" className="text-muted hover:text-signal">
                Trending stocks
              </Link>
              <Link href="/" className="text-muted hover:text-signal">
                Themes
              </Link>
            </nav>
            <span className="ml-auto text-[10px] text-faint">
              research output · not investment advice
            </span>
          </div>
        </header>
        <ProvenanceBar />
        <main className="mx-auto max-w-[1400px] px-4 py-6">{children}</main>
        <footer className="mx-auto max-w-[1400px] px-4 pb-10 pt-4 text-[11px] leading-relaxed text-faint">
          Market Radar ranks research priority. Scores are not forecasts of any security&rsquo;s
          price, and nothing here is investment advice. Every figure is traceable to the
          evidence that produced it; anything not measured is labelled unavailable rather than
          estimated.
        </footer>
      </body>
    </html>
  );
}
