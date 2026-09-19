import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { PrivateScope, privateMutationKey, privateQueryKey, ScopeChangedError } from "../src/lib/private-scope";

describe("private browser scope", () => {
  it("scopes query/mutation keys and purges cache and resources on baby switch", () => {
    const queryClient = new QueryClient();
    const scope = new PrivateScope(queryClient);
    scope.set("user-a", "baby-a");
    const old = scope.snapshot();
    const key = privateQueryKey(old, "timeline", { page: 1 });
    queryClient.setQueryData(key, { alias: "private" });
    const controller = new AbortController();
    scope.trackRequest(controller);
    const cleanup = vi.fn();
    scope.registerCleanup(cleanup);

    scope.set("user-a", "baby-b");
    expect(controller.signal.aborted).toBe(true);
    expect(cleanup).toHaveBeenCalledTimes(1);
    expect(queryClient.getQueryData(key)).toBeUndefined();
    expect(() => scope.assertCurrent(old)).toThrow(ScopeChangedError);
    expect(privateQueryKey(scope.snapshot(), "timeline")).toEqual(["private", "user-a", "baby-b", "timeline", {}]);
    expect(privateMutationKey(scope.snapshot(), "save")).toEqual(["private-mutation", "user-a", "baby-b", "save"]);
  });

  it("purges on restore even when user and baby ids did not change", () => {
    const queryClient = new QueryClient();
    const scope = new PrivateScope(queryClient);
    scope.set("user-a", "baby-a");
    const old = scope.snapshot();
    queryClient.setQueryData(privateQueryKey(old, "analysis"), { status: "COMPLETE" });
    scope.clearForRevalidation();
    expect(queryClient.getQueryData(privateQueryKey(old, "analysis"))).toBeUndefined();
    expect(() => scope.assertCurrent(old)).toThrow(ScopeChangedError);
  });

  it("rejects baby-only and unauthenticated private keys", () => {
    const scope = new PrivateScope(new QueryClient());
    expect(() => scope.set(null, "baby-a")).toThrow();
    expect(() => privateQueryKey(scope.snapshot(), "babies")).toThrow();
  });
});
