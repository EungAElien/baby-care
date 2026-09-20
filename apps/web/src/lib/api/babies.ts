"use client";

// A-03 연결 순서 1: 실제 Auth 세션 위에서 아기 목록/생성/활성 아기를 다룬다.
// 계약대로 Idempotency-Key와 body.client_request_id를 같은 UUID로 맞춘다.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePrivateScope } from "@/components/app-providers";
import { useApiClient } from "@/lib/api/real-client";
import { newClientRequestId, idempotencyHeaders, ContractRequestError } from "@/lib/api/client";
import type { RealApiClient } from "@/lib/api/real-client";
import { requireData } from "@/lib/api/errors";
import type { components } from "@/lib/api/generated";

export type CreateBabyInput = Readonly<{
  alias: string;
  birth_date: string;
  feeding_mode: components["schemas"]["CreateBaby"]["feeding_mode"];
  timezone: string;
}>;

export async function patchBaby(client: RealApiClient, babyId: string, body: components["schemas"]["PatchBaby"]) {
  const result = requireData(await client.PATCH("/babies/{baby_id}", {
    params: { path: { baby_id: babyId }, header: idempotencyHeaders(body.client_request_id) }, body,
  }));
  if (result.baby_id !== babyId) throw new ContractRequestError("Updated baby is outside the requested scope.");
  return result;
}

export function babiesKey(userId: string | null) {
  return ["real", userId, "babies"] as const;
}

export function activeBabyKey(userId: string | null) {
  return ["real", userId, "active-baby"] as const;
}

export function useBabiesQuery(enabled: boolean) {
  const client = useApiClient();
  const scope = usePrivateScope();
  const userId = scope.snapshot().userId;
  return useQuery({
    queryKey: babiesKey(userId),
    queryFn: async () => {
      if (!client) throw new Error("Real API client is not configured.");
      return requireData(await client.GET("/babies"));
    },
    enabled: enabled && client !== null && userId !== null,
  });
}

export function useCreateBabyMutation() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const scope = usePrivateScope();
  return useMutation({
    mutationFn: async (input: CreateBabyInput) => {
      if (!client) throw new Error("Real API client is not configured.");
      const clientRequestId = newClientRequestId();
      return requireData(
        await client.POST("/babies", {
          params: { header: idempotencyHeaders(clientRequestId) },
          body: { ...input, client_request_id: clientRequestId },
        }),
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: babiesKey(scope.snapshot().userId) });
    },
  });
}

export function useActiveBabyQuery(enabled: boolean) {
  const client = useApiClient();
  const scope = usePrivateScope();
  const userId = scope.snapshot().userId;
  return useQuery({
    queryKey: activeBabyKey(userId),
    queryFn: async () => {
      if (!client) throw new Error("Real API client is not configured.");
      return requireData(await client.GET("/babies/current"));
    },
    enabled: enabled && client !== null && userId !== null,
  });
}

export function useSetActiveBabyMutation() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const scope = usePrivateScope();
  return useMutation({
    mutationFn: async (babyId: string) => {
      if (!client) throw new Error("Real API client is not configured.");
      const clientRequestId = newClientRequestId();
      return requireData(
        await client.PUT("/me/active-baby", {
          params: { header: idempotencyHeaders(clientRequestId) },
          body: { client_request_id: clientRequestId, baby_id: babyId },
        }),
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: activeBabyKey(scope.snapshot().userId) });
    },
  });
}

export function findBabyAccess(
  babies: components["schemas"]["BabyList"] | undefined,
  babyId: string,
): components["schemas"]["BabyAccess"] | null {
  return babies?.items.find((item) => item.baby.baby_id === babyId) ?? null;
}
