// Runs before first paint so a saved theme never flashes the wrong colours. See src/theme.ts.
try {
  const theme = localStorage.getItem('casemgr.theme')
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme
} catch {
  // No storage: follow the operating system.
}
