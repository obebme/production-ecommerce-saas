"use client";

import * as React from "react";
import {
  ApiError,
  authApi,
  cartApi,
  getCookieValue,
  registerAuthInvalidationListener,
  setMemoryCsrfToken,
} from "@/lib/api/client";
import type { CartDTO, UserDTO } from "@/lib/api/types";
import { clearAllGuestOrderTokens } from "@/lib/guest-order-memory";
import { useToast } from "@/components/ui/toast";

type MergedCartSubscriber = (mergedCart: CartDTO) => void;
type LogoutCartSubscriber = () => void;

interface AuthContextValue {
  user: UserDTO | null;
  isLoading: boolean;
  sessionExpired: boolean;
  accountDisabled: boolean;
  clearAuthNotices: () => void;
  refreshSession: () => Promise<UserDTO | null>;
  login: (email: string, password: string) => Promise<UserDTO>;
  register: (params: { email: string; password: string; fullName: string }) => Promise<UserDTO>;
  logout: () => Promise<void>;
  updateProfile: (fullName: string) => Promise<UserDTO>;
  subscribeMergedCart: (cb: MergedCartSubscriber) => () => void;
  subscribeLogout: (cb: LogoutCartSubscriber) => () => void;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const { toast } = useToast();
  const [user, setUser] = React.useState<UserDTO | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [sessionExpired, setSessionExpired] = React.useState(false);
  const [accountDisabled, setAccountDisabled] = React.useState(false);

  const userRef = React.useRef<UserDTO | null>(null);
  userRef.current = user;

  const mergedCartSubscribersRef = React.useRef<Set<MergedCartSubscriber>>(new Set());
  const logoutSubscribersRef = React.useRef<Set<LogoutCartSubscriber>>(new Set());

  const subscribeMergedCart = React.useCallback((cb: MergedCartSubscriber) => {
    mergedCartSubscribersRef.current.add(cb);
    return () => {
      mergedCartSubscribersRef.current.delete(cb);
    };
  }, []);

  const subscribeLogout = React.useCallback((cb: LogoutCartSubscriber) => {
    logoutSubscribersRef.current.add(cb);
    return () => {
      logoutSubscribersRef.current.delete(cb);
    };
  }, []);

  const clearAuthNotices = React.useCallback(() => {
    setSessionExpired(false);
    setAccountDisabled(false);
  }, []);

  React.useEffect(() => {
    registerAuthInvalidationListener((reason) => {
      const wasAuthenticated = userRef.current !== null;
      setUser(null);
      setMemoryCsrfToken(null);
      if (reason === "account_disabled") {
        setAccountDisabled(true);
        setSessionExpired(false);
        toast({
          title: "Account disabled",
          description: "Your account has been deactivated. Contact an administrator.",
          variant: "destructive",
        });
      } else {
        setSessionExpired(true);
        setAccountDisabled(false);
        if (wasAuthenticated) {
          toast({
            title: "Session expired",
            description: "Please sign in again to continue.",
            variant: "warning",
          });
        }
      }
    });

    return () => {
      registerAuthInvalidationListener(null);
    };
  }, [toast]);

  const hydrateSession = React.useCallback(async (signal?: AbortSignal): Promise<UserDTO | null> => {
    const hadCsrfCookie = Boolean(getCookieValue("ecom_csrf"));
    try {
      const res = await authApi.me({ skipAuthInvalidationHook: true, signal });
      setUser(res.user);
      setSessionExpired(false);
      setAccountDisabled(false);
      return res.user;
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return null;
      }
      setUser(null);
      setMemoryCsrfToken(null);
      if (err instanceof ApiError) {
        if (err.status === 403 && err.code === "AUTH_ACCOUNT_DISABLED") {
          setAccountDisabled(true);
        } else if (
          err.status === 401 &&
          err.code !== "AUTH_REQUIRED" &&
          (err.code === "AUTH_SESSION_EXPIRED" || hadCsrfCookie)
        ) {
          setSessionExpired(true);
        }
      }
      return null;
    } finally {
      setIsLoading(false);
    }
  }, []);

  React.useEffect(() => {
    const controller = new AbortController();
    void hydrateSession(controller.signal);
    return () => {
      controller.abort();
    };
  }, [hydrateSession]);

  const refreshSession = React.useCallback(async () => {
    return hydrateSession();
  }, [hydrateSession]);

  const mergeAfterAuth = React.useCallback(async () => {
    try {
      const merged = await cartApi.mergeCart();
      for (const subscriber of mergedCartSubscribersRef.current) {
        subscriber(merged);
      }
    } catch {
      // CartProvider will still attempt GET /cart when user state updates if merge failed transiently
    }
  }, []);

  const login = React.useCallback(
    async (email: string, password: string): Promise<UserDTO> => {
      const res = await authApi.login({ email: email.trim(), password });
      setSessionExpired(false);
      setAccountDisabled(false);
      setUser(res.user);
      await mergeAfterAuth();
      return res.user;
    },
    [mergeAfterAuth]
  );

  const register = React.useCallback(
    async ({
      email,
      password,
      fullName,
    }: {
      email: string;
      password: string;
      fullName: string;
    }): Promise<UserDTO> => {
      const res = await authApi.register({
        email: email.trim(),
        password,
        full_name: fullName.trim(),
      });
      setSessionExpired(false);
      setAccountDisabled(false);
      setUser(res.user);
      await mergeAfterAuth();
      return res.user;
    },
    [mergeAfterAuth]
  );

  const logout = React.useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // Clear local session state even if session was already expired server-side
    } finally {
      clearAllGuestOrderTokens();
      setUser(null);
      setMemoryCsrfToken(null);
      setSessionExpired(false);
      setAccountDisabled(false);
      for (const subscriber of logoutSubscribersRef.current) {
        subscriber();
      }
    }
  }, []);

  const updateProfile = React.useCallback(async (fullName: string): Promise<UserDTO> => {
    const res = await authApi.updateProfile({ full_name: fullName.trim() });
    setUser(res.user);
    return res.user;
  }, []);

  const value = React.useMemo<AuthContextValue>(
    () => ({
      user,
      isLoading,
      sessionExpired,
      accountDisabled,
      clearAuthNotices,
      refreshSession,
      login,
      register,
      logout,
      updateProfile,
      subscribeMergedCart,
      subscribeLogout,
    }),
    [
      user,
      isLoading,
      sessionExpired,
      accountDisabled,
      clearAuthNotices,
      refreshSession,
      login,
      register,
      logout,
      updateProfile,
      subscribeMergedCart,
      subscribeLogout,
    ]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider.");
  }
  return ctx;
}
