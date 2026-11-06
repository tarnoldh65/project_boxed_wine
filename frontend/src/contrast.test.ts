/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { expect, test } from 'vitest'

const CSS = readFileSync('src/index.css', 'utf8')
const AA = 4.5

/** A token's [light, dark] colours from its light-dark() definition. */
function token(name: string): [string, string] {
  const line = CSS.split('\n').find((l) => l.trim().startsWith(`--${name}: `))
  const match = line?.match(/light-dark\((#[0-9a-f]{6}), (#[0-9a-f]{6})\)/)
  if (!match) throw new Error(`no token --${name}`)
  return [match[1], match[2]]
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const channel = parseInt(hex.slice(i, i + 2), 16) / 255
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(a: string, b: string): number {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (high + 0.05) / (low + 0.05)
}

test.each([
  ['critical-fg', 'critical-bg'],
  ['high-fg', 'high-bg'],
  ['medium-fg', 'medium-bg'],
  ['low-fg', 'low-bg'],
  ['ok-fg', 'ok-bg'],
  ['neutral-fg', 'neutral-bg'],
  ['text', 'bg'],
  ['text', 'surface'],
  ['text-muted', 'bg'],
  ['text-muted', 'surface'],
  ['accent', 'surface'],
  ['on-accent', 'accent-fill'],
  // Tiles, hovered rows, and selected rows and chips.
  ['text', 'surface-raised'],
  ['text-muted', 'surface-raised'],
  ['accent', 'surface-raised'],
  ['text', 'accent-soft'],
  ['text-muted', 'accent-soft'],
  ['accent', 'accent-soft'],
])('%s on %s meets WCAG AA in both themes', (foreground, background) => {
  const [fgLight, fgDark] = token(foreground)
  const [bgLight, bgDark] = token(background)
  expect(contrast(fgLight, bgLight)).toBeGreaterThanOrEqual(AA)
  expect(contrast(fgDark, bgDark)).toBeGreaterThanOrEqual(AA)
})
