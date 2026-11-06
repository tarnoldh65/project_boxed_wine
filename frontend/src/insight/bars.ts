import type { Tone } from '../tones'

export interface Bar {
  key: string
  label: string
  value: number
  tone?: Tone
  selected?: boolean
}

/** Counts as bars in a set order; keys the order does not know come last. */
export function orderedBars(
  counts: Record<string, number>,
  order: string[],
  label: (key: string) => string = (key) => key,
): Bar[] {
  const keys = [...order.filter((key) => key in counts), ...Object.keys(counts).filter((key) => !order.includes(key))]
  return keys.map((key) => ({ key, label: label(key), value: counts[key] }))
}
