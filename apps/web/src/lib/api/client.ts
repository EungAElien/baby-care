import createClient from "openapi-fetch";
import { PrivateScope } from "@/lib/private-scope";
import { validateApiBaseUrl } from "@/lib/public-config";
import type { paths } from "./generated";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const mutationMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export type ApiSession = Readonly<{ userId: string; accessToken: string }>;

export interface ApiAuthAdapter {
  getSession(): Promise<ApiSession | null>;
  refreshSession(): Promise<ApiSession | null>;
}

export interface ApiClientOptions {
  baseUrl: string;
  auth: ApiAuthAdapter;
  scope: PrivateScope;
  fetchImpl?: typeof fetch;
}

export class ContractRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ContractRequestError";
  }
}

export class AuthenticationBoundaryError extends Error {
  constructor() {
    super("The authenticated user does not match the active private-data scope.");
    this.name = "AuthenticationBoundaryError";
  }
}

export class NetworkRequestError extends Error {
  constructor() {
    super("The API request could not be completed. Its server outcome is unknown.");
    this.name = "NetworkRequestError";
  }
}

export class RequestCancelledError extends Error {
  constructor() {
    super("The API request was cancelled.");
    this.name = "RequestCancelledError";
  }
}

export function newClientRequestId(): string {
  return crypto.randomUUID();
}

export function idempotencyHeaders(clientRequestId: string): { "Idempotency-Key": string } {
  if (!uuidPattern.test(clientRequestId)) throw new ContractRequestError("A UUID client_request_id is required.");
  return { "Idempotency-Key": clientRequestId };
}

async function validateMutation(request: Request): Promise<void> {
  if (!mutationMethods.has(request.method)) return;
  const key = request.headers.get("Idempotency-Key");
  if (!key || !uuidPattern.test(key)) throw new ContractRequestError("Mutation requires a UUID Idempotency-Key.");
  if (request.method === "DELETE") {
    if (request.body) throw new ContractRequestError("DELETE must use the contract's version query, not a body.");
    return;
  }
  if (!request.headers.get("Content-Type")?.includes("application/json")) {
    throw new ContractRequestError("Mutation requires an application/json body.");
  }
  let body: unknown;
  try {
    body = await request.clone().json();
  } catch {
    throw new ContractRequestError("Mutation body must be valid JSON.");
  }
  if (typeof body !== "object" || body === null || Array.isArray(body) ||
      (body as Record<string, unknown>).client_request_id !== key) {
    throw new ContractRequestError("Idempotency-Key must equal body.client_request_id.");
  }
}

export function createApiClient(options: ApiClientOptions) {
  if (typeof window === "undefined" && !options.fetchImpl) {
    throw new ContractRequestError("Private API client must be created in the browser.");
  }
  const { baseUrl, auth, scope, fetchImpl = fetch } = options;
  const validatedBaseUrl = validateApiBaseUrl(baseUrl);
  const apiOrigin = new URL(validatedBaseUrl).origin;

  const authenticatedFetch: typeof fetch = async (input, init) => {
    const request = new Request(input, init);
    const target = new URL(request.url);
    if (target.origin !== apiOrigin || !target.pathname.startsWith("/v1/")) {
      throw new ContractRequestError("API requests must stay on the configured /v1 origin.");
    }
    await validateMutation(request);

    const snapshot = scope.snapshot();
    if (!snapshot.userId) throw new AuthenticationBoundaryError();
    const session = await auth.getSession();
    scope.assertCurrent(snapshot);
    if (!session || session.userId !== snapshot.userId || !session.accessToken) {
      scope.reset();
      throw new AuthenticationBoundaryError();
    }

    const controller = new AbortController();
    const untrack = scope.trackRequest(controller);
    const onAbort = () => controller.abort();
    request.signal.addEventListener("abort", onAbort, { once: true });
    if (request.signal.aborted) controller.abort();

    const send = async (token: string): Promise<Response> => {
      const headers = new Headers(request.headers);
      headers.set("Authorization", `Bearer ${token}`);
      const attempt = new Request(request.clone(), {
        headers,
        signal: controller.signal,
        cache: "no-store",
      });
      try {
        return await fetchImpl(attempt);
      } catch {
        scope.assertCurrent(snapshot);
        if (controller.signal.aborted) throw new RequestCancelledError();
        throw new NetworkRequestError();
      }
    };

    try {
      let response = await send(session.accessToken);
      scope.assertCurrent(snapshot);
      if (response.status === 401) {
        const refreshed = await auth.refreshSession();
        scope.assertCurrent(snapshot);
        if (!refreshed || !refreshed.accessToken) {
          scope.reset();
          return response;
        }
        if (refreshed.userId !== snapshot.userId) {
          scope.reset();
          throw new AuthenticationBoundaryError();
        }
        response = await send(refreshed.accessToken);
        scope.assertCurrent(snapshot);
        if (response.status === 401) scope.reset();
      }
      return response;
    } finally {
      request.signal.removeEventListener("abort", onAbort);
      untrack();
    }
  };

  return createClient<paths>({ baseUrl: validatedBaseUrl, fetch: authenticatedFetch });
}
