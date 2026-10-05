/**
 * Tiny wrapper around localStorage for the JWT pair. Kept in its own module
 * so the storage mechanism (localStorage today) can be swapped later
 * (e.g. for an httpOnly-cookie-based flow, see ARCHITECTURE.md) without
 * touching every call site.
 */
const ACCESS_KEY = "rb_access_token";
const REFRESH_KEY = "rb_refresh_token";

export const tokenStorage = {
  getAccessToken: () => localStorage.getItem(ACCESS_KEY),
  getRefreshToken: () => localStorage.getItem(REFRESH_KEY),
  setTokens: (accessToken: string, refreshToken: string) => {
    localStorage.setItem(ACCESS_KEY, accessToken);
    localStorage.setItem(REFRESH_KEY, refreshToken);
  },
  clear: () => {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};
