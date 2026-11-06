/** Placeholder lines while a frame loads. */
export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" role="img" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} />
      ))}
    </div>
  )
}
