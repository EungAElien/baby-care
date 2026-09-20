import { ContractRequestError, idempotencyHeaders } from "./client";
import { requireData } from "./errors";
import type { components } from "./generated";
import type { RealApiClient } from "./real-client";

export type Entry = components["schemas"]["CareEntry"];
export type Content = components["schemas"]["NormalizedContent"];
export type Run = components["schemas"]["NormalizationRun"];
export type Confirmation = components["schemas"]["ConfirmCareEntry"];
export type Confirmed = components["schemas"]["ConfirmedResources"];
export type Observation = components["schemas"]["StateObservation"];
export type Source = Pick<Entry, "input_mode" | "raw_text" | "choices" | "occurred_at" | "time_precision">;

/** Every private result is checked before it reaches a view, including OWNER sessions. */
export function careEntriesApi(client: RealApiClient, babyId: string, userId: string) {
  const check = (entry: Entry, id?: string) => {
    if (entry.baby_id !== babyId || entry.author_user_id !== userId || entry.episode_id !== null ||
      (id !== undefined && entry.entry_id !== id)) throw new ContractRequestError("Private entry scope mismatch.");
    return entry;
  };
  const checkRun = (run: Run, entryId: string, runId: string) => {
    if (run.entry_id !== entryId || run.run_id !== runId) throw new ContractRequestError("Normalization scope mismatch.");
    return run;
  };
  return {
    async capabilities() { return requireData(await client.GET("/capabilities")); },
    async list(cursor?: string) {
      const page = requireData(await client.GET("/babies/{baby_id}/care-entries", {
        params: { path: { baby_id: babyId }, query: cursor ? { cursor } : {} },
      }));
      // Event-linked entries may coexist in the author's list; they are outside this screen.
      for (const item of page.items) {
        if (item.baby_id !== babyId || item.author_user_id !== userId) throw new ContractRequestError("Private list scope mismatch.");
      }
      return { ...page, items: page.items.filter((item) => item.episode_id === null).map((item) => check(item)) };
    },
    async get(id: string) {
      return check(requireData(await client.GET("/care-entries/{entry_id}", { params: { path: { entry_id: id } } })), id);
    },
    async create(body: components["schemas"]["CreateCareEntry"]) {
      if (body.episode_id !== null) throw new ContractRequestError("Only eventless entries are supported.");
      return check(requireData(await client.POST("/babies/{baby_id}/care-entries", {
        params: { path: { baby_id: babyId }, header: idempotencyHeaders(body.client_request_id) }, body,
      })));
    },
    async patch(id: string, body: components["schemas"]["PatchCareEntry"]) {
      return check(requireData(await client.PATCH("/care-entries/{entry_id}", {
        params: { path: { entry_id: id }, header: idempotencyHeaders(body.client_request_id) }, body,
      })), id);
    },
    async normalize(id: string, body: components["schemas"]["CreateNormalization"]) {
      return checkRun(requireData(await client.POST("/care-entries/{entry_id}/normalizations", {
        params: { path: { entry_id: id }, header: idempotencyHeaders(body.client_request_id) }, body,
      })), id, body.run_id);
    },
    async run(entryId: string, runId: string) {
      return checkRun(requireData(await client.GET("/normalizations/{run_id}", {
        params: { path: { run_id: runId } },
      })), entryId, runId);
    },
    async confirm(id: string, body: Confirmation) {
      const result = requireData(await client.POST("/care-entries/{entry_id}/confirm", {
        params: { path: { entry_id: id }, header: idempotencyHeaders(body.client_request_id) }, body,
      }));
      if (result.entry_id !== id || result.input_revision !== body.input_revision) throw new ContractRequestError("Confirmation scope mismatch.");
      return result;
    },
    async observation(id: string) {
      const result = requireData(await client.GET("/state-observations/{state_observation_id}", {
        params: { path: { state_observation_id: id } },
      }));
      if (result.baby_id !== babyId || result.state_observation_id !== id) throw new ContractRequestError("Observation scope mismatch.");
      return result;
    },
    async baseRecords(versions: Entry["base_record_versions"]) {
      return Promise.all(versions.map(async (record) => {
        if (record.resource_type === "CARE_EVENT") {
          const current = requireData(await client.GET("/care-events/{care_event_id}", { params: { path: { care_event_id: record.resource_id } } }));
          if (current.baby_id !== babyId || current.care_event_id !== record.resource_id) throw new ContractRequestError("Base record scope mismatch.");
          return { record: { ...record, version: current.version }, summary: JSON.stringify(current.event) };
        }
        if (record.resource_type === "STATE_OBSERVATION") {
          const current = requireData(await client.GET("/state-observations/{state_observation_id}", { params: { path: { state_observation_id: record.resource_id } } }));
          if (current.baby_id !== babyId || current.state_observation_id !== record.resource_id) throw new ContractRequestError("Base observation scope mismatch.");
          return { record: { ...record, version: current.version }, summary: `${current.state_codes.join(" · ")} · ${current.observed_at ?? "시각 모름"}` };
        }
        throw new ContractRequestError("Event-linked correction is outside this screen.");
      }));
    },
  };
}
export type CareEntriesApi = ReturnType<typeof careEntriesApi>;
