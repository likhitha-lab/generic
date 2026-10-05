import { apiClient } from "./client";
import { tokenStorage } from "./tokenStorage";
import type { TokenResponse, User } from "../types/auth";

export async function register(email: string, password: string, fullName: string): Promise<User> {
  const { data } = await apiClient.post<User>("/api/auth/register", { email, password, full_name: fullName });
  return data;
}

export async function login(email: string, password: string): Promise<User> {
  const { data } = await apiClient.post<TokenResponse>("/api/auth/login", { email, password });
  tokenStorage.setTokens(data.access_token, data.refresh_token);
  return fetchMe();
}

export async function fetchMe(): Promise<User> {
  const { data } = await apiClient.get<User>("/api/auth/me");
  return data;
}

export function logout(): void {
  tokenStorage.clear();
}
