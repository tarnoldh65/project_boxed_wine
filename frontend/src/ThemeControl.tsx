import { Monitor, Moon, Sun } from 'lucide-react'
import { useState } from 'react'
import { applyTheme, storedTheme, type Theme } from './theme'

const THEMES = [
  ['light', 'Light', Sun],
  ['dark', 'Dark', Moon],
  ['system', 'System', Monitor],
] as const

export function ThemeControl() {
  const [theme, setTheme] = useState<Theme>(storedTheme)

  function choose(next: Theme) {
    applyTheme(next)
    setTheme(next)
  }

  return (
    <div className="segmented" role="radiogroup" aria-label="Theme">
      {THEMES.map(([value, label, Icon]) => (
        <label key={value} title={label}>
          <input
            type="radio"
            name="theme"
            aria-label={label}
            checked={theme === value}
            onChange={() => choose(value)}
          />
          <Icon aria-hidden size={16} />
        </label>
      ))}
    </div>
  )
}
