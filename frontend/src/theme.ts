export type Theme = 'light' | 'dark' | 'system'

const THEME_KEY = 'casemgr.theme'

export function storedTheme(): Theme {
  try {
    const theme = localStorage.getItem(THEME_KEY)
    return theme === 'light' || theme === 'dark' ? theme : 'system'
  } catch {
    return 'system'
  }
}

/** Apply a theme now and remember it. "system" follows the operating system through CSS. */
export function applyTheme(theme: Theme) {
  if (theme === 'system') delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = theme
  try {
    localStorage.setItem(THEME_KEY, theme)
  } catch {
    // Remembering the theme is a convenience only.
  }
}
