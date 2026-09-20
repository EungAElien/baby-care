// @vitest-environment jsdom
import type { ReactNode } from "react";
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders, usePrivateScope } from "../src/components/app-providers";
import { activeBabyKey, babiesKey } from "../src/lib/api/babies";
import { careEventKey, timelineKey } from "../src/lib/api/care-events";
import { useSharedChangePolling } from "../src/lib/api/changes";
import { membersKey } from "../src/lib/api/members";
import type { components } from "../src/lib/api/generated";
import type { PrivateScope } from "../src/lib/private-scope";

// A-08 ①: the foreground /changes polling loop. B-09 handoff §A의 안전한 폴링·복구 순서.
// These are all synthetic responses (never a real server); they only verify the client-side
// ordering, cache application, pause/resume, resync, and backoff rules the handoff specifies.

type Changes = components["schemas"]["Changes"];

const babyId = "10000000-0000-4000-8000-000000000101";
const userId = "10000000-0000-4000-8000-000000000001";
const careEventId = "10000000-0000-4000-8000-000000000601";
const membershipId = "10000000-0000-4000-8000-000000000204";

// The real useApiClient() (src/lib/api/real-client.ts) memoizes on scope identity, so a
// same-scope revalidation (AppProviders' visibilitychange/focus handler) never hands the
// hook a new client reference. This mock returns the same object every render for the same
// reason — otherwise the effect would spuriously restart on every such revalidation, which
// the real client does not do.
const state = vi.hoisted(() => {
  const get = vi.fn();
  return { get, client: { GET: get } };
});
vi.mock("@/lib/api/real-client", () => ({ useApiClient: () => state.client }));

function jsonStep(
  body: Changes,
  status = 200,
  headers: Record<string, string> = {},
) {
  return { data: body, response: new Response(null, { status, headers }) };
}

function errorStep(
  status: number,
  code: string,
  headers: Record<string, string> = {},
) {
  return {
    error: {
      code,
      message: code,
      retryable: true,
      request_id: "req",
      field_errors: [],
      details: {},
    },
    response: new Response(null, { status, headers }),
  };
}

function changes(
  current: number,
  entries: Changes["changes"],
  resync = false,
  forBabyId = babyId,
): Changes {
  return {
    baby_id: forBabyId,
    current_revision: current,
    changes: entries,
    resync_required: resync,
    server_time: "2026-09-20T00:00:00Z",
  };
}

let capturedScope: PrivateScope;
let capturedQueryClient: ReturnType<typeof useQueryClient>;

function Grabber() {
  const scope = usePrivateScope();
  const queryClient = useQueryClient();
  useEffect(() => {
    capturedScope = scope;
    capturedQueryClient = queryClient;
  }, [scope, queryClient]);
  return null;
}

function Wrapper({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <AppProviders>
      <Grabber />
      {children}
    </AppProviders>
  );
}

function setVisibility(value: "visible" | "hidden") {
  act(() => {
    Object.defineProperty(document, "visibilityState", {
      value,
      configurable: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

function setScope() {
  act(() => {
    capturedScope.set(userId, babyId);
  });
}

async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  state.get.mockReset();
  setVisibility("visible");
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("A-08 ① useSharedChangePolling", () => {
  it("bootstraps from 0, catches up immediately, then never runs two calls at once", async () => {
    state.get
      .mockResolvedValueOnce(jsonStep(changes(8, [], true)))
      .mockResolvedValueOnce(jsonStep(changes(8, [])));
    const { unmount } = renderHook(() => useSharedChangePolling(babyId, true), {
      wrapper: Wrapper,
    });
    setScope();
    await flush();
    await flush();

    expect(state.get).toHaveBeenCalledTimes(2);
    expect(state.get.mock.calls[0]![1].params.query.since_revision).toBe(0);
    expect(state.get.mock.calls[1]![1].params.query.since_revision).toBe(8);
    unmount();
  });

  it("applies tombstones before live changes and invalidates the right query for each resource type", async () => {
    // Cache entries with no active observer are gcTime:0 and vanish immediately (AppProviders'
    // QueryClient defaults), so this asserts on the cache calls applyChange makes, not on
    // reading the (already garbage-collected) query state back afterward.
    const careKey = careEventKey(
      { userId, babyId, generation: 0 },
      babyId,
      careEventId,
    );
    const timeKey = timelineKey({ userId, babyId, generation: 0 }, babyId);
    state.get
      .mockResolvedValueOnce(jsonStep(changes(8, [], true)))
      .mockResolvedValueOnce(jsonStep(changes(8, [])));
    renderHook(() => useSharedChangePolling(babyId, true), {
      wrapper: Wrapper,
    });
    setScope();
    await flush();
    await flush();

    const calls: Array<{ method: "invalidate" | "remove"; key: unknown }> = [];
    vi.spyOn(capturedQueryClient, "invalidateQueries").mockImplementation(
      (filters) => {
        calls.push({ method: "invalidate", key: filters?.queryKey });
        return Promise.resolve();
      },
    );
    vi.spyOn(capturedQueryClient, "removeQueries").mockImplementation(
      (filters) => {
        calls.push({ method: "remove", key: filters?.queryKey });
      },
    );

    state.get.mockResolvedValueOnce(
      jsonStep(
        changes(11, [
          {
            resource_type: "CARE_EVENT",
            resource_id: careEventId,
            version: 4,
            deleted: true,
          },
          {
            resource_type: "BABY",
            resource_id: babyId,
            version: 9,
            deleted: false,
          },
          {
            resource_type: "MEMBERSHIP",
            resource_id: membershipId,
            version: 1,
            deleted: false,
          },
        ]),
      ),
    );
    await flush(5000);

    expect(calls).toEqual([
      { method: "remove", key: careKey },
      { method: "invalidate", key: timeKey },
      { method: "invalidate", key: babiesKey(userId) },
      { method: "invalidate", key: activeBabyKey(userId) },
      { method: "invalidate", key: membersKey(babyId) },
      { method: "invalidate", key: ["private", userId, babyId, "summary"] },
      { method: "invalidate", key: ["private", userId, babyId, "patterns"] },
    ]);
  });

  it("replays a change when its active resource refetch fails before revision advances", async () => {
    state.get
      .mockResolvedValueOnce(jsonStep(changes(8, [], true)))
      .mockResolvedValueOnce(jsonStep(changes(8, [])));
    renderHook(() => useSharedChangePolling(babyId, true), {
      wrapper: Wrapper,
    });
    setScope();
    await flush();
    await flush();

    const invalidations = vi.spyOn(capturedQueryClient, "invalidateQueries");
    invalidations.mockRejectedValueOnce(
      new Error("synthetic resource refetch failure"),
    );
    state.get.mockResolvedValueOnce(
      jsonStep(
        changes(9, [
          {
            resource_type: "CARE_EVENT",
            resource_id: careEventId,
            version: 2,
            deleted: false,
          },
        ]),
      ),
    );
    await flush(5000);
    expect(state.get.mock.calls[2]![1].params.query.since_revision).toBe(8);
    expect(invalidations.mock.calls[0]![1]).toEqual({ throwOnError: true });

    state.get.mockResolvedValueOnce(
      jsonStep(
        changes(9, [
          {
            resource_type: "CARE_EVENT",
            resource_id: careEventId,
            version: 2,
            deleted: false,
          },
        ]),
      ),
    );
    await flush(5000);
    expect(state.get.mock.calls[3]![1].params.query.since_revision).toBe(8);
    expect(invalidations).toHaveBeenCalledWith(
      { queryKey: timelineKey({ userId, babyId, generation: 0 }, babyId) },
      { throwOnError: true },
    );

    state.get.mockResolvedValueOnce(jsonStep(changes(9, [])));
    await flush(5000);
    expect(state.get.mock.calls[4]![1].params.query.since_revision).toBe(9);
  });

  it("pauses while hidden and polls immediately on return", async () => {
    state.get
      .mockResolvedValueOnce(jsonStep(changes(8, [], true)))
      .mockResolvedValueOnce(jsonStep(changes(8, [])));
    renderHook(() => useSharedChangePolling(babyId, true), {
      wrapper: Wrapper,
    });
    setScope();
    await flush();
    await flush();
    expect(state.get).toHaveBeenCalledTimes(2);

    setVisibility("hidden");
    await flush(20_000);
    expect(state.get).toHaveBeenCalledTimes(2);

    state.get.mockResolvedValueOnce(jsonStep(changes(8, [])));
    setVisibility("visible");
    await flush();
    expect(state.get).toHaveBeenCalledTimes(3);
    expect(state.get.mock.calls[2]![1].params.query.since_revision).toBe(8);
  });

  it("re-bootstraps when a mid-stream poll reports resync_required", async () => {
    state.get
      .mockResolvedValueOnce(jsonStep(changes(8, [], true)))
      .mockResolvedValueOnce(jsonStep(changes(8, [])));
    renderHook(() => useSharedChangePolling(babyId, true), {
      wrapper: Wrapper,
    });
    setScope();
    await flush();
    await flush();

    state.get
      .mockResolvedValueOnce(jsonStep(changes(740, [], true)))
      .mockResolvedValueOnce(jsonStep(changes(740, [], true)))
      .mockResolvedValueOnce(jsonStep(changes(740, [])));
    await flush(5000);

    expect(state.get.mock.calls[2]![1].params.query.since_revision).toBe(8);
    expect(state.get.mock.calls[3]![1].params.query.since_revision).toBe(0);
    expect(state.get.mock.calls[4]![1].params.query.since_revision).toBe(740);
  });

  it("halts and clears the baby scope cache on a 404 (membership lost or baby deleted)", async () => {
    state.get
      .mockResolvedValueOnce(jsonStep(changes(8, [], true)))
      .mockResolvedValueOnce(jsonStep(changes(8, [])));
    renderHook(() => useSharedChangePolling(babyId, true), {
      wrapper: Wrapper,
    });
    setScope();
    await flush();
    await flush();

    const removed: unknown[] = [];
    vi.spyOn(capturedQueryClient, "removeQueries").mockImplementation(
      (filters) => {
        removed.push(filters?.queryKey);
      },
    );
    vi.spyOn(capturedQueryClient, "invalidateQueries").mockImplementation(() =>
      Promise.resolve(),
    );

    state.get.mockResolvedValueOnce(errorStep(404, "RESOURCE_NOT_FOUND"));
    await flush(5000);
    expect(removed).toEqual([["private", userId, babyId], membersKey(babyId)]);
    expect(state.get).toHaveBeenCalledTimes(3);

    await flush(30_000);
    expect(state.get).toHaveBeenCalledTimes(3);
  });

  it("backs off using Retry-After on 429 instead of the normal 5s cadence", async () => {
    state.get
      .mockResolvedValueOnce(jsonStep(changes(8, [], true)))
      .mockResolvedValueOnce(jsonStep(changes(8, [])));
    renderHook(() => useSharedChangePolling(babyId, true), {
      wrapper: Wrapper,
    });
    setScope();
    await flush();
    await flush();

    state.get.mockResolvedValueOnce(
      errorStep(429, "RATE_LIMITED", { "Retry-After": "20" }),
    );
    await flush(5000);
    expect(state.get).toHaveBeenCalledTimes(3);

    state.get.mockResolvedValueOnce(jsonStep(changes(8, [])));
    await flush(19_999);
    expect(state.get).toHaveBeenCalledTimes(3);
    await flush(1);
    expect(state.get).toHaveBeenCalledTimes(4);
  });

  it("stops polling and detaches its listener on unmount", async () => {
    state.get
      .mockResolvedValueOnce(jsonStep(changes(8, [], true)))
      .mockResolvedValueOnce(jsonStep(changes(8, [])));
    const { unmount } = renderHook(() => useSharedChangePolling(babyId, true), {
      wrapper: Wrapper,
    });
    setScope();
    await flush();
    await flush();
    expect(state.get).toHaveBeenCalledTimes(2);

    unmount();
    await flush(20_000);
    expect(state.get).toHaveBeenCalledTimes(2);
  });
});

// A-08 ②: checklist bullet 2 (Realtime, SEC30~31) stays blocked — B has not produced the
// channel-revocation evidence yet, so this slice only closes out bullet 3 (drafts never
// propagate, and a baby switch or leaving this baby stops the poll and drops its cache).
// Draft non-propagation is not separately tested: DraftProvider (src/lib/mock/draft.tsx) has
// no code path into getChanges/applyChange, so there is nothing for the feed to leak.
describe("A-08 ② baby-switch and leave stop this baby's polling", () => {
  it("stops immediately when this baby's scope clears (e.g. care-team's self-leave), no 404 required", async () => {
    state.get
      .mockResolvedValueOnce(jsonStep(changes(8, [], true)))
      .mockResolvedValueOnce(jsonStep(changes(8, [])));
    renderHook(() => useSharedChangePolling(babyId, true), {
      wrapper: Wrapper,
    });
    setScope();
    await flush();
    await flush();
    expect(state.get).toHaveBeenCalledTimes(2);

    // Mirrors care-team.tsx's leaveOrAttempt(): the user stays signed in but this baby's
    // scope is cleared as soon as the leave mutation succeeds, before any navigation.
    act(() => {
      capturedScope.set(userId, null);
    });
    await flush(30_000);
    expect(state.get).toHaveBeenCalledTimes(2);
  });

  it("drops the old baby's poll and bootstraps the new one on a baby switch", async () => {
    const otherBabyId = "10000000-0000-4000-8000-000000000102";
    state.get
      .mockResolvedValueOnce(jsonStep(changes(8, [], true)))
      .mockResolvedValueOnce(jsonStep(changes(8, [])));
    const { rerender } = renderHook(
      ({ id }: { id: string }) => useSharedChangePolling(id, true),
      { wrapper: Wrapper, initialProps: { id: babyId } },
    );
    setScope();
    await flush();
    await flush();
    expect(state.get).toHaveBeenCalledTimes(2);

    state.get
      .mockResolvedValueOnce(jsonStep(changes(3, [], true, otherBabyId)))
      .mockResolvedValueOnce(jsonStep(changes(3, [], false, otherBabyId)));
    act(() => {
      capturedScope.set(userId, otherBabyId);
    });
    rerender({ id: otherBabyId });
    await flush();
    await flush();

    expect(state.get).toHaveBeenCalledTimes(4);
    const calledBabyIds = state.get.mock.calls.map(
      (call) =>
        (call[1] as { params: { path: { baby_id: string } } }).params.path
          .baby_id,
    );
    expect(calledBabyIds).toEqual([babyId, babyId, otherBabyId, otherBabyId]);

    // Old baby must not resume even though its interval would otherwise still be pending —
    // every later call (retries included, since nothing is queued past call 4) stays on the
    // new baby.
    await flush(30_000);
    const laterCalls = state.get.mock.calls.slice(4);
    expect(laterCalls.length).toBeGreaterThan(0);
    for (const call of laterCalls) {
      expect(
        (call[1] as { params: { path: { baby_id: string } } }).params.path
          .baby_id,
      ).toBe(otherBabyId);
    }
  });
});
