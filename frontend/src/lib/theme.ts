export type ThemeMode = 'dark' | 'light'

export const THEME_STORAGE_KEY = 'cutline.theme'
export const DEFAULT_THEME: ThemeMode = 'dark'

export function isThemeMode(value: unknown): value is ThemeMode {
  return value === 'dark' || value === 'light'
}

export function readStoredTheme(): ThemeMode {
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY)
    if (isThemeMode(raw)) return raw
  } catch {
    // Ignore private-mode / blocked storage.
  }
  return DEFAULT_THEME
}

export function applyTheme(theme: ThemeMode) {
  document.documentElement.dataset.theme = theme
  document.documentElement.style.colorScheme = theme
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) {
    meta.setAttribute('content', theme === 'dark' ? '#202321' : '#f5f6f4')
  }
}

export function persistTheme(theme: ThemeMode) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Ignore private-mode / blocked storage.
  }
}

export function setTheme(theme: ThemeMode) {
  applyTheme(theme)
  persistTheme(theme)
}

export function initTheme(): ThemeMode {
  const theme = readStoredTheme()
  applyTheme(theme)
  return theme
}
