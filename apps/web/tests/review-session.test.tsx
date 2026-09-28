// @vitest-environment jsdom
import type { AuthChangeEvent, Session } from "@supabase/supabase-js";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppProviders, usePrivateScope } from "../src/components/app-providers";
import { RealSessionProvider } from "../src/lib/auth/real-session";
import { PrivateScope, privateQueryKey, ScopeChangedError } from "../src/lib/private-scope";

type AuthCallback = (event: AuthChangeEvent, session: Session | null) => void;
const authEvents = vi.hoisted(() => ({ callback: null as AuthCallback | null }));

vi.mock("@/lib/public-config", () => ({ tryReadPublicConfig: () => ({ apiBaseUrl: "https://api.example.invalid" }) }));
vi.mock("@/lib/auth/supabase-client", () => ({
  getSupabaseClient: () => ({
    auth: {
      onAuthStateChange: (callback: AuthCallback) => {
        authEvents.callback = callback;
        return { data: { subscription: { unsubscribe: vi.fn() } } };
      },
    },
  }),
}));

function sessionFor(userId: string): Session {
  return { user: { id: userId, email: `${userId}@example.invalid` } } as Session;
}

let scope: PrivateScope;
let queryClient: QueryClient;
function ScopeProbe() {
  const currentScope = usePrivateScope();
  const currentQueryClient = useQueryClient();
  useEffect(() => {
    scope = currentScope;
    queryClient = currentQueryClient;
  }, [currentScope, currentQueryClient]);
  return null;
}

afterEach(() => {
  cleanup();
  authEvents.callback = null;
});

describe("A-03 real session scope", () => {
  it("keeps the selected baby and in-flight work for a repeated SIGNED_IN of the same user", () => {
    render(
      <AppProviders>
        <RealSessionProvider><ScopeProbe /></RealSessionProvider>
      </AppProviders>,
    );
    expect(authEvents.callback).not.toBeNull();

    act(() => authEvents.callback!("INITIAL_SESSION", sessionFor("user-a")));
    act(() => scope.set("user-a", "baby-a"));
    const before = scope.snapshot();
    const key = privateQueryKey(before, "record");
    queryClient.setQueryData(key, { private: true });
    const controller = new AbortController();
    scope.trackRequest(controller);

    act(() => authEvents.callback!("SIGNED_IN", sessionFor("user-a")));

    expect(scope.snapshot()).toBe(before);
    expect(scope.snapshot().babyId).toBe("baby-a");
    expect(controller.signal.aborted).toBe(false);
    expect(queryClient.getQueryData(key)).toEqual({ private: true });

    act(() => authEvents.callback!("SIGNED_IN", sessionFor("user-b")));
    expect(scope.snapshot()).toMatchObject({ userId: "user-b", babyId: null });
    expect(controller.signal.aborted).toBe(true);
    expect(() => scope.assertCurrent(before)).toThrow(ScopeChangedError);
  });
});
