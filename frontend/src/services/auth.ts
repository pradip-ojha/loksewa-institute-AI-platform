import api from "./api";
import type { TokenResponse, User } from "../types";

export const authService = {
  async login(email: string, password: string): Promise<TokenResponse> {
    const { data } = await api.post<TokenResponse>("/api/auth/login", { email, password });
    localStorage.setItem("access_token", data.access_token);
    localStorage.setItem("user", JSON.stringify(data.user));
    return data;
  },

  async getMe(): Promise<User> {
    const { data } = await api.get<User>("/api/auth/me");
    localStorage.setItem("user", JSON.stringify(data));
    return data;
  },

  logout(): void {
    localStorage.removeItem("access_token");
    localStorage.removeItem("user");
  },

  getStoredUser(): User | null {
    const raw = localStorage.getItem("user");
    return raw ? JSON.parse(raw) : null;
  },

  getToken(): string | null {
    return localStorage.getItem("access_token");
  },
};
