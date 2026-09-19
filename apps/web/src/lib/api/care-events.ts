"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePrivateScope } from "@/components/app-providers";
import { ContractRequestError, idempotencyHeaders } from "@/lib/api/client";
import type { createApiClient } from "@/lib/api/client";
import { requireData } from "@/lib/api/errors";
import type { components } from "@/lib/api/generated";
import { useApiClient } from "@/lib/api/real-client";
import { privateQueryKey } from "@/lib/private-scope";
import type { PrivateScopeSnapshot } from "@/lib/private-scope";

type ApiClient = ReturnType<typeof createApiClient>;
export type CareEvent = components["schemas"]["CareEvent"];
export type TimelineItem = components["schemas"]["TimelineItem"];
export type TimelineItemPage = components["schemas"]["TimelineItemPage"];
export type CreateCareEventRequest = Readonly<{
  babyId: string;
  clientRequestId: string;
  event: components["schemas"]["CareEventValue"];
}>;

function assertCareEventScope(event: CareEvent, babyId: string): CareEvent {
  if (event.baby_id !== babyId) throw new ContractRequestError("CareEvent does not belong to the requested baby.");
  return event;
}

export async function createCareEvent(client: ApiClient, request: CreateCareEventRequest): Promise<CareEvent> {
  const result = requireData(await client.POST("/babies/{baby_id}/care-events", {
    params: {
      path: { baby_id: request.babyId },
      header: idempotencyHeaders(request.clientRequestId),
    },
    body: { client_request_id: request.clientRequestId, event: request.event },
  }));
  return assertCareEventScope(result, request.babyId);
}

export async function getCareEvent(client: ApiClient, babyId: string, careEventId: string): Promise<CareEvent> {
  const result = requireData(await client.GET("/care-events/{care_event_id}", {
    params: { path: { care_event_id: careEventId } },
  }));
  if (result.care_event_id !== careEventId) throw new ContractRequestError("CareEvent id does not match the request.");
  return assertCareEventScope(result, babyId);
}

export async function getTimelinePage(client: ApiClient, babyId: string, cursor?: string): Promise<TimelineItemPage> {
  const result = requireData(await client.GET("/babies/{baby_id}/timeline", {
    params: { path: { baby_id: babyId }, query: cursor === undefined ? {} : { cursor } },
  }));
  for (const item of result.items) {
    if (item.baby_id !== babyId || item.resource.baby_id !== babyId) {
      throw new ContractRequestError("Timeline item does not belong to the requested baby.");
    }
    if (item.kind === "CARE_EVENT") {
      if (!("care_event_id" in item.resource) || item.resource.care_event_id !== item.resource_id) {
        throw new ContractRequestError("Timeline CareEvent does not match its resource id.");
      }
    }
  }
  return result;
}

function timelineKey(snapshot: PrivateScopeSnapshot, babyId: string) {
  return privateQueryKey(snapshot, "timeline", { babyId });
}

function careEventKey(snapshot: PrivateScopeSnapshot, babyId: string, careEventId: string) {
  return privateQueryKey(snapshot, "care-event", { babyId, careEventId });
}

export function useTimelineQuery(babyId: string, enabled: boolean) {
  const client = useApiClient();
  const scope = usePrivateScope();
  const snapshot = scope.snapshot();
  return useInfiniteQuery({
    queryKey: snapshot.userId ? timelineKey(snapshot, babyId) : ["private", null, babyId, "timeline"],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => {
      if (!client) throw new Error("Real API client is not configured.");
      return getTimelinePage(client, babyId, pageParam);
    },
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: enabled && client !== null && snapshot.userId !== null && snapshot.babyId === babyId,
  });
}

export function useCareEventQuery(babyId: string, careEventId: string, enabled: boolean) {
  const client = useApiClient();
  const scope = usePrivateScope();
  const snapshot = scope.snapshot();
  return useQuery({
    queryKey: snapshot.userId
      ? careEventKey(snapshot, babyId, careEventId)
      : ["private", null, babyId, "care-event", careEventId],
    queryFn: async () => {
      if (!client) throw new Error("Real API client is not configured.");
      return getCareEvent(client, babyId, careEventId);
    },
    enabled: enabled && client !== null && snapshot.userId !== null && snapshot.babyId === babyId,
  });
}

export function useCreateCareEventMutation() {
  const client = useApiClient();
  const scope = usePrivateScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (request: CreateCareEventRequest) => {
      if (!client) throw new Error("Real API client is not configured.");
      const snapshot = scope.snapshot();
      if (!snapshot.userId || snapshot.babyId !== request.babyId) {
        throw new ContractRequestError("CareEvent request is outside the active baby scope.");
      }
      const event = await createCareEvent(client, request);
      scope.assertCurrent(snapshot);
      return { event, snapshot };
    },
    onSuccess: ({ event, snapshot }, request) => {
      if (scope.snapshot() !== snapshot) return;
      queryClient.setQueryData(careEventKey(snapshot, request.babyId, event.care_event_id), event);
      void queryClient.invalidateQueries({ queryKey: timelineKey(snapshot, request.babyId) });
    },
  });
}
