import type { Bar } from './bars'

/**
 * Horizontal bars with the value at each bar's end. It is a real table, so screen
 * readers get the figures directly, and a selectable label filters on that row.
 */
export function BarChart({
  caption,
  bars,
  onSelect,
}: {
  caption: string
  bars: Bar[]
  onSelect?: (key: string) => void
}) {
  const largest = Math.max(...bars.map((bar) => bar.value), 1)
  return (
    <table className="bars">
      <caption>{caption}</caption>
      <tbody>
        {bars.length === 0 && (
          <tr>
            <td>None</td>
          </tr>
        )}
        {bars.map(({ key, label, value, tone, selected }) => (
          <tr key={key} className={selected ? 'selected' : undefined}>
            <th scope="row">
              {onSelect ? (
                <button
                  type="button"
                  aria-pressed={!!selected}
                  aria-label={`${label}: ${value}`}
                  onClick={() => onSelect(key)}
                >
                  {label}
                </button>
              ) : (
                label
              )}
            </th>
            <td>
              <span className="bar-track">
                <span
                  className={`bar bar-${tone ?? 'accent'}`}
                  style={{ width: `${(value / largest) * 100}%` }}
                />
              </span>
              <span className="bar-value">{value}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
