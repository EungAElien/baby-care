"use client";

import { revokeSessions } from "@/lib/api/account-security";
import { newClientRequestId } from "@/lib/api/client";
import { ContractApiError } from "@/lib/api/errors";
import type { RealApiClient } from "@/lib/api/real-client";
import type { PrivateScope } from "@/lib/private-scope";

export type LogoutOutcome = "complete" | "provider-pending" | "unknown" | "local-failed";

/** Clear this browser even if the server response is lost; never label an unknown server result complete. */
export async function revokeAndSignOut(
  client: RealApiClient | null, scope: PrivateScope,
  signOut: () => Promise<{ ok: boolean }>, revocationScope: "CURRENT" | "ALL",
): Promise<LogoutOutcome> {
  let outcome: LogoutOutcome = "unknown";
  try {
    if (client) {
      const result = await revokeSessions(client, revocationScope, newClientRequestId());
      outcome = result.status === "COMPLETE" ? "complete" : "provider-pending";
    }
  } catch (error) {
    if (error instanceof ContractApiError && error.envelope.code === "AUTH_PROVIDER_REVOCATION_FAILED") {
      outcome = "provider-pending";
    }
  } finally {
    scope.reset();
    try {
      const local = await signOut();
      if (!local.ok) outcome = "local-failed";
    } catch {
      outcome = "local-failed";
    }
  }
  return outcome;
}
