import React, { createContext, useContext, useEffect, useState } from "react";
import axios from "axios";
import { authService } from "../services/auth";
import type { User } from "../types";

interface AuthContextValue {
  user: User | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const stored = authService.getStoredUser();
    const token = authService.getToken();
    if (stored && token) {
      setUser(stored);
      authService.getMe().then(setUser).catch((err) => {
        // Only drop the session when the token is actually rejected (401).
        // On a network blip or server error, keep the stored session so the
        // user isn't logged out mid-work.
        if (axios.isAxiosError(err) && err.response?.status === 401) {
          authService.logout();
          setUser(null);
        }
      }).finally(() => setIsLoading(false));
    } else {
      setIsLoading(false);
    }
  }, []);

  const login = async (email: string, password: string) => {
    const data = await authService.login(email, password);
    setUser(data.user);
  };

  const logout = () => {
    authService.logout();
    setUser(null);
  };

  const refreshUser = async () => {
    const fresh = await authService.getMe();
    setUser(fresh);
  };

  return (
    <AuthContext.Provider value={{ user, isLoading, login, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
