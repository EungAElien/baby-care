#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";

const repoDir = fileURLToPath(new URL("..", import.meta.url)).replace(/\/$/, "");
const dbContainer = process.env.SUPABASE_DB_CONTAINER ?? "supabase_db_baby-care-b03-local";
const supabaseWorkdir = process.env.SUPABASE_WORKDIR ?? repoDir;
const npxCommand = process.platform === "win32" ? "npx.cmd" : "npx";

function localStatus() {
  const output = execFileSync(
    npxCommand,
    ["supabase", "--workdir", supabaseWorkdir, "status", "-o", "json"],
    { cwd: repoDir, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
  );
  return JSON.parse(output);
}

function assertUuid(value, label) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)) {
    throw new Error(`${label} is not a UUID`);
  }
  return value;
}

function decodeJwt(token) {
  const [, payload] = token.split(".");
  return JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
}

function runSql(sql) {
  execFileSync(
    "docker",
    ["exec", "-i", dbContainer, "psql", "-U", "postgres", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-f", "-"],
    { input: sql, encoding: "utf8", stdio: ["pipe", "ignore", "pipe"] },
  );
}

async function request(url, options = {}) {
  try {
    const response = await fetch(url, options);
    return {
      ok: response.ok,
      status: response.status,
      statusText: response.statusText,
      errorBody: response.ok ? "" : (await response.text()).slice(0, 500),
    };
  } catch (error) {
    return { ok: false, status: 0, statusText: error.name };
  }
}

const status = localStatus();
const apiUrl = status.API_URL;
const anonKey = status.ANON_KEY;
if (!apiUrl || !anonKey) {
  throw new Error("Local Supabase API URL or anon key is unavailable");
}

async function createAuthUser(label) {
  const email = `b03-${label}-${randomUUID()}@example.test`;
  const password = `Local-${randomUUID()}-Aa1!`;
  const response = await fetch(`${apiUrl}/auth/v1/signup`, {
    method: "POST",
    headers: { apikey: anonKey, "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const payload = await response.json();
  if (!response.ok || !payload.access_token) {
    throw new Error(`Local Auth signup failed for ${label}: HTTP ${response.status}`);
  }
  const claims = decodeJwt(payload.access_token);
  return {
    token: payload.access_token,
    userId: assertUuid(claims.sub, `${label} subject`),
    sessionId: assertUuid(claims.session_id, `${label} session`),
  };
}

const users = {
  owner: await createAuthUser("owner"),
  caregiver: await createAuthUser("caregiver"),
  ownerB: await createAuthUser("owner-b"),
  outsider: await createAuthUser("outsider"),
};

const babyA = randomUUID();
const babyB = randomUUID();
const episodeA = randomUUID();
const episodeB = randomUUID();

function makeUpload(name, babyId, episodeId, user, options = {}) {
  const audioId = randomUUID();
  const uploadId = randomUUID();
  return {
    name,
    babyId,
    episodeId,
    audioId,
    uploadId,
    uploaderId: user.userId,
    sessionId: options.sessionId ?? user.sessionId,
    objectKey: `${babyId}/${audioId}/${uploadId}`,
    assetStatus: options.assetStatus ?? "ALLOCATED",
    grantState: options.grantState ?? "active",
  };
}

const uploads = {
  ownerOk: makeUpload("owner upload", babyA, episodeA, users.owner),
  caregiverOk: makeUpload("caregiver upload", babyA, episodeA, users.caregiver),
  anonymous: makeUpload("anonymous request", babyA, episodeA, users.owner),
  outsider: makeUpload("nonmember", babyA, episodeA, users.outsider),
  otherBaby: makeUpload("other baby", babyB, episodeB, users.owner),
  wrongUploader: makeUpload("wrong uploader", babyA, episodeA, users.owner),
  expired: makeUpload("expired", babyA, episodeA, users.owner, { grantState: "expired" }),
  canceled: makeUpload("canceled", babyA, episodeA, users.owner, { grantState: "canceled" }),
  finalized: makeUpload("non-allocated asset", babyA, episodeA, users.owner, { assetStatus: "VERIFYING" }),
  sessionMismatch: makeUpload("session mismatch", babyA, episodeA, users.owner, { sessionId: randomUUID() }),
  oversize: makeUpload("oversize", babyA, episodeA, users.owner),
  afterMembershipRevoke: makeUpload("membership revoked", babyA, episodeA, users.caregiver),
  afterBabyDelete: makeUpload("baby deleting", babyB, episodeB, users.ownerB),
  afterSessionRevoke: makeUpload("session revoked", babyA, episodeA, users.owner),
};

function sqlLiteral(value) {
  if (!/^[0-9a-f/-]+$/i.test(value)) {
    throw new Error("Unsafe generated SQL literal");
  }
  return `'${value}'`;
}

const uploadSql = Object.values(uploads).map((upload) => {
  let recordedAt = "clock_timestamp()";
  let expiresAt = "clock_timestamp() + interval '14 minutes 59 seconds'";
  let canceledAt = "null";
  if (upload.grantState === "expired") {
    recordedAt = "clock_timestamp() - interval '10 minutes'";
    expiresAt = "clock_timestamp() - interval '1 minute'";
  } else if (upload.grantState === "canceled") {
    canceledAt = "clock_timestamp()";
  }
  return `
insert into baby_data.audio_assets (
  audio_id, baby_id, episode_id, created_by_user_id, object_key,
  mime_type, bytes, status, data_origin, version
) values (
  ${sqlLiteral(upload.audioId)}, ${sqlLiteral(upload.babyId)}, ${sqlLiteral(upload.episodeId)},
  ${sqlLiteral(upload.uploaderId)}, ${sqlLiteral(upload.objectKey)},
  'audio/wav', 0, '${upload.assetStatus}', 'DEMO', 1
);
insert into baby_data.audio_upload_grants (
  upload_id, baby_id, episode_id, audio_id, uploader_user_id, auth_session_id,
  object_key, method, max_bytes, recorded_at, expires_at, canceled_at
) values (
  ${sqlLiteral(upload.uploadId)}, ${sqlLiteral(upload.babyId)}, ${sqlLiteral(upload.episodeId)},
  ${sqlLiteral(upload.audioId)}, ${sqlLiteral(upload.uploaderId)}, ${sqlLiteral(upload.sessionId)},
  ${sqlLiteral(upload.objectKey)}, 'STANDARD', 25000000,
  ${recordedAt}, ${expiresAt}, ${canceledAt}
);`;
}).join("\n");

runSql(`
begin;
insert into baby_data.babies (
  baby_id, owner_user_id, alias, birth_date, feeding_mode, timezone, status, version
) values
  (${sqlLiteral(babyA)}, ${sqlLiteral(users.owner.userId)}, 'HTTP 합성 아기 A', current_date, 'MIXED', 'Asia/Seoul', 'ACTIVE', 1),
  (${sqlLiteral(babyB)}, ${sqlLiteral(users.ownerB.userId)}, 'HTTP 합성 아기 B', current_date, 'FORMULA', 'Asia/Seoul', 'ACTIVE', 1);
insert into baby_data.baby_memberships (
  membership_id, baby_id, user_id, role, relationship, display_name, status, version
) values
  (${sqlLiteral(randomUUID())}, ${sqlLiteral(babyA)}, ${sqlLiteral(users.owner.userId)}, 'OWNER', 'MOTHER', 'HTTP owner', 'ACTIVE', 1),
  (${sqlLiteral(randomUUID())}, ${sqlLiteral(babyA)}, ${sqlLiteral(users.caregiver.userId)}, 'CAREGIVER', 'FATHER', 'HTTP caregiver', 'ACTIVE', 1),
  (${sqlLiteral(randomUUID())}, ${sqlLiteral(babyB)}, ${sqlLiteral(users.ownerB.userId)}, 'OWNER', 'MOTHER', 'HTTP owner B', 'ACTIVE', 1);
insert into baby_data.episodes (
  episode_id, baby_id, created_by_user_id, source, timing_status, started_at, data_origin, version
) values
  (${sqlLiteral(episodeA)}, ${sqlLiteral(babyA)}, ${sqlLiteral(users.owner.userId)}, 'MANUAL', 'KNOWN', clock_timestamp(), 'DEMO', 1),
  (${sqlLiteral(episodeB)}, ${sqlLiteral(babyB)}, ${sqlLiteral(users.ownerB.userId)}, 'FILE', 'UNKNOWN', null, 'DEMO', 1);
${uploadSql}
commit;
`);

const results = [];
function record(name, expectation, response) {
  const allowed = response.status >= 200 && response.status < 300;
  const pass = expectation === "allow" ? allowed : !allowed;
  results.push({ name, expectation, status: response.status, pass });
  if (!pass) {
    throw new Error(
      `${name}: expected ${expectation}, received HTTP ${response.status} ${response.errorBody ?? ""}`,
    );
  }
}

function authHeaders(token, extras = {}) {
  return { apikey: anonKey, authorization: `Bearer ${token}`, ...extras };
}

async function upload(uploadCase, token, body = Buffer.from("synthetic wav bytes"), extras = {}) {
  return request(`${apiUrl}/storage/v1/object/baby-audio/${uploadCase.objectKey}`, {
    method: "POST",
    headers: authHeaders(token, { "content-type": "audio/wav", "x-upsert": "false", ...extras }),
    body,
  });
}

record("authenticated Data API table", "deny", await request(
  `${apiUrl}/rest/v1/babies?select=*`, { headers: authHeaders(users.owner.token) },
));
record("anonymous Data API table", "deny", await request(
  `${apiUrl}/rest/v1/babies?select=*`, { headers: { apikey: anonKey } },
));
record("authenticated direct RPC", "deny", await request(
  `${apiUrl}/rest/v1/rpc/can_upload_audio_object`, {
    method: "POST",
    headers: authHeaders(users.owner.token, { "content-type": "application/json" }),
    body: JSON.stringify({ requested_bucket_id: "baby-audio", requested_object_key: uploads.ownerOk.objectKey }),
  },
));

record("anonymous exact-key upload", "deny", await request(
  `${apiUrl}/storage/v1/object/baby-audio/${uploads.anonymous.objectKey}`, {
    method: "POST",
    headers: { apikey: anonKey, "content-type": "audio/wav" },
    body: Buffer.from("anonymous"),
  },
));
record("active owner upload", "allow", await upload(uploads.ownerOk, users.owner.token));
record("active caregiver upload", "allow", await upload(uploads.caregiverOk, users.caregiver.token));
record("nonmember upload", "deny", await upload(uploads.outsider, users.outsider.token));
record("other baby upload", "deny", await upload(uploads.otherBaby, users.owner.token));
record("other uploader exact key", "deny", await upload(uploads.wrongUploader, users.caregiver.token));

const arbitraryPath = `${babyA}/${randomUUID()}/${randomUUID()}`;
record("unregistered object key", "deny", await request(
  `${apiUrl}/storage/v1/object/baby-audio/${arbitraryPath}`, {
    method: "POST",
    headers: authHeaders(users.owner.token, { "content-type": "audio/wav" }),
    body: Buffer.from("unregistered"),
  },
));
record("expired upload grant", "deny", await upload(uploads.expired, users.owner.token));
record("canceled upload grant", "deny", await upload(uploads.canceled, users.owner.token));
record("non-allocated asset replacement", "deny", await upload(uploads.finalized, users.owner.token));
record("mismatched Auth session", "deny", await upload(uploads.sessionMismatch, users.owner.token));
record("25,000,001-byte upload", "deny", await upload(
  uploads.oversize,
  users.owner.token,
  Buffer.alloc(25000001, 1),
));
record("overwrite existing object", "deny", await upload(
  uploads.ownerOk,
  users.owner.token,
  Buffer.from("overwrite"),
  { "x-upsert": "true" },
));

record("browser object list", "deny", await request(`${apiUrl}/storage/v1/object/list/baby-audio`, {
  method: "POST",
  headers: authHeaders(users.owner.token, { "content-type": "application/json" }),
  body: JSON.stringify({ prefix: babyA, limit: 100 }),
}));
record("browser object download", "deny", await request(
  `${apiUrl}/storage/v1/object/authenticated/baby-audio/${uploads.ownerOk.objectKey}`,
  { headers: authHeaders(users.owner.token) },
));
record("browser signed URL", "deny", await request(
  `${apiUrl}/storage/v1/object/sign/baby-audio/${uploads.ownerOk.objectKey}`,
  {
    method: "POST",
    headers: authHeaders(users.owner.token, { "content-type": "application/json" }),
    body: JSON.stringify({ expiresIn: 3600 }),
  },
));
record("browser object delete", "deny", await request(
  `${apiUrl}/storage/v1/object/baby-audio/${uploads.ownerOk.objectKey}`,
  { method: "DELETE", headers: authHeaders(users.owner.token) },
));

runSql(`
update baby_data.baby_memberships
   set status = 'REVOKED', version = version + 1
 where baby_id = ${sqlLiteral(babyA)} and user_id = ${sqlLiteral(users.caregiver.userId)};
`);
record("existing JWT after membership revocation", "deny", await upload(
  uploads.afterMembershipRevoke,
  users.caregiver.token,
));

runSql(`
update baby_data.babies
   set status = 'DELETING', version = version + 1
 where baby_id = ${sqlLiteral(babyB)};
`);
record("existing JWT after baby deletion starts", "deny", await upload(
  uploads.afterBabyDelete,
  users.ownerB.token,
));

runSql(`
insert into baby_data.revoked_sessions (session_id, user_id, expires_at, reason)
values (${sqlLiteral(users.owner.sessionId)}, ${sqlLiteral(users.owner.userId)},
        clock_timestamp() + interval '1 hour', 'LOGOUT');
`);
record("revoked Auth session", "deny", await upload(
  uploads.afterSessionRevoke,
  users.owner.token,
));

console.log(JSON.stringify({ suite: "supabase-http", tests: results.length, results }, null, 2));
