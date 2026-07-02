declare global {
  interface Window {
    AIBOT_API_BASE_URL?: string;
    AIBOT_ACTIVE_API_BASE_URL?: string;
  }
}

export const apiBaseUrl = (
  window.AIBOT_API_BASE_URL ||
  localStorage.getItem('aibot.apiBaseUrl') ||
  'http://127.0.0.1:8766'
).replace(/\/$/, '');

window.AIBOT_ACTIVE_API_BASE_URL = apiBaseUrl;

export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${apiBaseUrl}${path.startsWith('/') ? path : `/${path}`}`;
}

export async function api<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(apiUrl(path), {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok || (payload as { error?: string }).error) {
    throw new Error((payload as { error?: string }).error || `HTTP ${response.status}`);
  }
  return payload as T;
}
