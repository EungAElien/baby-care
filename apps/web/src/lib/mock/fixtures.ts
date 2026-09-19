// Typed access to the team contract's synthetic fixture (contracts/목 응답과
// 시험 사용자 배치.json, copied verbatim by `npm run generate:fixtures`).
// Every value here is `data_origin=DEMO` / `inference_mode=STUB` per the
// contract's section 11 — screens must show that, never real analysis.
import raw from "./fixtures.json";
import type { components } from "@/lib/api/generated";
import type { ApiErrorEnvelope } from "@/lib/api/errors";

export type MockScenarioName =
  | "baby_created"
  | "active_baby_lost_membership"
  | "analysis_complete_stub"
  | "analysis_running"
  | "analysis_abstain"
  | "analysis_no_cry"
  | "analysis_failed"
  | "analysis_in_progress"
  | "idempotent_replay"
  | "idempotency_mismatch"
  | "auth_expired"
  | "owner_required"
  | "not_member"
  | "edit_conflict"
  | "owner_cannot_leave"
  | "invite_expired"
  | "invite_wrong_email"
  | "invite_used"
  | "sleep_conflict"
  | "detection_conflict"
  | "normalization_review"
  | "draft_private"
  | "draft_other_author"
  | "normalization_failure"
  | "stale_draft"
  | "confirm_once"
  | "confirm_replay"
  | "deletion_accepted"
  | "resource_deleting"
  | "deletion_failed"
  | "deletion_complete"
  | "consent_revoke_after_leave"
  | "care_event_saved"
  | "file_rejected"
  | "model_unavailable"
  | "invite_issued"
  | "invite_replay_no_token"
  | "invite_accepted"
  | "member_left"
  | "normalization_running"
  | "normalization_stale"
  | "summary_empty"
  | "summary_unknown_amount"
  | "pattern_insufficient"
  | "similar_cases_empty"
  | "confirmed_state_visual";

export type MockScenario = Readonly<{
  name: MockScenarioName;
  operation_id: string;
  synthetic: true;
  expected_ui: string;
  response: Readonly<{ status: number; headers: Record<string, string>; body: unknown }>;
  request?: Readonly<{ headers: Record<string, string>; body: Record<string, unknown> }>;
}>;

export type MockTestUserAlias = "owner_a" | "caregiver_a" | "owner_b" | "invited_a" | "removed_a";

export type MockTestUser = Readonly<{
  alias: MockTestUserAlias;
  user_id: string;
  email_placeholder: string;
}>;

export type MockRoleAssignment = Readonly<{
  user_id: string;
  baby_id: string;
  role: "OWNER" | "CAREGIVER";
  status: "ACTIVE" | "REVOKED";
}>;

type FixtureFile = Readonly<{
  contract_version: string;
  notice: string;
  test_users: readonly MockTestUser[];
  test_babies: Readonly<Record<"baby_a" | "baby_b", string>>;
  role_assignments: readonly MockRoleAssignment[];
  scenarios: readonly MockScenario[];
}>;

const fixtures = raw as unknown as FixtureFile;

export const mockNotice = fixtures.notice;
export const mockTestUsers = fixtures.test_users;
export const mockTestBabies = fixtures.test_babies;
export const mockRoleAssignments = fixtures.role_assignments;

const scenariosByName = new Map<MockScenarioName, MockScenario>(
  fixtures.scenarios.map((scenario) => [scenario.name, scenario]),
);

export function getMockScenario(name: MockScenarioName): MockScenario {
  const scenario = scenariosByName.get(name);
  if (!scenario) throw new Error(`Missing contract fixture scenario: ${name}`);
  return scenario;
}

export function mockScenarioBody<T>(name: MockScenarioName): T {
  return getMockScenario(name).response.body as T;
}

/** ACTIVE memberships only — LEFT/REVOKED must not grant mock screen access. */
export function activeMembershipsFor(userId: string): readonly MockRoleAssignment[] {
  return mockRoleAssignments.filter((entry) => entry.user_id === userId && entry.status === "ACTIVE");
}

export function membershipFor(userId: string, babyId: string): MockRoleAssignment | null {
  return mockRoleAssignments.find(
    (entry) => entry.user_id === userId && entry.baby_id === babyId && entry.status === "ACTIVE",
  ) ?? null;
}

export function testUserByAlias(alias: MockTestUserAlias): MockTestUser {
  const user = mockTestUsers.find((entry) => entry.alias === alias);
  if (!user) throw new Error(`Unknown mock test user alias: ${alias}`);
  return user;
}

const babyLabels: Record<string, string> = {
  [mockTestBabies.baby_a]: "아기 A",
  [mockTestBabies.baby_b]: "아기 B",
};

/** Display label for a fixture baby_id; falls back to a short id for unknown values. */
export function mockBabyLabel(babyId: string): string {
  return babyLabels[babyId] ?? `아기(${babyId.slice(0, 8)})`;
}

// Screen-specific typed views onto scenario bodies, kept close to their
// schema so a screen never invents fields the contract does not have.
export type MockAnalysisScenarioName =
  | "analysis_complete_stub"
  | "analysis_running"
  | "analysis_abstain"
  | "analysis_no_cry"
  | "analysis_failed";

export function mockAnalysis(name: MockAnalysisScenarioName): components["schemas"]["Analysis"] {
  return mockScenarioBody(name);
}

export type MockNormalizationScenarioName =
  | "normalization_review"
  | "normalization_running"
  | "normalization_stale"
  | "normalization_failure";

export function mockNormalizationRun(name: MockNormalizationScenarioName): components["schemas"]["NormalizationRun"] {
  return mockScenarioBody(name);
}

export function mockCareEntry(): components["schemas"]["CareEntry"] {
  return mockScenarioBody("draft_private");
}

export function mockDailySummary(name: "summary_empty" | "summary_unknown_amount"): components["schemas"]["DailySummary"] {
  return mockScenarioBody(name);
}

export function mockPatterns(): components["schemas"]["Patterns"] {
  return mockScenarioBody("pattern_insufficient");
}

export function mockSimilarCases(): components["schemas"]["SimilarCases"] {
  return mockScenarioBody("similar_cases_empty");
}

export function mockCareEvent(): components["schemas"]["CareEvent"] {
  return mockScenarioBody("care_event_saved");
}

export function mockDeletionJob(
  name: "deletion_accepted" | "deletion_failed" | "deletion_complete",
): components["schemas"]["DeletionJob"] {
  return mockScenarioBody(name);
}

export function mockErrorEnvelope(name: MockScenarioName): ApiErrorEnvelope {
  return mockScenarioBody(name);
}

export function mockStateObservation(): components["schemas"]["StateObservation"] {
  return mockScenarioBody("confirmed_state_visual");
}
