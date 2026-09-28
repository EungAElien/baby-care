"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePrivateScope } from "@/components/app-providers";
import { ContractRequestError, idempotencyHeaders } from "@/lib/api/client";
import type { createApiClient } from "@/lib/api/client";
import { ContractApiError, requireData } from "@/lib/api/errors";
import type { components } from "@/lib/api/generated";
import { useApiClient } from "@/lib/api/real-client";
import { privateQueryKey } from "@/lib/private-scope";
import type { PrivateScopeSnapshot } from "@/lib/private-scope";

type ApiClient = ReturnType<typeof createApiClient>;
export type CareEvent = components["schemas"]["CareEvent"];
export type TimelineItem = components["schemas"]["TimelineItem"];
export type TimelineItemPage = components["schemas"]["TimelineItemPage"];
export type DeletionJob = components["schemas"]["DeletionJob"];
export type ActionAttempt = components["schemas"]["ActionAttempt"];
export type CreateCareEventRequest = Readonly<{
  babyId: string;
  clientRequestId: string;
  event: components["schemas"]["CareEventValue"];
}>;
export type PatchCareEventRequest = CreateCareEventRequest & Readonly<{
  careEventId: string;
  version: number;
}>;
export type DeleteCareEventRequest = Readonly<{
  babyId: string;
  careEventId: string;
  clientRequestId: string;
  version: number;
}>;
export type LinkCareEventRequest = Readonly<{
  babyId: string;
  careEventId: string;
  episodeId: string;
  clientRequestId: string;
  sequence: number;
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

export async function patchCareEvent(client: ApiClient, request: PatchCareEventRequest): Promise<CareEvent> {
  const result = requireData(await client.PATCH("/care-events/{care_event_id}", {
    params: {
      path: { care_event_id: request.careEventId },
      header: idempotencyHeaders(request.clientRequestId),
    },
    body: { client_request_id: request.clientRequestId, version: request.version, event: request.event },
  }));
  if (result.care_event_id !== request.careEventId) {
    throw new ContractRequestError("Updated CareEvent id does not match the request.");
  }
  return assertCareEventScope(result, request.babyId);
}

/** A 409 payload is not display authority: read again before showing a latest value. */
export async function getCareEventAfterConflict(
  client: ApiClient, babyId: string, careEventId: string, error: unknown,
): Promise<CareEvent> {
  if (!(error instanceof ContractApiError) || error.status !== 409 || error.envelope.code !== "VERSION_CONFLICT") {
    throw new ContractRequestError("Only a CareEvent version conflict can be refreshed here.");
  }
  return getCareEvent(client, babyId, careEventId);
}

function assertCareEventDeletion(job: DeletionJob, babyId: string, careEventId: string): DeletionJob {
  if (job.scope !== "CARE_EVENT" || job.baby_id !== babyId || job.resource_id !== careEventId) {
    throw new ContractRequestError("Deletion job does not belong to the requested CareEvent.");
  }
  return job;
}

export async function deleteCareEvent(client: ApiClient, request: DeleteCareEventRequest): Promise<DeletionJob> {
  const result = requireData(await client.DELETE("/care-events/{care_event_id}", {
    params: {
      path: { care_event_id: request.careEventId },
      header: idempotencyHeaders(request.clientRequestId),
      query: { version: request.version },
    },
  }));
  return assertCareEventDeletion(result, request.babyId, request.careEventId);
}

export async function getCareEventDeletion(
  client: ApiClient, deletionJobId: string, requesterUserId: string,
): Promise<DeletionJob> {
  const result = requireData(await client.GET("/deletions/{deletion_job_id}", {
    params: { path: { deletion_job_id: deletionJobId } },
  }));
  if (result.deletion_job_id !== deletionJobId || result.requester_user_id !== requesterUserId ||
    result.scope !== "CARE_EVENT") {
    throw new ContractRequestError("Deletion job is outside the current requester scope.");
  }
  return result;
}

export async function retryCareEventDeletion(
  client: ApiClient, deletionJobId: string, requesterUserId: string,
  expectedAttempt: number, clientRequestId: string,
): Promise<DeletionJob> {
  const result = requireData(await client.POST("/deletions/{deletion_job_id}/retry", {
    params: {
      path: { deletion_job_id: deletionJobId },
      header: idempotencyHeaders(clientRequestId),
    },
    body: { client_request_id: clientRequestId, expected_attempt: expectedAttempt },
  }));
  if (result.deletion_job_id !== deletionJobId || result.requester_user_id !== requesterUserId ||
    result.scope !== "CARE_EVENT") {
    throw new ContractRequestError("Deletion job is outside the current requester scope.");
  }
  return result;
}

export async function linkExistingCareEvent(client: ApiClient, request: LinkCareEventRequest): Promise<ActionAttempt> {
  const body: components["schemas"]["CreateAction"] = {
    client_request_id: request.clientRequestId,
    care_event_id: request.careEventId,
    new_care_event: null,
    recommendation_id: null,
    performed_by_user_id: null,
    sequence: request.sequence,
  };
  const result = requireData(await client.POST("/episodes/{episode_id}/actions", {
    params: {
      path: { episode_id: request.episodeId },
      header: idempotencyHeaders(request.clientRequestId),
    },
    // openapi-fetch narrows the oneOf branch to an omitted field, but the contract requires explicit null.
    // @ts-expect-error The runtime body is checked against the OpenAPI CreateAction schema above and in tests.
    body,
  }));
  if (result.baby_id !== request.babyId || result.care_event_id !== request.careEventId ||
    result.episode_id !== request.episodeId) {
    throw new ContractRequestError("Linked action does not match the requested baby, event, and episode.");
  }
  return result;
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

export function timelineKey(snapshot: PrivateScopeSnapshot, babyId: string) {
  return privateQueryKey(snapshot, "timeline", { babyId });
}

export function careEventKey(snapshot: PrivateScopeSnapshot, babyId: string, careEventId: string) {
  return privateQueryKey(snapshot, "care-event", { babyId, careEventId });
}

function deletionKey(snapshot: PrivateScopeSnapshot, deletionJobId: string) {
  return privateQueryKey(snapshot, "care-event-deletion", { deletionJobId });
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

export function usePatchCareEventMutation() {
  const client = useApiClient();
  const scope = usePrivateScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (request: PatchCareEventRequest) => {
      if (!client) throw new Error("Real API client is not configured.");
      const snapshot = scope.snapshot();
      if (!snapshot.userId || snapshot.babyId !== request.babyId) {
        throw new ContractRequestError("CareEvent request is outside the active baby scope.");
      }
      const event = await patchCareEvent(client, request);
      scope.assertCurrent(snapshot);
      return { event, snapshot };
    },
    onSuccess: ({ event, snapshot }, request) => {
      if (scope.snapshot() !== snapshot) return;
      queryClient.setQueryData(careEventKey(snapshot, request.babyId, request.careEventId), event);
      void queryClient.invalidateQueries({ queryKey: timelineKey(snapshot, request.babyId) });
    },
  });
}

export function useDeleteCareEventMutation() {
  const client = useApiClient();
  const scope = usePrivateScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (request: DeleteCareEventRequest) => {
      if (!client) throw new Error("Real API client is not configured.");
      const snapshot = scope.snapshot();
      if (!snapshot.userId || snapshot.babyId !== request.babyId) {
        throw new ContractRequestError("CareEvent request is outside the active baby scope.");
      }
      const job = await deleteCareEvent(client, request);
      scope.assertCurrent(snapshot);
      return { job, snapshot };
    },
    onSuccess: ({ job, snapshot }, request) => {
      if (scope.snapshot() !== snapshot) return;
      queryClient.removeQueries({ queryKey: careEventKey(snapshot, request.babyId, request.careEventId) });
      queryClient.setQueryData(deletionKey(snapshot, job.deletion_job_id), job);
      void queryClient.invalidateQueries({ queryKey: timelineKey(snapshot, request.babyId) });
    },
  });
}

export function useCareEventDeletionQuery(deletionJobId: string, enabled: boolean) {
  const client = useApiClient();
  const scope = usePrivateScope();
  const snapshot = scope.snapshot();
  return useQuery({
    queryKey: snapshot.userId
      ? deletionKey(snapshot, deletionJobId)
      : ["private", null, "care-event-deletion", deletionJobId],
    queryFn: async () => {
      if (!client || !snapshot.userId) throw new Error("Real API client is not configured.");
      const job = await getCareEventDeletion(client, deletionJobId, snapshot.userId);
      scope.assertCurrent(snapshot);
      return job;
    },
    enabled: enabled && client !== null && snapshot.userId !== null,
  });
}

export function useRetryCareEventDeletionMutation() {
  const client = useApiClient();
  const scope = usePrivateScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (request: Readonly<{
      deletionJobId: string; expectedAttempt: number; clientRequestId: string;
    }>) => {
      if (!client) throw new Error("Real API client is not configured.");
      const snapshot = scope.snapshot();
      if (!snapshot.userId) throw new ContractRequestError("Deletion retry requires the requester session.");
      const job = await retryCareEventDeletion(
        client, request.deletionJobId, snapshot.userId, request.expectedAttempt, request.clientRequestId,
      );
      scope.assertCurrent(snapshot);
      return { job, snapshot };
    },
    onSuccess: ({ job, snapshot }) => {
      if (scope.snapshot() !== snapshot) return;
      queryClient.setQueryData(deletionKey(snapshot, job.deletion_job_id), job);
    },
  });
}

export function useLinkExistingCareEventMutation() {
  const client = useApiClient();
  const scope = usePrivateScope();
  return useMutation({
    mutationFn: async (request: LinkCareEventRequest) => {
      if (!client) throw new Error("Real API client is not configured.");
      const snapshot = scope.snapshot();
      if (!snapshot.userId || snapshot.babyId !== request.babyId) {
        throw new ContractRequestError("CareEvent request is outside the active baby scope.");
      }
      const action = await linkExistingCareEvent(client, request);
      scope.assertCurrent(snapshot);
      return action;
    },
  });
}
