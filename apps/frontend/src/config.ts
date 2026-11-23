export const API_BASE_URL = resolveApiBaseUrl(import.meta.env.PUBLIC_PROJECT_API_BASE_URL);

export function resolveApiBaseUrl(apiBaseUrl: string): string {
  return apiBaseUrl.trim().replace(/\/+$/, "");
}
