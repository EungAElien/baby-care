import type { QueryClient } from "@tanstack/react-query";

export type PrivateScopeSnapshot = Readonly<{
  userId: string | null;
  babyId: string | null;
  generation: number;
}>;

export class ScopeChangedError extends Error {
  constructor() {
    super("The private-data scope changed before the request completed.");
    this.name = "ScopeChangedError";
  }
}

/** Keeps browser-only state from crossing a user or baby boundary. */
export class PrivateScope {
  private state: PrivateScopeSnapshot = { userId: null, babyId: null, generation: 0 };
  private readonly controllers = new Set<AbortController>();
  private readonly cleanups = new Set<() => void>();
  private readonly listeners = new Set<() => void>();

  constructor(private readonly queryClient: QueryClient) {}

  snapshot(): PrivateScopeSnapshot {
    return this.state;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  set(userId: string | null, babyId: string | null): void {
    if (!userId && babyId) {
      throw new Error("A baby cannot be selected without a user.");
    }
    if (this.state.userId === userId && this.state.babyId === babyId) return;
    this.transition(userId, babyId);
  }

  reset(): void {
    this.transition(null, null);
  }

  /** BFCache, tab restore, and foreground focus must discard private snapshots. */
  clearForRevalidation(): void {
    this.transition(this.state.userId, this.state.babyId);
  }

  assertCurrent(snapshot: PrivateScopeSnapshot): void {
    if (this.state !== snapshot) throw new ScopeChangedError();
  }

  trackRequest(controller: AbortController): () => void {
    this.controllers.add(controller);
    return () => this.controllers.delete(controller);
  }

  /** Future forms, audio playback, and subscriptions register teardown here. */
  registerCleanup(cleanup: () => void): () => void {
    this.cleanups.add(cleanup);
    return () => this.cleanups.delete(cleanup);
  }

  private transition(userId: string | null, babyId: string | null): void {
    this.state = { userId, babyId, generation: this.state.generation + 1 };
    for (const controller of this.controllers) controller.abort();
    this.controllers.clear();
    for (const cleanup of this.cleanups) {
      try {
        cleanup();
      } catch {
        // One failing cleanup must not preserve another user's cache or audio resources.
      } finally {
        this.cleanups.delete(cleanup);
      }
    }
    this.queryClient.clear();
    for (const listener of this.listeners) listener();
  }
}

export type QueryParameter = string | number | boolean | null;

export function privateQueryKey(
  scope: PrivateScopeSnapshot,
  resource: string,
  parameters: Readonly<Record<string, QueryParameter>> = {},
) {
  if (!scope.userId) throw new Error("Private queries require an authenticated user scope.");
  return ["private", scope.userId, scope.babyId, resource, parameters] as const;
}

export function privateMutationKey(scope: PrivateScopeSnapshot, resource: string) {
  if (!scope.userId) throw new Error("Private mutations require an authenticated user scope.");
  return ["private-mutation", scope.userId, scope.babyId, resource] as const;
}
