import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { components } from "../src/lib/api/generated";
import {
  AnalysisFlow,
  type AnalysisView,
} from "../src/lib/analysis/analysis-flow";
import { NetworkRequestError } from "../src/lib/api/client";
import { ContractApiError } from "../src/lib/api/errors";
import { PrivateScope } from "../src/lib/private-scope";
import type { RealApiClient } from "../src/lib/api/real-client";

type Analysis = components["schemas"]["Analysis"];

/** Builds a stored Analysis that always matches whichever analysis_id the flow under test generated. */
function analysisFor(id: string, overrides: Partial<Analysis> = {}): Analysis {
  return {
    analysis_id: id,
    baby_id: "baby",
    episode_id: "episode",
    audio_id: "audio",
    created_by_user_id: "user",
    status: "RUNNING",
    stage: "INFERENCE",
    attempt_no: 1,
    lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
    quality_status: "PENDING",
    quality_reasons: [],
    cry_detected: null,
    audio_candidates: [],
    abstain_reason: null,
    failure: null,
    model_version: null,
    preprocess_version: null,
    label_mapping_version: null,
    context_snapshot: null,
    recommendation: null,
    inference_mode: "REAL",
    inference_executed: false,
    data_origin: "USER",
    recorded_at: new Date().toISOString(),
    completed_at: null,
    ...overrides,
  };
}

function completeFor(id: string, overrides: Partial<Analysis> = {}): Analysis {
  return analysisFor(id, {
    status: "COMPLETE",
    stage: "FINISHED",
    lease_expires_at: null,
    quality_status: "PASS",
    cry_detected: true,
    audio_candidates: [{ code: "hungry", label: "배고픔 신호", rank: 1 }],
    model_version: "v1",
    preprocess_version: "pre-v1",
    label_mapping_version: "label-v1",
    recommendation: {
      recommendation_id: "rec",
      analysis_id: id,
      context_snapshot_id: null,
      status: "GENERAL_CHECKLIST",
      actions: [],
      optional_questions: [],
      help_action: null,
      policy_version: "policy-v1",
      supersedes_id: null,
      recorded_at: new Date().toISOString(),
    },
    completed_at: new Date().toISOString(),
    ...overrides,
  });
}

function abstainFor(id: string): Analysis {
  return analysisFor(id, {
    status: "ABSTAIN",
    stage: "FINISHED",
    lease_expires_at: null,
    quality_status: "INSUFFICIENT",
    cry_detected: false,
    audio_candidates: [],
    abstain_reason: "LOW_QUALITY",
    completed_at: new Date().toISOString(),
  });
}

function failedFor(id: string, attempt = 1): Analysis {
  return analysisFor(id, {
    status: "FAILED",
    stage: "FINISHED",
    lease_expires_at: null,
    attempt_no: attempt,
    completed_at: new Date().toISOString(),
    failure: {
      code: "INFERENCE_ERROR",
      message: "model crashed",
      retryable: true,
    },
  });
}

function apiError(
  status: number,
  code: string,
  extra: Partial<Record<string, unknown>> = {},
  headers = new Headers(),
): ContractApiError {
  return new ContractApiError(
    status,
    {
      code,
      message: "error",
      retryable: status === 429 || status === 503,
      request_id: "req",
      field_errors: [],
      details: {
        current_version: null,
        current_resource: null,
        resource_type: null,
        existing_analysis_id: null,
        existing_run_id: null,
        existing_session_id: null,
        deletion_job_id: null,
        session_revocation_id: null,
        reauthentication_challenge_id: null,
        status_url: null,
        retry_after_seconds: null,
        ...extra,
      },
    } as never,
    headers,
  );
}

function setup() {
  const scope = new PrivateScope(new QueryClient());
  scope.set("user", "baby");
  const views: AnalysisView[] = [];
  return { scope, views, listener: (view: AnalysisView) => views.push(view) };
}

type PostOptions = {
  params: { path?: Record<string, string>; header: Record<string, string> };
  body: Record<string, unknown>;
};
type GetOptions = { params: { path: Record<string, string> } };
const ok = (data: unknown) => ({ data, response: { ok: true } });

/** A manually-resolvable promise, used to simulate a POST that hasn't answered yet. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("A-06 ① analysis request/recovery", () => {
  it("sends analysis_id/client_request_id as the Idempotency-Key and body, and settles on the first COMPLETE response", async () => {
    const { scope, views, listener } = setup();
    const posts: Array<{
      path: string;
      body: Record<string, unknown>;
      key: string;
    }> = [];
    const api = {
      POST: vi.fn(async (path: string, options: PostOptions) => {
        posts.push({
          path,
          body: options.body,
          key: options.params.header["Idempotency-Key"] ?? "",
        });
        return ok(completeFor(String(options.body.analysis_id)));
      }),
    } as unknown as RealApiClient;
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      api,
      scope,
      listener,
    );
    flow.start();
    await vi.runAllTimersAsync();

    const uuidPattern =
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    expect(posts).toHaveLength(1);
    expect(posts[0]!.path).toBe("/episodes/{episode_id}/analyses");
    expect(posts[0]!.key).toBe(posts[0]!.body.client_request_id);
    expect(posts[0]!.body.analysis_id).toMatch(uuidPattern);
    expect(posts[0]!.body.analysis_id).not.toBe(
      posts[0]!.body.client_request_id,
    );
    expect(posts[0]!.body.audio_id).toBe("audio");
    expect(views.at(-1)?.phase).toBe("resolved");
    expect(views.at(-1)?.analysis?.status).toBe("COMPLETE");
    flow.dispose();
  });

  it("recovers a lost create response with a GET on the same analysis_id, never a second POST", async () => {
    const { scope, views, listener } = setup();
    let sentId = "";
    let postCount = 0;
    let getCount = 0;
    const api = {
      POST: vi.fn(async (_path: string, options: PostOptions) => {
        postCount++;
        sentId = String(options.body.analysis_id);
        throw new NetworkRequestError();
      }),
      GET: vi.fn(async (_path: string, options: GetOptions) => {
        getCount++;
        expect(options.params.path.analysis_id).toBe(sentId);
        return ok(completeFor(sentId));
      }),
    } as unknown as RealApiClient;
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      api,
      scope,
      listener,
    );
    flow.start();
    await vi.runAllTimersAsync();

    expect(postCount).toBe(1);
    expect(getCount).toBe(1);
    expect(views.at(-1)?.phase).toBe("resolved");
    expect(views.some((view) => view.phase === "recovering")).toBe(true);
    flow.dispose();
  });

  it("stops waiting on the POST after 65s and switches to GET recovery without creating a new analysis", async () => {
    const { scope, views, listener } = setup();
    let sentId = "";
    let getCount = 0;
    const pending = deferred<ReturnType<typeof ok>>();
    const api = {
      POST: vi.fn((_path: string, options: PostOptions) => {
        sentId = String(options.body.analysis_id);
        return pending.promise;
      }),
      GET: vi.fn(async () => {
        getCount++;
        return ok(analysisFor(sentId));
      }),
    } as unknown as RealApiClient;
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      api,
      scope,
      listener,
    );
    flow.start();
    await vi.advanceTimersByTimeAsync(65_000);

    expect(getCount).toBeGreaterThanOrEqual(1);
    expect(views.some((view) => view.phase === "recovering")).toBe(true);
    expect(api.POST).toHaveBeenCalledTimes(1);

    // The stale POST resolving afterwards must not overwrite the GET-driven state.
    const beforeLength = views.length;
    pending.resolve(ok(completeFor(sentId)));
    await vi.advanceTimersByTimeAsync(0);
    expect(views.length).toBe(beforeLength);
    flow.dispose();
  });

  it("polls RUNNING every 2 seconds and stops once a terminal status arrives", async () => {
    const { scope, views, listener } = setup();
    let sentId = "";
    let getCount = 0;
    const api = {
      POST: vi.fn(async (_path: string, options: PostOptions) => {
        sentId = String(options.body.analysis_id);
        return ok(analysisFor(sentId));
      }),
      GET: vi.fn(async () => {
        getCount++;
        return ok(getCount < 2 ? analysisFor(sentId) : completeFor(sentId));
      }),
    } as unknown as RealApiClient;
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      api,
      scope,
      listener,
    );
    flow.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(views.at(-1)?.phase).toBe("polling"); // RUNNING from the create POST itself, before any GET

    await vi.advanceTimersByTimeAsync(2_000);
    expect(getCount).toBe(1);
    expect(views.at(-1)?.phase).toBe("polling"); // first poll still RUNNING

    await vi.advanceTimersByTimeAsync(2_000);
    expect(getCount).toBe(2);
    expect(views.at(-1)?.phase).toBe("resolved");
    expect(views.at(-1)?.analysis?.status).toBe("COMPLETE");

    // No further polling after resolution.
    await vi.advanceTimersByTimeAsync(10_000);
    expect(getCount).toBe(2);
    flow.dispose();
  });

  it("stops polling when the baby or account scope changes mid-poll", async () => {
    const { scope, views, listener } = setup();
    let sentId = "";
    let getCount = 0;
    const api = {
      POST: vi.fn(async (_path: string, options: PostOptions) => {
        sentId = String(options.body.analysis_id);
        return ok(analysisFor(sentId));
      }),
      GET: vi.fn(async () => {
        getCount++;
        return ok(analysisFor(sentId));
      }),
    } as unknown as RealApiClient;
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      api,
      scope,
      listener,
    );
    scope.registerCleanup(() => flow.dispose());
    flow.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(views.at(-1)?.phase).toBe("polling");

    scope.set("user", "other-baby");
    const countAfterSwitch = getCount;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(getCount).toBe(countAfterSwitch);
  });

  it("discards a late response that resolves after the baby scope has already changed", async () => {
    const { scope, views, listener } = setup();
    let sentId = "";
    const pending = deferred<ReturnType<typeof ok>>();
    const api = {
      POST: vi.fn((_path: string, options: PostOptions) => {
        sentId = String(options.body.analysis_id);
        return pending.promise;
      }),
    } as unknown as RealApiClient;
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      api,
      scope,
      listener,
    );
    scope.registerCleanup(() => flow.dispose());
    flow.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(views.at(-1)?.phase).toBe("requesting");

    scope.set("user", "other-baby");
    const beforeLength = views.length;
    pending.resolve(ok(completeFor(sentId)));
    await vi.advanceTimersByTimeAsync(0);
    expect(views.length).toBe(beforeLength);
  });

  it("retries only a stored FAILED result with the same analysis_id, a fresh key and the current attempt_no", async () => {
    const { scope, views, listener } = setup();
    const posts: Array<{ path: string; body: Record<string, unknown> }> = [];
    let sentId = "";
    const api = {
      POST: vi.fn(async (path: string, options: PostOptions) => {
        posts.push({ path, body: options.body });
        if (path === "/episodes/{episode_id}/analyses") {
          sentId = String(options.body.analysis_id);
          return ok(failedFor(sentId, 1));
        }
        return ok(completeFor(sentId, { attempt_no: 2 }));
      }),
    } as unknown as RealApiClient;
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      api,
      scope,
      listener,
    );
    flow.start();
    await vi.runAllTimersAsync();
    expect(views.at(-1)?.analysis?.status).toBe("FAILED");
    const createBody = posts[0]!.body;

    flow.retryFailed();
    await vi.runAllTimersAsync();

    expect(posts).toHaveLength(2);
    expect(posts[1]!.path).toBe("/analyses/{analysis_id}/retry");
    expect(posts[1]!.body.expected_attempt).toBe(1);
    expect(posts[1]!.body.client_request_id).not.toBe(
      createBody.client_request_id,
    );
    expect(views.at(-1)?.analysis?.status).toBe("COMPLETE");
    flow.dispose();
  });

  it("refuses to retry a COMPLETE or ABSTAIN result", async () => {
    const { scope, views, listener } = setup();
    const api = {
      POST: vi.fn(async (_path: string, options: PostOptions) =>
        ok(completeFor(String(options.body.analysis_id))),
      ),
    } as unknown as RealApiClient;
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      api,
      scope,
      listener,
    );
    flow.start();
    await vi.runAllTimersAsync();
    expect(views.at(-1)?.analysis?.status).toBe("COMPLETE");
    expect(() => flow.retryFailed()).toThrow();

    views.length = 0;
    const abstainApi = {
      POST: vi.fn(async (_path: string, options: PostOptions) =>
        ok({
          ...abstainFor(String(options.body.analysis_id)),
          episode_id: "episode2",
          audio_id: "audio2",
        }),
      ),
    } as unknown as RealApiClient;
    const abstainFlow = new AnalysisFlow(
      "baby",
      "episode2",
      "audio2",
      abstainApi,
      scope,
      listener,
    );
    abstainFlow.start();
    await vi.runAllTimersAsync();
    expect(views.at(-1)?.analysis?.status).toBe("ABSTAIN");
    expect(views.at(-1)?.analysis?.audio_candidates).toEqual([]);
    expect(() => abstainFlow.retryFailed()).toThrow();
    flow.dispose();
    abstainFlow.dispose();
  });

  it("blocks on MODEL_NOT_READY without inventing a result, and does not enable a new analysis until the user resumes", async () => {
    const { scope, views, listener } = setup();
    let calls = 0;
    const api = {
      POST: vi.fn(async (_path: string, options: PostOptions) => {
        calls++;
        if (calls === 1)
          throw apiError(
            503,
            "MODEL_NOT_READY",
            { retry_after_seconds: 5 },
            new Headers({ "Retry-After": "5" }),
          );
        return ok(completeFor(String(options.body.analysis_id)));
      }),
    } as unknown as RealApiClient;
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      api,
      scope,
      listener,
    );
    flow.start();
    await vi.runAllTimersAsync();

    expect(views.at(-1)?.phase).toBe("blocked");
    expect(views.at(-1)?.errorCode).toBe("MODEL_NOT_READY");
    expect(views.at(-1)?.analysis).toBeNull();

    flow.resume();
    await vi.runAllTimersAsync();
    expect(calls).toBe(2);
    expect(views.at(-1)?.phase).toBe("resolved");
    flow.dispose();
  });

  it("blocks on 429 with the server's Retry-After and only continues when the user resumes", async () => {
    const { scope, views, listener } = setup();
    let calls = 0;
    const api = {
      POST: vi.fn(async (_path: string, options: PostOptions) => {
        calls++;
        if (calls === 1)
          throw apiError(
            429,
            "RATE_LIMITED",
            { retry_after_seconds: 3 },
            new Headers({ "Retry-After": "3" }),
          );
        return ok(completeFor(String(options.body.analysis_id)));
      }),
    } as unknown as RealApiClient;
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      api,
      scope,
      listener,
    );
    flow.start();
    await vi.runAllTimersAsync();

    expect(views.at(-1)?.phase).toBe("blocked");
    expect(views.at(-1)?.errorCode).toBe("RATE_LIMITED");
    expect(views.at(-1)?.retryAfterSeconds).toBe(3);
    expect(calls).toBe(1);

    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls).toBe(1); // no automatic infinite retry

    flow.resume();
    await vi.runAllTimersAsync();
    expect(calls).toBe(2);
    flow.dispose();
  });

  it("recovers ANALYSIS_IN_PROGRESS with the server-supplied existing analysis_id instead of creating a new one", async () => {
    const { scope, views, listener } = setup();
    const posts: string[] = [];
    const api = {
      POST: vi.fn(async (path: string) => {
        posts.push(path);
        throw apiError(409, "ANALYSIS_IN_PROGRESS", {
          existing_analysis_id: "existing-analysis",
        });
      }),
      GET: vi.fn(async () => ok(completeFor("existing-analysis"))),
    } as unknown as RealApiClient;
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      api,
      scope,
      listener,
    );
    flow.start();
    await vi.runAllTimersAsync();

    expect(posts).toEqual(["/episodes/{episode_id}/analyses"]);
    expect(api.GET).toHaveBeenCalledWith("/analyses/{analysis_id}", {
      params: { path: { analysis_id: "existing-analysis" } },
    });
    expect(views.at(-1)?.phase).toBe("resolved");
    expect(views.at(-1)?.analysis?.analysis_id).toBe("existing-analysis");
    flow.dispose();
  });

  it("re-reads the resource on VERSION_CONFLICT instead of auto-creating a new analysis", async () => {
    const { scope, views, listener } = setup();
    let sentId = "";
    const api = {
      POST: vi.fn(async (path: string, options: PostOptions) => {
        if (path === "/episodes/{episode_id}/analyses") {
          sentId = String(options.body.analysis_id);
          return ok(failedFor(sentId, 1));
        }
        throw apiError(409, "VERSION_CONFLICT", { current_version: 2 });
      }),
      GET: vi.fn(async () => ok(failedFor(sentId, 2))),
    } as unknown as RealApiClient;
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      api,
      scope,
      listener,
    );
    flow.start();
    await vi.runAllTimersAsync();
    expect(views.at(-1)?.analysis?.attempt_no).toBe(1);

    flow.retryFailed();
    await vi.runAllTimersAsync();

    expect(api.GET).toHaveBeenCalledTimes(1);
    expect(views.at(-1)?.phase).toBe("resolved");
    expect(views.at(-1)?.analysis?.attempt_no).toBe(2);
    flow.dispose();
  });

  it("does not auto-retry an IDEMPOTENCY_KEY_REUSED mismatch into a new analysis", async () => {
    const { scope, views, listener } = setup();
    const api = {
      POST: vi.fn(async () => {
        throw apiError(409, "IDEMPOTENCY_KEY_REUSED");
      }),
    } as unknown as RealApiClient;
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      api,
      scope,
      listener,
    );
    flow.start();
    await vi.runAllTimersAsync();

    expect(views.at(-1)?.phase).toBe("error");
    expect(views.at(-1)?.errorCode).toBe("IDEMPOTENCY_KEY_REUSED");
    expect(api.POST).toHaveBeenCalledTimes(1);
    flow.dispose();
  });

  it("surfaces a permission error without a retry affordance", async () => {
    const { scope, views, listener } = setup();
    const api = {
      POST: vi.fn(async () => {
        throw apiError(403, "CONSENT_REQUIRED");
      }),
    } as unknown as RealApiClient;
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      api,
      scope,
      listener,
    );
    flow.start();
    await vi.runAllTimersAsync();
    expect(views.at(-1)?.phase).toBe("error");
    expect(views.at(-1)?.errorKind).toBe("permission");
    flow.dispose();
  });
});

describe("synchronous analysis submission guards", () => {
  it("starts only one analysis when start is invoked twice before React renders", () => {
    const { scope, listener } = setup();
    const post = vi.fn(() => new Promise(() => {}));
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      { POST: post } as unknown as RealApiClient,
      scope,
      listener,
    );
    flow.start();
    flow.start();
    expect(post).toHaveBeenCalledTimes(1);
    flow.dispose();
  });
  it("allocates only one retry key while a failed attempt is being resubmitted", async () => {
    const { scope, listener } = setup();
    const post = vi.fn(async (_path: string, options: PostOptions) =>
      ok(failedFor(options.body.analysis_id as string)),
    );
    const flow = new AnalysisFlow(
      "baby",
      "episode",
      "audio",
      { POST: post } as unknown as RealApiClient,
      scope,
      listener,
    );
    flow.start();
    await Promise.resolve();
    await Promise.resolve();
    post.mockImplementation(() => new Promise(() => {}));
    flow.retryFailed();
    flow.retryFailed();
    expect(post).toHaveBeenCalledTimes(2);
    flow.dispose();
  });
});
