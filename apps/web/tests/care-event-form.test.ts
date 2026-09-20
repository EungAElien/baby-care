import { describe, expect, it } from "vitest";
import { NetworkRequestError, RequestCancelledError } from "../src/lib/api/client";
import { ContractApiError } from "../src/lib/api/errors";
import { ScopeChangedError } from "../src/lib/private-scope";
import {
  buildCareEventValue,
  careEventValueToFormValues,
  careEventFormSchema,
  defaultCareEventFormValues,
  isCareEventValueEditable,
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

  it("prefills edit fields without turning unknown into zero or truncating seconds", () => {
    const event = {
      type: "FEEDING" as const,
      occurred_at: "2026-09-19T09:00:42.250Z",
      ended_at: null,
      time_precision: "EXACT" as const,
      payload: { mode: "FORMULA" as const, amount_ml: 0, duration_minutes: null },
    };
    expect(buildCareEventValue(careEventValueToFormValues(event))).toEqual(event);
    expect(careEventValueToFormValues({ ...event, occurred_at: null, time_precision: "UNKNOWN" }).amountMl).toBe("0");
    expect(careEventValueToFormValues({
      ...event, payload: { ...event.payload, amount_ml: null },
    }).amountMl).toBe("");
  });

  it("does not silently turn a relative-time confirmed event into an exact-time edit", () => {
    const relative = {
      type: "DIAPER" as const,
      occurred_at: "2026-09-19T09:00:00Z",
      ended_at: null,
      time_precision: "RELATIVE" as const,
      payload: { operation: "CHECK" as const, condition: "UNKNOWN" as const },
    };
    expect(isCareEventValueEditable(relative)).toBe(false);
    expect(() => careEventValueToFormValues(relative)).toThrow("revision draft");
    expect(isCareEventValueEditable({ ...relative, time_precision: "EXACT", ended_at: "2026-09-19T09:02:00Z" })).toBe(false);
  });
});
