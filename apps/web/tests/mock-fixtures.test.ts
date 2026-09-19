import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  activeMembershipsFor,
  getMockScenario,
  membershipFor,
  mockAnalysis,
  mockBabyLabel,
  mockRoleAssignments,
  mockTestBabies,
  mockTestUsers,
  testUserByAlias,
} from "../src/lib/mock/fixtures";

const sourcePath = resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json");
const sourceFixtures = JSON.parse(readFileSync(sourcePath, "utf8")) as {
  test_users: unknown[];
  scenarios: { name: string; synthetic: boolean }[];
};

describe("mock fixtures", () => {
  it("copies the contract fixture verbatim (no invented data)", () => {
    const copiedPath = resolve(process.cwd(), "src/lib/mock/fixtures.json");
    expect(readFileSync(copiedPath, "utf8")).toBe(readFileSync(sourcePath, "utf8"));
  });

  it("marks every scenario synthetic and exposes it by name", () => {
    expect(sourceFixtures.scenarios.every((scenario) => scenario.synthetic)).toBe(true);
    for (const scenario of sourceFixtures.scenarios) {
      expect(getMockScenario(scenario.name as never).name).toBe(scenario.name);
    }
  });

  it("keeps test users and role assignments identical to the contract", () => {
    expect(mockTestUsers).toHaveLength(sourceFixtures.test_users.length);
    expect(mockTestUsers).toEqual(sourceFixtures.test_users);
  });

  it("computes ACTIVE-only memberships per the dev contract's permission table", () => {
    const ownerA = testUserByAlias("owner_a");
    const removedA = testUserByAlias("removed_a");
    expect(activeMembershipsFor(ownerA.user_id)).toHaveLength(2);
    expect(activeMembershipsFor(removedA.user_id)).toHaveLength(0);
    expect(membershipFor(removedA.user_id, mockTestBabies.baby_a)).toBeNull();
    expect(mockRoleAssignments.some((entry) => entry.user_id === removedA.user_id && entry.status === "REVOKED")).toBe(
      true,
    );
  });

  it("labels the two fixture babies without inventing new ones", () => {
    expect(mockBabyLabel(mockTestBabies.baby_a)).toBe("아기 A");
    expect(mockBabyLabel(mockTestBabies.baby_b)).toBe("아기 B");
  });

  it("preserves inference_mode=STUB and data_origin=DEMO on analysis fixtures", () => {
    for (const name of ["analysis_complete_stub", "analysis_running", "analysis_abstain", "analysis_failed"] as const) {
      const analysis = mockAnalysis(name);
      expect(analysis.inference_mode).toBe("STUB");
      expect(analysis.data_origin).toBe("DEMO");
      expect(analysis.inference_executed).toBe(false);
    }
  });
});
