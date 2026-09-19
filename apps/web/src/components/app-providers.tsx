"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { PrivateScope } from "@/lib/private-scope";

const PrivateScopeContext = createContext<PrivateScope | null>(null);

export function AppProviders({ children }: Readonly<{ children: React.ReactNode }>) {
  const [browserState] = useState(() => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
          staleTime: 0,
          gcTime: 0,
          refetchOnMount: "always",
          refetchOnWindowFocus: false,
        },
        mutations: { retry: false, gcTime: 0 },
      },
    });
    return { queryClient, scope: new PrivateScope(queryClient) };
  });

  useEffect(() => {
    const revalidate = () => browserState.scope.clearForRevalidation();
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) revalidate();
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") revalidate();
    };
    window.addEventListener("pageshow", onPageShow);
    window.addEventListener("focus", revalidate);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.removeEventListener("pageshow", onPageShow);
      window.removeEventListener("focus", revalidate);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      browserState.scope.reset();
    };
  }, [browserState]);

  return (
    <QueryClientProvider client={browserState.queryClient}>
      <PrivateScopeContext.Provider value={browserState.scope}>{children}</PrivateScopeContext.Provider>
    </QueryClientProvider>
  );
}

export function usePrivateScope(): PrivateScope {
  const scope = useContext(PrivateScopeContext);
  if (!scope) throw new Error("usePrivateScope must be called below AppProviders.");
  return scope;
}

/** Shared by the real and mock baby layouts to keep PrivateScope's (user_id, baby_id) snapshot current. */
export function useSyncPrivateScopeForBaby(userId: string | null, babyId: string): void {
  const scope = usePrivateScope();
  const lastKey = useRef<string | null>(null);

  useEffect(() => {
    const key = `${userId ?? ""}:${babyId}`;
    if (lastKey.current === key) return;
    lastKey.current = key;
    if (userId) scope.set(userId, babyId);
  }, [userId, babyId, scope]);
}
