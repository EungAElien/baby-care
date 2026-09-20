import { z } from "zod";
import type { components } from "@/lib/api/generated";

function validNonNegativeAmount(value: string): boolean {
  return value.trim() === "" || (Number.isFinite(Number(value)) && Number(value) >= 0);
}

export function parseLocalDateTime(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/.exec(value);
  if (!match) return null;
  const [, year, month, day, hour, minute, second = "0", millisecond = "0"] = match;
  const date = new Date(
    Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute),
    Number(second), Number(millisecond.padEnd(3, "0")),
  );
  if (
    Number.isNaN(date.getTime()) ||
    date.getFullYear() !== Number(year) ||
    date.getMonth() + 1 !== Number(month) ||
    date.getDate() !== Number(day) ||
    date.getHours() !== Number(hour) ||
    date.getMinutes() !== Number(minute) ||
    date.getSeconds() !== Number(second) ||
    date.getMilliseconds() !== Number(millisecond.padEnd(3, "0"))
  ) return null;
  return date;
}

export const careEventFormSchema = z.object({
  type: z.enum(["FEEDING", "SLEEP", "DIAPER", "SOOTHE"]),
  timeUnknown: z.boolean(),
  occurredAt: z.string(),
  endedAt: z.string(),
  feedingMode: z.enum(["BREAST", "FORMULA", "MIXED", "UNSPECIFIED"]),
  amountMl: z.string(),
  durationMinutes: z.string(),
  diaperOperation: z.enum(["CHECK", "CHANGE"]),
  diaperCondition: z.enum(["WET", "STOOL", "BOTH", "CLEAN", "UNKNOWN"]),
  sootheAction: z.enum(["HOLDING", "BURPING", "SLEEP_PREPARATION", "ENVIRONMENT_ADJUSTMENT", "OTHER"]),
}).superRefine((value, context) => {
  if (value.type === "FEEDING") {
    if (!validNonNegativeAmount(value.amountMl)) {
      context.addIssue({ code: "custom", path: ["amountMl"], message: "0 이상의 숫자를 입력해 주세요." });
    }
    if (!validNonNegativeAmount(value.durationMinutes)) {
      context.addIssue({ code: "custom", path: ["durationMinutes"], message: "0 이상의 숫자를 입력해 주세요." });
    }
  }
  const needsExactTime = value.type === "SLEEP" || !value.timeUnknown;
  const start = needsExactTime ? parseLocalDateTime(value.occurredAt) : null;
  if (needsExactTime && !start) {
    context.addIssue({ code: "custom", path: ["occurredAt"], message: "실제 시각을 확인해 주세요." });
  }
  if (value.type === "SLEEP" && value.endedAt) {
    const end = parseLocalDateTime(value.endedAt);
    if (!end || (start && end <= start)) {
      context.addIssue({ code: "custom", path: ["endedAt"], message: "종료 시각은 시작보다 뒤여야 해요." });
    }
  }
});

export type CareEventFormValues = z.infer<typeof careEventFormSchema>;
export type CareEventValue = components["schemas"]["CareEventValue"];

function localDateTime(date: Date): string {
  const two = (value: number) => String(value).padStart(2, "0");
  const minute = `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())}T${two(date.getHours())}:${two(date.getMinutes())}`;
  if (date.getMilliseconds() !== 0) return `${minute}:${two(date.getSeconds())}.${String(date.getMilliseconds()).padStart(3, "0")}`;
  return date.getSeconds() === 0 ? minute : `${minute}:${two(date.getSeconds())}`;
}

export function isCareEventValueEditable(value: CareEventValue): boolean {
  if (value.type === "SLEEP") return value.time_precision === "EXACT" && value.occurred_at !== null;
  if (value.ended_at !== null) return false;
  return (value.time_precision === "EXACT" && value.occurred_at !== null) ||
    (value.time_precision === "UNKNOWN" && value.occurred_at === null);
}

/** Preserve unknown and zero values, including sub-minute timestamps, when pre-filling an edit. */
export function careEventValueToFormValues(value: CareEventValue): CareEventFormValues {
  if (!isCareEventValueEditable(value)) throw new Error("This CareEvent time precision needs a revision draft.");
  const base = defaultCareEventFormValues();
  const common = {
    ...base,
    type: value.type,
    timeUnknown: value.occurred_at === null,
    occurredAt: value.occurred_at === null ? "" : localDateTime(new Date(value.occurred_at)),
    endedAt: value.ended_at === null ? "" : localDateTime(new Date(value.ended_at)),
  };
  switch (value.type) {
    case "FEEDING": return {
      ...common, type: "FEEDING", feedingMode: value.payload.mode,
      amountMl: value.payload.amount_ml === null ? "" : String(value.payload.amount_ml),
      durationMinutes: value.payload.duration_minutes === null ? "" : String(value.payload.duration_minutes),
    };
    case "SLEEP": return { ...common, type: "SLEEP" };
    case "DIAPER": return {
      ...common, type: "DIAPER", diaperOperation: value.payload.operation,
      diaperCondition: value.payload.condition,
    };
    case "SOOTHE": return { ...common, type: "SOOTHE", sootheAction: value.payload.action_kind };
  }
}

export function defaultCareEventFormValues(now = new Date()): CareEventFormValues {
  return {
    type: "FEEDING",
    timeUnknown: false,
    occurredAt: localDateTime(now),
    endedAt: "",
    feedingMode: "UNSPECIFIED",
    amountMl: "",
    durationMinutes: "",
    diaperOperation: "CHECK",
    diaperCondition: "UNKNOWN",
    sootheAction: "OTHER",
  };
}

export function buildCareEventValue(input: CareEventFormValues): CareEventValue {
  const value = careEventFormSchema.parse(input);
  const occurredAt = value.timeUnknown && value.type !== "SLEEP"
    ? null
    : parseLocalDateTime(value.occurredAt)!.toISOString();
  const timePrecision = occurredAt === null ? "UNKNOWN" : "EXACT";

  switch (value.type) {
    case "FEEDING":
      return {
        type: "FEEDING",
        occurred_at: occurredAt,
        ended_at: null,
        time_precision: timePrecision,
        payload: {
          mode: value.feedingMode,
          amount_ml: value.amountMl.trim() === "" ? null : Number(value.amountMl),
          duration_minutes: value.durationMinutes.trim() === "" ? null : Number(value.durationMinutes),
        },
      };
    case "SLEEP":
      return {
        type: "SLEEP",
        occurred_at: occurredAt!,
        ended_at: value.endedAt ? parseLocalDateTime(value.endedAt)!.toISOString() : null,
        time_precision: "EXACT",
        payload: {},
      };
    case "DIAPER":
      return {
        type: "DIAPER",
        occurred_at: occurredAt,
        ended_at: null,
        time_precision: timePrecision,
        payload: { operation: value.diaperOperation, condition: value.diaperCondition },
      };
    case "SOOTHE":
      return {
        type: "SOOTHE",
        occurred_at: occurredAt,
        ended_at: null,
        time_precision: timePrecision,
        payload: { action_kind: value.sootheAction },
      };
  }
}
