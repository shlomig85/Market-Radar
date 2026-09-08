import Link from "next/link";

export default function NotFound() {
  return (
    <div className="border border-dashed border-line px-4 py-10 text-center">
      <p className="text-sm text-muted">No such theme.</p>
      <p className="mt-1 text-xs text-faint">
        A theme exists only once its signals have accelerated against their own baseline.
      </p>
      <Link href="/" className="mt-4 inline-block text-xs text-signal hover:underline">
        ← back to all themes
      </Link>
    </div>
  );
}
