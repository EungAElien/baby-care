import { NetworkRequestError, RequestCancelledError } from "@/lib/api/client";
import { ContractApiError } from "@/lib/api/errors";
import { ScopeChangedError } from "@/lib/private-scope";

/** A mutation may already have committed; only the original key/body can safely recover it. */
export function isCareEventOutcomeUnknown(error: unknown): boolean {
  return error instanceof NetworkRequestError || error instanceof RequestCancelledError ||
    error instanceof ScopeChangedError ||
    (error instanceof ContractApiError && (error.status === 500 || error.status === 503));
}
