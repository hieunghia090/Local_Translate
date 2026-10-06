const KEY = 'lt.theme';
export type Theme = 'auto' | 'light' | 'dark';

export function storedTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return v === 'light' || v === 'dark' ? v : 'auto';
  } catch {
    return 'auto';
  }
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === 'auto') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', theme);
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* trình duyệt chặn localStorage: chỉ không nhớ được lựa chọn */
  }
}

export function applyStoredTheme(): void {
  applyTheme(storedTheme());
}
