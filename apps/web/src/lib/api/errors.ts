import type { components } from "./generated";

export type ContractErrorCode = components["schemas"]["ErrorCode"];

export type ApiErrorEnvelope = Readonly<{
  code: string;
  message: string;
  retryable: boolean;
  request_id: string;
  field_errors: ReadonlyArray<Readonly<{ field: string; code: string; message: string }>>;
  details: Readonly<Record<string, unknown>>;
}>;

export type ApiErrorKind =
  | "authentication"
  | "permission"
  | "not-found"
  | "version-conflict"
  | "operation-in-progress"
  | "idempotency-conflict"
  | "conflict"
  | "gone"
  | "validation"
  | "rate-limit"
  | "service"
  | "unknown";

const authenticationCodes = new Set<ContractErrorCode>([
  "AUTH_REQUIRED", "TOKEN_EXPIRED", "INVALID_TOKEN", "SESSION_REVOKED", "REAUTH_REQUIRED", "REAUTH_PROOF_INVALID",
]);
const permissionCodes = new Set<ContractErrorCode>([
  "OWNER_ONLY", "AUTHOR_ONLY", "INVITE_EMAIL_MISMATCH", "CONSENT_REQUIRED", "CHILD_DATA_VERIFICATION_REQUIRED",
]);
const inProgressCodes = new Set<ContractErrorCode>([
  "ANALYSIS_IN_PROGRESS", "NORMALIZATION_IN_PROGRESS", "OPERATION_IN_PROGRESS",
]);
const goneCodes = new Set<ContractErrorCode>(["INVITE_EXPIRED", "INVITE_REVOKED", "RESOURCE_DELETED"]);
const validationCodes = new Set<ContractErrorCode>([
  "FILE_TOO_LARGE", "UNSUPPORTED_MEDIA_TYPE", "VALIDATION_ERROR", "INVALID_AUDIO",
]);
const serviceCodes = new Set<ContractErrorCode>([
  "INTERNAL_ERROR", "MODEL_NOT_READY", "SERVICE_UNAVAILABLE", "AUTH_PROVIDER_REVOCATION_FAILED",
]);

export function classifyErrorCode(code: string): ApiErrorKind {
  if (authenticationCodes.has(code as ContractErrorCode)) return "authentication";
  if (permissionCodes.has(code as ContractErrorCode)) return "permission";
  if (code === "RESOURCE_NOT_FOUND") return "not-found";
  if (code === "VERSION_CONFLICT" || code === "SOURCE_REVISION_CHANGED") return "version-conflict";
  if (inProgressCodes.has(code as ContractErrorCode)) return "operation-in-progress";
  if (code === "IDEMPOTENCY_KEY_REUSED") return "idempotency-conflict";
  if (
    code === "OWNER_REQUIRED" || code === "ACTIVE_SESSION_EXISTS" || code === "SLEEP_ALREADY_ACTIVE" ||
    code === "RESOURCE_DELETING" || code === "ALREADY_MEMBER" || code === "INVITE_ALREADY_USED" ||
    code === "OWNER_BABY_LIMIT" || code === "ALREADY_CONFIRMED" || code === "INVALID_STATE"
  ) return "conflict";
  if (goneCodes.has(code as ContractErrorCode)) return "gone";
  if (validationCodes.has(code as ContractErrorCode)) return "validation";
  if (code === "RATE_LIMITED") return "rate-limit";
  if (serviceCodes.has(code as ContractErrorCode)) return "service";
  return "unknown";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseErrorEnvelope(value: unknown): ApiErrorEnvelope | null {
  if (!isRecord(value) || typeof value.code !== "string" || typeof value.message !== "string" ||
      typeof value.retryable !== "boolean" || typeof value.request_id !== "string" ||
      !Array.isArray(value.field_errors) || !isRecord(value.details)) return null;
  if (!value.field_errors.every((item) => isRecord(item) && typeof item.field === "string" &&
      typeof item.code === "string" && typeof item.message === "string")) return null;
  return value as ApiErrorEnvelope;
}

function retryAfterSeconds(header: string | null): number | null {
  if (!header || !/^\d+$/.test(header)) return null;
  return Number(header);
}

export class ContractApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly retryAfterSeconds: number | null;

  constructor(readonly status: number, readonly envelope: ApiErrorEnvelope, headers: Headers) {
    super(`API request failed: ${envelope.code}`);
    this.name = "ContractApiError";
    this.kind = classifyErrorCode(envelope.code);
    this.retryAfterSeconds = retryAfterSeconds(headers.get("Retry-After"));
  }
}

export class InvalidApiResponseError extends Error {
  constructor(readonly status: number) {
    super("The API response did not match the agreed error envelope.");
    this.name = "InvalidApiResponseError";
  }
}

export function requireData<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.response.ok && result.data !== undefined) return result.data;
  if (!result.response.ok) {
    const envelope = parseErrorEnvelope(result.error);
    if (envelope) throw new ContractApiError(result.response.status, envelope, result.response.headers);
  }
  throw new InvalidApiResponseError(result.response.status);
}
