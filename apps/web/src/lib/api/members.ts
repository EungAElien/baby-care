"use client";

// A-03 실제 연동 다음 단계: 구성원 조회/제거/관계 수정과 초대 조회/취소.
// createInvite·reissueInvite·acceptInvite는 이번 슬라이스에서 뺐다 —
// 앞 둘은 계약상 X-Reauthentication-Proof(CREATE_INVITE)가 필수이고,
// acceptInvite/setBabyConsent/setMyTrainingConsent는 policy_version을
// 어디서 읽어와야 하는지 계약·B-04 인계 어디에도 명시가 없다. 두 값 모두
// 지어내지 않고, 실제 값을 알려줄 때까지 목 화면으로 남겨둔다.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApiClient } from "@/lib/api/real-client";
import { newClientRequestId, idempotencyHeaders } from "@/lib/api/client";
import { requireData } from "@/lib/api/errors";
import type { components } from "@/lib/api/generated";

export function membersKey(babyId: string) {
  return ["real", "members", babyId] as const;
}

function invitesKey(babyId: string) {
  return ["real", "invites", babyId] as const;
}

export function useMembersQuery(babyId: string, enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: membersKey(babyId),
    queryFn: async () => {
      if (!client) throw new Error("Real API client is not configured.");
      return requireData(
        await client.GET("/babies/{baby_id}/members", { params: { path: { baby_id: babyId } } }),
      );
    },
    enabled: enabled && client !== null,
  });
}

export function useRemoveMembershipMutation(babyId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { userId: string; version: number }) => {
      if (!client) throw new Error("Real API client is not configured.");
      const clientRequestId = newClientRequestId();
      return requireData(
        await client.DELETE("/babies/{baby_id}/members/{user_id}", {
          params: {
            path: { baby_id: babyId, user_id: input.userId },
            query: { version: input.version },
            header: idempotencyHeaders(clientRequestId),
          },
        }),
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: membersKey(babyId) });
    },
  });
}

export function usePatchMyRelationshipMutation(babyId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      version: number;
      relationship: components["schemas"]["PatchMembership"]["relationship"];
    }) => {
      if (!client) throw new Error("Real API client is not configured.");
      const clientRequestId = newClientRequestId();
      return requireData(
        await client.PATCH("/babies/{baby_id}/members/me", {
          params: { path: { baby_id: babyId }, header: idempotencyHeaders(clientRequestId) },
          body: { client_request_id: clientRequestId, version: input.version, relationship: input.relationship },
        }),
      );
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: membersKey(babyId) }),
  });
}

export function useInvitesQuery(babyId: string, enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: invitesKey(babyId),
    queryFn: async () => {
      if (!client) throw new Error("Real API client is not configured.");
      return requireData(
        await client.GET("/babies/{baby_id}/invites", { params: { path: { baby_id: babyId } } }),
      );
    },
    enabled: enabled && client !== null,
  });
}

export function useRevokeInviteMutation(babyId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { inviteId: string; version: number }) => {
      if (!client) throw new Error("Real API client is not configured.");
      const clientRequestId = newClientRequestId();
      return requireData(
        await client.DELETE("/invites/{invite_id}", {
          params: {
            path: { invite_id: input.inviteId },
            query: { version: input.version },
            header: idempotencyHeaders(clientRequestId),
          },
        }),
      );
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: invitesKey(babyId) }),
  });
}
