"use client";

// A-08 ①: browser-side wiring for B-09's GET /babies/{baby_id}/changes (contract 1.1.1).
// Server behavior, revision semantics, and the safe polling/recovery order are fixed by
// docs/handoffs/b09-shared-change-feed.md — this module implements that order, it does not
// invent new semantics. Realtime stays off; this is the foreground-polling path only.
import { useCallback, useEffect, useSyncExternalStore } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";
import { usePrivateScope } from "@/components/app-providers";
import { activeBabyKey, babiesKey } from "@/lib/api/babies";
import { careEventKey, timelineKey } from "@/lib/api/care-events";
import { ContractRequestError } from "@/lib/api/client";
import { ContractApiError, requireData } from "@/lib/api/errors";
import type { components } from "@/lib/api/generated";
import { membersKey } from "@/lib/api/members";
import { useApiClient } from "@/lib/api/real-client";
import type { RealApiClient } from "@/lib/api/real-client";
import type { PrivateScopeSnapshot } from "@/lib/private-scope";

export type SharedChange = components["schemas"]["Change"];
export type SharedChanges = components["schemas"]["Changes"];

/** Poll cadence while the tab is foregrounded. Not a delivery-time guarantee (B-09 handoff §요청·응답 예시). */
const POLL_INTERVAL_MS = 5000;
/** Bounded backoff cap for 429/503; no fixed value is in the contract, so this is our own limit, not a contract number. */
const MAX_BACKOFF_MS = 60_000;

export async function getChanges(
  client: RealApiClient,
  babyId: string,
  sinceRevision: number,
): Promise<SharedChanges> {
  if (!Number.isInteger(sinceRevision) || sinceRevision < 0) {
    throw new ContractRequestError("since_revision must be a non-negative integer.");
  }
  const result = requireData(await client.GET("/babies/{baby_id}/changes", {
    params: { path: { baby_id: babyId }, query: { since_revision: sinceRevision } },
  }));
  if (result.baby_id !== babyId) {
    throw new ContractRequestError("Changes response does not match the requested baby.");
  }
  if (!result.resync_required && result.current_revision < sinceRevision) {
    throw new ContractRequestError("Changes response revision moved backward.");
  }
  return result;
}

/**
 * Refresh only implemented query resources. Observation/episode changes also affect the
 * timeline and the home's latest-confirmed-observation view.
 */
async function applyChange(
  queryClient: QueryClient,
  scope: PrivateScopeSnapshot,
  babyId: string,
  change: SharedChange,
): Promise<void> {
  if (["CARE_EVENT", "STATE_OBSERVATION", "BABY"].includes(change.resource_type)) {
    await queryClient.invalidateQueries({ queryKey: ["private", scope.userId, babyId, "summary"] }, { throwOnError: true });
    await queryClient.invalidateQueries({ queryKey: ["private", scope.userId, babyId, "patterns"] }, { throwOnError: true });
  }
  switch (change.resource_type) {
    case "STATE_OBSERVATION":
    case "EPISODE": {
      await queryClient.invalidateQueries({ queryKey: timelineKey(scope, babyId) }, { throwOnError: true });
      return;
    }
    case "CARE_EVENT": {
      const key = careEventKey(scope, babyId, change.resource_id);
      if (change.deleted) {
        queryClient.removeQueries({ queryKey: key, exact: true });
      } else {
        await queryClient.invalidateQueries({ queryKey: key, exact: true }, { throwOnError: true });
      }
      await queryClient.invalidateQueries({ queryKey: timelineKey(scope, babyId) }, { throwOnError: true });
      return;
    }
    case "BABY": {
      if (!scope.userId) return;
      await queryClient.invalidateQueries({ queryKey: babiesKey(scope.userId) }, { throwOnError: true });
      await queryClient.invalidateQueries({ queryKey: activeBabyKey(scope.userId) }, { throwOnError: true });
      return;
    }
    case "MEMBERSHIP": {
      await queryClient.invalidateQueries({ queryKey: membersKey(babyId) }, { throwOnError: true });
      return;
    }
    default:
      return;
  }
}

async function applyChanges(
  queryClient: QueryClient,
  scope: PrivateScopeSnapshot,
  babyId: string,
  changes: readonly SharedChange[],
): Promise<void> {
  // Tombstones first, per B-09 handoff §A의 안전한 폴링·복구 순서 step 5.
  for (const change of changes) if (change.deleted) await applyChange(queryClient, scope, babyId, change);
  for (const change of changes) if (!change.deleted) await applyChange(queryClient, scope, babyId, change);
}

function clearBabyScopeCache(queryClient: QueryClient, scope: PrivateScopeSnapshot, babyId: string): void {
  queryClient.removeQueries({ queryKey: ["private", scope.userId, babyId] });
  queryClient.removeQueries({ queryKey: membersKey(babyId) });
  if (scope.userId) {
    void queryClient.invalidateQueries({ queryKey: babiesKey(scope.userId) });
    void queryClient.invalidateQueries({ queryKey: activeBabyKey(scope.userId) });
  }
}

/** 401 is handled by the client's own refresh-then-reset; 404 means this baby is gone/unreachable. */
function isTerminal(error: unknown): boolean {
  return error instanceof ContractApiError && (error.kind === "authentication" || error.kind === "not-found");
}

function nextBackoffMs(error: unknown, currentMs: number): number {
  if (error instanceof ContractApiError) {
    if (error.kind === "rate-limit") {
      return Math.min(Math.max((error.retryAfterSeconds ?? 1) * 1000, POLL_INTERVAL_MS), MAX_BACKOFF_MS);
    }
    if (error.kind === "service") {
      return Math.min(currentMs * 2, MAX_BACKOFF_MS);
    }
  }
  return POLL_INTERVAL_MS;
}

type StepResult =
  | Readonly<{ kind: "ok"; revision: number }>
  | Readonly<{ kind: "resync" }>
  | Readonly<{ kind: "error" }>;

/**
 * Foreground polling per B-09 handoff §A의 안전한 폴링·복구 순서: bootstrap from since_revision=0,
 * immediately catch up once, then poll every 5s only while the tab is visible, pausing while
 * hidden and resuming with an immediate poll on return. Never runs two /changes calls at once.
 */
export function useSharedChangePolling(babyId: string, enabled: boolean): void {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const scope = usePrivateScope();
  const subscribe = useCallback((listener: () => void) => scope.subscribe(listener), [scope]);
  const getSnapshot = useCallback(() => scope.snapshot(), [scope]);
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  const ready = enabled && client !== null && snapshot.userId !== null && snapshot.babyId === babyId;
  const userId = snapshot.userId;

  useEffect(() => {
    if (!ready || !client || typeof document === "undefined") return;
    // generation is a placeholder: privateQueryKey only reads userId/babyId, and this effect
    // deliberately does not restart on a same-scope generation bump (see the dependency array
    // below) — an in-place revalidation must not race this hook's own visibility-driven resume.
    const scopeSnapshot: PrivateScopeSnapshot = { userId, babyId, generation: 0 };

    let cancelled = false;
    let halted = false;
    let revision: number | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let inFlight = false;
    let backoffMs = POLL_INTERVAL_MS;

    const halt = () => {
      halted = true;
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };

    const handleError = (error: unknown) => {
      if (cancelled) return;
      if (isTerminal(error)) {
        clearBabyScopeCache(queryClient, scopeSnapshot, babyId);
        halt();
      } else {
        backoffMs = nextBackoffMs(error, backoffMs);
      }
    };

    // since_revision=0 always answers resync_required=true (B-09 handoff: "Use 0 only to
    // bootstrap a full resync") — that is the expected outcome here, not a retry signal.
    // Only current_revision (the boundary R0) matters from this call.
    const fetchBoundary = async (): Promise<number | null> => {
      try {
        const result = await getChanges(client, babyId, 0);
        if (cancelled) return null;
        backoffMs = POLL_INTERVAL_MS;
        return result.current_revision;
      } catch (error) {
        handleError(error);
        return null;
      }
    };

    const fetchIncremental = async (sinceRevision: number): Promise<StepResult> => {
      try {
        const result = await getChanges(client, babyId, sinceRevision);
        if (cancelled) return { kind: "error" };
        if (result.resync_required) return { kind: "resync" };
        // Keep the previous revision when an active resource refetch fails; the next tick
        // must replay this coalesced range instead of silently losing its change.
        await applyChanges(queryClient, scopeSnapshot, babyId, result.changes);
        if (cancelled) return { kind: "error" };
        backoffMs = POLL_INTERVAL_MS;
        return { kind: "ok", revision: result.current_revision };
      } catch (error) {
        if (cancelled) return { kind: "error" };
        handleError(error);
        return { kind: "error" };
      }
    };

    const bootstrap = async () => {
      const r0 = await fetchBoundary();
      if (cancelled || halted || r0 === null) return;
      const caughtUp = await fetchIncremental(r0);
      if (cancelled || halted) return;
      // A resync or error right after boundary R0 is rare (a gap opening within the same
      // instant); leave revision null so the next tick simply re-bootstraps.
      revision = caughtUp.kind === "ok" ? caughtUp.revision : null;
    };

    const tick = async () => {
      if (cancelled || halted || inFlight) return;
      inFlight = true;
      try {
        if (revision === null) {
          await bootstrap();
          return;
        }
        const result = await fetchIncremental(revision);
        if (result.kind === "ok") revision = result.revision;
        else if (result.kind === "resync") {
          revision = null;
          await bootstrap();
        }
      } finally {
        inFlight = false;
      }
    };

    const scheduleNext = () => {
      if (cancelled || halted) return;
      if (document.visibilityState !== "visible") {
        timer = null;
        return;
      }
      timer = setTimeout(() => {
        timer = null;
        void tick().then(scheduleNext);
      }, backoffMs);
    };

    const onVisibilityChange = () => {
      if (cancelled || halted) return;
      if (document.visibilityState === "visible") {
        if (!timer && !inFlight) void tick().then(scheduleNext);
      } else if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    if (document.visibilityState === "visible") void tick().then(scheduleNext);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [ready, client, queryClient, babyId, userId]);
}
