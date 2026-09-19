import { describe, expect, it } from "vitest";
import { NetworkRequestError, RequestCancelledError } from "../src/lib/api/client";
import { ContractApiError } from "../src/lib/api/errors";
import { ScopeChangedError } from "../src/lib/private-scope";
import {
  buildCareEventValue,
  careEventFormSchema,
  defaultCareEventFormValues,
  parseLocalDateTime,
} from "../src/lib/care-events/form";
import { isCareEventOutcomeUnknown } from "../src/lib/care-events/request-outcome";

const now = new Date(2026, 8, 19, 9, 0);

describe("A-04 ① structured CareEvent input", () => {
  it("keeps unknown feeding amount distinct from an actual zero", () => {
    const base = { ...defaultCareEventFormValues(now), feedingMode: "FORMULA" as const };
    expect(buildCareEventValue(base)).toEqual({
      type: "FEEDING",
      occurred_at: now.toISOString(),
      ended_at: null,
      time_precision: "EXACT",
      payload: { mode: "FORMULA", amount_ml: null, duration_minutes: null },
    });
    expect(buildCareEventValue({ ...base, amountMl: "0" })).toMatchObject({
      payload: { amount_ml: 0 },
    });
  });

  it("supports unknown actual time without substituting recorded time", () => {
    const event = buildCareEventValue({
      ...defaultCareEventFormValues(now),
      type: "DIAPER",
      timeUnknown: true,
      diaperOperation: "CHANGE",
      diaperCondition: "WET",
    });
    expect(event).toEqual({
      type: "DIAPER",
      occurred_at: null,
      ended_at: null,
      time_precision: "UNKNOWN",
      payload: { operation: "CHANGE", condition: "WET" },
    });
  });

  it("represents an in-progress sleep with ended_at=null and a complete cross-day sleep as one event", () => {
    const base = { ...defaultCareEventFormValues(now), type: "SLEEP" as const };
    expect(buildCareEventValue(base)).toEqual({
      type: "SLEEP",
      occurred_at: now.toISOString(),
      ended_at: null,
      time_precision: "EXACT",
      payload: {},
    });
    const overnight = buildCareEventValue({
      ...base,
      occurredAt: "2026-09-19T23:00",
      endedAt: "2026-09-20T01:00",
    });
    expect(overnight.type).toBe("SLEEP");
    expect(overnight.ended_at).toBe(new Date(2026, 8, 20, 1, 0).toISOString());
  });

  it("maps other care only to the contract's SOOTHE action code", () => {
    expect(buildCareEventValue({
      ...defaultCareEventFormValues(now), type: "SOOTHE", sootheAction: "OTHER",
    })).toEqual({
      type: "SOOTHE",
      occurred_at: now.toISOString(),
      ended_at: null,
      time_precision: "EXACT",
      payload: { action_kind: "OTHER" },
    });
  });

  it("rejects negative quantities, invalid dates and sleep end before start", () => {
    const base = defaultCareEventFormValues(now);
    expect(careEventFormSchema.safeParse({ ...base, amountMl: "-1" }).success).toBe(false);
    expect(careEventFormSchema.safeParse({ ...base, type: "DIAPER", amountMl: "-1" }).success).toBe(true);
    expect(careEventFormSchema.safeParse({ ...base, occurredAt: "2026-02-30T09:00" }).success).toBe(false);
    expect(careEventFormSchema.safeParse({ ...base, type: "SLEEP", endedAt: "2026-09-19T08:59" }).success).toBe(false);
    expect(parseLocalDateTime("2026-02-30T09:00")).toBeNull();
  });

  it("keeps the original request after an uncertain network or scope interruption", () => {
    expect(isCareEventOutcomeUnknown(new NetworkRequestError())).toBe(true);
    expect(isCareEventOutcomeUnknown(new RequestCancelledError())).toBe(true);
    expect(isCareEventOutcomeUnknown(new ScopeChangedError())).toBe(true);
    const envelope = {
      code: "SLEEP_ALREADY_ACTIVE", message: "진행 중", retryable: false, request_id: "synthetic",
      field_errors: [], details: {},
    };
    expect(isCareEventOutcomeUnknown(new ContractApiError(409, envelope, new Headers()))).toBe(false);
    expect(isCareEventOutcomeUnknown(new ContractApiError(503, envelope, new Headers()))).toBe(true);
  });
});
