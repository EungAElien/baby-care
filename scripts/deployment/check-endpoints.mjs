// Read-only deployment check. No credentials or private response bodies are printed.
const [webValue, apiValue] = process.argv.slice(2);

function origin(value, name) {
  const url = new URL(value);
  if (url.protocol !== "https:" || url.username || url.password ||
      url.search || url.hash || url.pathname !== "/") {
    throw new Error(`${name} must be an HTTPS origin without a path or credentials`);
  }
  return url.origin;
}

async function request(url, options = {}) {
  return fetch(url, { ...options, redirect: "manual", signal: AbortSignal.timeout(20000) });
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
  console.log(`PASS ${message}`);
}

try {
  if (!webValue) throw new Error("Usage: node scripts/deployment/check-endpoints.mjs <web-origin> [api-origin]");
  const web = origin(webValue, "web");
  for (const path of ["/", "/login"]) {
    const response = await request(`${web}${path}`);
    assert(response.status === 200 && response.headers.get("content-type")?.includes("text/html"),
      `public web ${path} returns HTML 200 without credentials`);
  }
  if (apiValue) {
    const api = origin(apiValue, "api");
    const live = await request(`${api}/health/live`);
    assert(live.status === 200 && (await live.json()).status === "ok", "API liveness");
    const ready = await request(`${api}/health/ready`);
    assert(ready.status === 200 && (await ready.json()).status === "ready", "API Auth/DB readiness");
    const preflight = await request(`${api}/v1/babies`, {
      method: "OPTIONS",
      headers: { Origin: web, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization" },
    });
    assert(preflight.status === 200 && preflight.headers.get("access-control-allow-origin") === web,
      "CORS permits the exact web origin");
    const denied = await request(`${api}/v1/babies`, { headers: { Origin: web } });
    assert(denied.status === 401, "private API rejects unauthenticated requests");
    const wrongOrigin = await request(`${api}/v1/babies`, {
      method: "OPTIONS",
      headers: { Origin: "https://untrusted.invalid", "Access-Control-Request-Method": "GET" },
    });
    assert(wrongOrigin.status === 400 && !wrongOrigin.headers.get("access-control-allow-origin"),
      "CORS rejects an unlisted origin");
  } else {
    console.log("NOT CHECKED API, Auth, database, model, and signed-in user flows");
  }
} catch (error) {
  console.error(`FAIL ${error instanceof Error ? error.message : "deployment check failed"}`);
  process.exitCode = 1;
}
