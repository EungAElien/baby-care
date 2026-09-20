import { describe, expect, it, vi } from "vitest";
import { createApiClient } from "../src/lib/api/client";
import { requireData } from "../src/lib/api/errors";
import { PrivateScope } from "../src/lib/private-scope";
import { QueryClient } from "@tanstack/react-query";

// A-06 ①: the product gate must come from GET /capabilities verbatim — this
// only checks the plain request shape (no baby scoping, auth header present).
function setup(fetchImpl: typeof fetch) {
  const queryClient = new QueryClient();
  const scope = new PrivateScope(queryClient);
  scope.set("user-a", null);
  const auth = {
    getSession: vi.fn(async () => ({ userId: "user-a", accessToken: "token" })),
    refreshSession: vi.fn(async () => null),
  };
  return createApiClient({ baseUrl: "https://api.example.invalid/v1", auth, scope, fetchImpl });
}

describe("A-06 ① capabilities gate", () => {
  it("reads the server's audio_model.available with a plain authenticated GET", async () => {
    const requests: Request[] = [];
    const body = {
      contract_version: "1.2.0",
      audio_model: { available: false, model_version: null, preprocess_version: null, label_mapping_version: null, supported_labels: [], inference_mode: "STUB" },
      supported_mime_types: [],
      upload_max_bytes: 25000000,
      upload_max_seconds: 60,
      normalizer_available: false,
      normalizer_unavailable_reason: "DISABLED",
      automatic_detection_supported: false,
      detector: { available: false, execution_mode: "STUB", model_version: null, model_asset_url: null, weights_sha256: null, input_sample_rate_hz: null, policy_version: null, supported_clients: [] },
    };
    const fetchImpl = vi.fn(async (request: Request) => {
      requests.push(request);
      return Response.json(body, { status: 200 });
    }) as unknown as typeof fetch;
    const client = setup(fetchImpl);

    const result = requireData(await client.GET("/capabilities"));

    expect(requests).toHaveLength(1);
    expect(requests[0]!.method).toBe("GET");
    expect(new URL(requests[0]!.url).pathname).toBe("/v1/capabilities");
    expect(requests[0]!.headers.get("Authorization")).toBe("Bearer token");
    expect(result.audio_model.available).toBe(false);
  });
});
