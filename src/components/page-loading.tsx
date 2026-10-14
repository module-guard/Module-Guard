export function PageLoading() {
  return (
    <div
      className="animate-pulse space-y-6"
      role="status"
      aria-label="Loading page"
    >
      <div className="h-8 w-64 rounded bg-slate-200" />
      <div className="h-4 w-3/4 rounded bg-slate-200" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[1, 2, 3, 4].map((n) => (
          <div key={n} className="panel h-36 bg-slate-100" />
        ))}
      </div>
      <div className="panel h-64 bg-slate-100" />
      <span className="sr-only">Loading security intelligence…</span>
    </div>
  );
}
