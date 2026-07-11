import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, clearSession, getCachedUser, getToken, saveSession, UserPublic } from "./api";

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

  const refresh = useCallback(async () => {
    const token = await getToken();
    if (!token) {
      setUser(null);
      return;
    }
    try {
      const me = await api.me();
      setUser(me);
      await saveSession(token, me);
    } catch {
      await clearSession();
      setUser(null);
    }
  }, []);

  useEffect(() => {
    (async () => {
      const cached = await getCachedUser();
      if (cached) setUser(cached);
      await refresh();
      setLoading(false);
    })();
  }, [refresh]);

  const setSession = useCallback(async (token: string, next: UserPublic) => {
    await saveSession(token, next);
    setUser(next);
  }, []);

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
