/**
 * Single axios instance for the whole app. A request interceptor attaches
 * the JWT access token; a response interceptor transparently refreshes an
 * expired access token exactly once and retries the original request, so
 * individual pages/components never have to think about token expiry.
 */
import axios, { type AxiosError, type InternalAxiosRequestConfig } from "axios";
import { tokenStorage } from "./tokenStorage";

const baseURL = import.meta.env.VITE_API_URL as string | undefined;

if (!baseURL) {
  throw new Error(
    "VITE_API_URL is not set. Create frontend/.env (copy frontend/.env.example) " +
      "with VITE_API_URL pointing at your running FastAPI backend, then restart `npm run dev`."
  );
}

export const apiClient = axios.create({ baseURL });

let onUnauthorized: (() => void) | null = null;
export function registerUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

apiClient.interceptors.request.use((config) => {
  const token = tokenStorage.getAccessToken();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

let refreshPromise: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = tokenStorage.getRefreshToken();
  if (!refreshToken) return null;

  try {
    const { data } = await axios.post(`${baseURL}/api/auth/refresh`, { refresh_token: refreshToken });
    tokenStorage.setTokens(data.access_token, data.refresh_token);
    return data.access_token as string;
  } catch {
    return null;
  }
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined;

    if (error.response?.status === 401 && original && !original._retried) {
      original._retried = true;

      refreshPromise ??= refreshAccessToken().finally(() => {
        refreshPromise = null;
      });
      const newToken = await refreshPromise;

      if (newToken) {
        original.headers.set("Authorization", `Bearer ${newToken}`);
        return apiClient(original);
      }

      tokenStorage.clear();
      onUnauthorized?.();
    }

    return Promise.reject(error);
  }
);
