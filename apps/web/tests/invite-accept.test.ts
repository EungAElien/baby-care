import { describe, expect, it } from "vitest";
import { isDemoToken } from "../src/app/invite/accept/page";

// PR #11 리뷰 필수 수정 — `in`은 프로토타입 체인까지 통과시켜
// #token=constructor 같은 값이 유효한 목 토큰으로 오인되고
// getMockScenario(Object)가 호출돼 화면이 중단되는 문제가 있었다.
describe("invite accept demo token allowlist", () => {
  it("accepts only the four defined demo tokens", () => {
    expect(isDemoToken("demo-accept")).toBe(true);
    expect(isDemoToken("demo-expired")).toBe(true);
    expect(isDemoToken("demo-wrong-email")).toBe(true);
    expect(isDemoToken("demo-used")).toBe(true);
  });

  it("rejects inherited Object.prototype keys instead of crashing on lookup", () => {
    expect(isDemoToken("constructor")).toBe(false);
    expect(isDemoToken("toString")).toBe(false);
    expect(isDemoToken("__proto__")).toBe(false);
    expect(isDemoToken("hasOwnProperty")).toBe(false);
  });

  it("rejects arbitrary and empty values", () => {
    expect(isDemoToken("")).toBe(false);
    expect(isDemoToken("not-a-real-token")).toBe(false);
  });
});
