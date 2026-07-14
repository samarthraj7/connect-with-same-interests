import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, clearSession, getCachedUser, getToken, saveSession, UserPublic } from "./api";
import { useTheme } from "./theme-context";

type AuthCtx = {
  user: UserPublic | null;
  loading: boolean;
  refresh: () => Promise<void>;
  setSession: (token: string, user: UserPublic) => Promise<void>;
  signOut: () => Promise<void>;
};

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserPublic | null>(null);
  const [loading, setLoading] = useState(true);
  const { setMode } = useTheme();

  const applyTheme = useCallback(
    (next: UserPublic | null) => {
      const t = next?.settings?.theme;
      if (t === "dark" || t === "light") setMode(t);
    },
    [setMode]
  );

  const refresh = useCallback(async () => {
    const token = await getToken();
    if (!token) {
      setUser(null);
      return;
    }
    try {
      const me = await api.me();
      setUser(me);
      applyTheme(me);
      await saveSession(token, me);
    } catch {
      await clearSession();
      setUser(null);
    }
  }, [applyTheme]);

  useEffect(() => {
    (async () => {
      const cached = await getCachedUser();
      if (cached) {
        setUser(cached);
        applyTheme(cached);
      }
      await refresh();
      setLoading(false);
    })();
  }, [refresh, applyTheme]);

  const setSession = useCallback(
    async (token: string, next: UserPublic) => {
      await saveSession(token, next);
      setUser(next);
      applyTheme(next);
    },
    [applyTheme]
  );

  const signOut = useCallback(async () => {
    await clearSession();
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loading, refresh, setSession, signOut }),
    [user, loading, refresh, setSession, signOut]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth outside provider");
  return ctx;
}
