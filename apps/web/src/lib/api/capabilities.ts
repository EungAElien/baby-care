"use client";

// GET /capabilities is the only server-declared source for the analysis
// product gate (개발계약 §1 "audio_model.available"). A-06 must not enable the
// real analysis start action, or invent a readiness reason, from anything
// other than this response.
import { useQuery } from "@tanstack/react-query";
import { usePrivateScope } from "@/components/app-providers";
import { useApiClient } from "@/lib/api/real-client";
import { requireData } from "@/lib/api/errors";
import type { components } from "@/lib/api/generated";

export type Capabilities = components["schemas"]["Capabilities"];

export function capabilitiesKey(userId: string | null) {
  return ["real", userId, "capabilities"] as const;
}

export function useCapabilitiesQuery(enabled: boolean) {
  const client = useApiClient();
  const scope = usePrivateScope();
  const userId = scope.snapshot().userId;
  return useQuery({
    queryKey: capabilitiesKey(userId),
    queryFn: async () => {
      if (!client) throw new Error("Real API client is not configured.");
      return requireData(await client.GET("/capabilities"));
    },
    enabled: enabled && client !== null && userId !== null,
    // Capabilities can flip (release the product gate) between visits; do not
    // let a stale cached "not ready" outlive this component's own mount.
    staleTime: 0,
  });
}
