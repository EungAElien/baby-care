-- B-04 account, shared-care, idempotency, session, and record API boundaries.
-- This migration follows the B-03 core/RLS migrations. Browser roles remain unable
-- to access baby_data or baby_private directly; only the FastAPI baby_app role is granted.

alter type baby_data.care_event_type add value if not exists 'SOOTHE';

alter table baby_data.care_events
    add column time_precision baby_data.time_precision not null default 'UNKNOWN',
    add column data_origin baby_data.data_origin not null default 'USER';

alter table baby_data.action_attempts
    add column data_origin baby_data.data_origin not null default 'USER';

alter table baby_data.raw_care_entries
    add column base_record_versions jsonb not null default '[]'::jsonb
        check (jsonb_typeof(base_record_versions) = 'array'),
    add column data_origin baby_data.data_origin not null default 'USER';

-- Evaluate visibility from the candidate row itself. The B-03 helper performs a
-- same-table lookup, whose STABLE statement snapshot cannot see a row being
-- inserted by `INSERT ... RETURNING`, even when the author is the caller.
drop policy raw_entries_author_select on baby_data.raw_care_entries;
create policy raw_entries_author_select on baby_data.raw_care_entries
    for select to baby_app
    using (
        baby_private.has_active_baby_access(baby_id)
        and (
            author_user_id = baby_private.current_request_user_id()
            or status = 'CONFIRMED'
        )
    );

create unique index invitations_token_sha256_unique
    on baby_data.invitations (token_sha256);

create type baby_data.idempotency_status as enum ('IN_PROGRESS', 'COMPLETE');
create type baby_data.reauthentication_operation as enum (
    'CREATE_INVITE',
    'DELETE_BABY',
    'ENABLE_BABY_TRAINING'
);
create type baby_data.reauthentication_status as enum ('PENDING', 'PROVED', 'EXPIRED');
create type baby_data.session_revocation_scope as enum ('CURRENT', 'OTHERS', 'ALL');
create type baby_data.session_revocation_status as enum ('PENDING', 'COMPLETE', 'FAILED');
create type baby_data.guardian_verification_status as enum (
    'UNVERIFIED',
    'SYNTHETIC_TEST_ONLY',
    'VERIFIED'
);

create table baby_data.idempotency_records (
    user_id uuid not null references auth.users(id) on delete cascade,
    method text not null check (method in ('POST', 'PUT', 'PATCH', 'DELETE')),
    target_path text not null check (target_path like '/v1/%'),
    idempotency_key uuid not null,
    request_sha256 text not null check (request_sha256 ~ '^[0-9a-f]{64}$'),
    status baby_data.idempotency_status not null default 'IN_PROGRESS',
    result_type text,
    result_id uuid,
    response_status integer check (response_status between 200 and 299),
    created_at timestamptz not null default clock_timestamp(),
    completed_at timestamptz,
    expires_at timestamptz not null default (clock_timestamp() + interval '7 days'),
    primary key (user_id, method, target_path, idempotency_key),
    check (expires_at >= created_at + interval '7 days'),
    check (
        (status = 'IN_PROGRESS' and completed_at is null and result_type is null and
            result_id is null and response_status is null)
        or
        (status = 'COMPLETE' and completed_at is not null and result_type is not null and
            response_status is not null)
    )
);

create index idempotency_records_expiry_idx
    on baby_data.idempotency_records (expires_at);

create table baby_data.security_attempts (
    attempt_id bigint generated always as identity primary key,
    user_id uuid not null references auth.users(id) on delete cascade,
    action text not null check (action in ('INVITE_CREATE', 'INVITE_ACCEPT', 'REAUTH_CHALLENGE', 'REAUTH_PROOF')),
    target_hash text not null check (target_hash ~ '^[0-9a-f]{64}$'),
    succeeded boolean not null,
    recorded_at timestamptz not null default clock_timestamp()
);

create index security_attempts_limit_idx
    on baby_data.security_attempts (user_id, action, target_hash, recorded_at desc);

create table baby_data.reauthentication_challenges (
    challenge_id uuid primary key default extensions.gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    requested_session_id uuid not null,
    operation baby_data.reauthentication_operation not null,
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    status baby_data.reauthentication_status not null default 'PENDING',
    created_at timestamptz not null default statement_timestamp(),
    expires_at timestamptz not null default (statement_timestamp() + interval '10 minutes'),
    proved_at timestamptz,
    check (expires_at = created_at + interval '10 minutes'),
    check ((status = 'PROVED') = (proved_at is not null))
);

create table baby_data.reauthentication_proofs (
    proof_id uuid primary key default extensions.gen_random_uuid(),
    challenge_id uuid not null unique
        references baby_data.reauthentication_challenges(challenge_id) on delete restrict,
    user_id uuid not null references auth.users(id) on delete cascade,
    session_id uuid not null,
    operation baby_data.reauthentication_operation not null,
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    token_sha256 bytea not null unique check (octet_length(token_sha256) = 32),
    issued_at timestamptz not null default statement_timestamp(),
    expires_at timestamptz not null default (statement_timestamp() + interval '5 minutes'),
    consumed_at timestamptz,
    consumed_request_id uuid,
    check (expires_at = issued_at + interval '5 minutes'),
    check ((consumed_at is null) = (consumed_request_id is null))
);

create table baby_data.session_revocation_jobs (
    revocation_id uuid primary key default extensions.gen_random_uuid(),
    requester_user_id uuid not null references auth.users(id) on delete cascade,
    requester_session_id uuid not null,
    scope baby_data.session_revocation_scope not null,
    status baby_data.session_revocation_status not null default 'PENDING',
    target_session_count integer not null check (target_session_count >= 0),
    provider_scope text not null check (provider_scope in ('local', 'others', 'global')),
    provider_http_status integer,
    failure_code text,
    access_blocked boolean not null default true check (access_blocked),
    requested_at timestamptz not null default clock_timestamp(),
    completed_at timestamptz,
    check (
        (status = 'PENDING' and completed_at is null and failure_code is null)
        or (status = 'COMPLETE' and completed_at is not null and failure_code is null)
        or (status = 'FAILED' and completed_at is null and failure_code is not null)
    )
);

create table baby_data.observed_auth_sessions (
    user_id uuid not null references auth.users(id) on delete cascade,
    session_id uuid not null,
    token_issued_at timestamptz not null,
    token_expires_at timestamptz not null,
    first_seen_at timestamptz not null default clock_timestamp(),
    last_seen_at timestamptz not null default clock_timestamp(),
    primary key (user_id, session_id),
    check (token_expires_at > token_issued_at)
);

create table baby_data.session_revocation_rules (
    rule_id uuid primary key default extensions.gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    scope baby_data.session_revocation_scope not null,
    requester_session_id uuid not null,
    revoked_at timestamptz not null default clock_timestamp(),
    expires_at timestamptz not null default (clock_timestamp() + interval '30 days'),
    check (scope in ('OTHERS', 'ALL')),
    check (expires_at > revoked_at)
);

create index session_revocation_rules_user_expiry_idx
    on baby_data.session_revocation_rules (user_id, expires_at);

create table baby_data.guardian_verifications (
    verification_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    subject_user_id uuid not null references auth.users(id) on delete restrict,
    status baby_data.guardian_verification_status not null default 'UNVERIFIED',
    method text,
    policy_version text,
    verified_at timestamptz,
    recorded_at timestamptz not null default clock_timestamp(),
    unique (baby_id, subject_user_id),
    check (
        (status in ('UNVERIFIED', 'SYNTHETIC_TEST_ONLY') and verified_at is null)
        or (status = 'VERIFIED' and verified_at is not null and method is not null and
            policy_version is not null)
    )
);

create table baby_data.resource_invalidations (
    invalidation_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    source_resource_type text not null check (source_resource_type in ('CARE_EVENT', 'CARE_ENTRY')),
    source_resource_id uuid not null,
    source_version bigint not null check (source_version >= 1),
    invalidated_categories text[] not null default array['SUMMARY', 'RECOMMENDATION', 'PERSONALIZATION'],
    reason text not null check (reason in ('CREATED', 'UPDATED', 'DELETION_REQUESTED')),
    context_revision bigint not null check (context_revision >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    unique (baby_id, source_resource_type, source_resource_id, context_revision)
);

-- A request is live only while the session has not been locally revoked. ALL and OTHERS
-- cutoff rules also block previously unseen access JWTs issued before revocation.
grant baby_policy_owner to postgres;
grant create on schema baby_private to baby_policy_owner;
grant select on baby_data.session_revocation_rules to baby_policy_owner;

set local role baby_policy_owner;
create or replace function baby_private.current_request_issued_at()
returns timestamptz
language sql
stable
set search_path = pg_catalog
as $$
    select nullif(current_setting('baby.request_issued_at', true), '')::timestamptz
$$;

create or replace function baby_private.has_live_request_context()
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $$
    select baby_private.current_request_user_id() is not null
       and baby_private.current_request_session_id() is not null
       and not exists (
           select 1
             from baby_data.revoked_sessions rs
            where rs.session_id = baby_private.current_request_session_id()
              and rs.user_id = baby_private.current_request_user_id()
              and rs.expires_at > statement_timestamp()
       )
       and not exists (
           select 1
             from baby_data.session_revocation_rules rr
            where rr.user_id = baby_private.current_request_user_id()
              and rr.expires_at > statement_timestamp()
              and coalesce(
                    baby_private.current_request_issued_at(),
                    '-infinity'::timestamptz
                  ) <= rr.revoked_at
              and (
                  rr.scope = 'ALL'
                  or rr.scope = 'OTHERS'
                     and baby_private.current_request_session_id() <> rr.requester_session_id
              )
       )
$$;
reset role;

alter function baby_private.current_request_issued_at() owner to baby_policy_owner;
alter function baby_private.has_live_request_context() owner to baby_policy_owner;
revoke all on function baby_private.current_request_issued_at() from public, anon, authenticated, service_role;
revoke all on function baby_private.has_live_request_context() from public, anon, authenticated, service_role;
grant execute on function baby_private.current_request_issued_at() to baby_app, baby_policy_owner;
grant execute on function baby_private.has_live_request_context() to baby_app;

-- Invitation acceptance is the only membership bootstrap after baby creation. The
-- callable surface is restricted to baby_app and validates the live request context,
-- token digest, verified JWT email, expiry, single-use state, and duplicate membership.
create or replace function baby_private.accept_invitation(
    supplied_token_sha256 bytea,
    verified_email text,
    accepted_relationship text,
    accepted_policy_version text
)
returns table (accepted_baby_id uuid, accepted_membership_id uuid)
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    target_invite baby_data.invitations%rowtype;
    previous_shared_consent baby_data.consents%rowtype;
    new_membership_id uuid;
    request_user_id uuid := baby_private.current_request_user_id();
begin
    if not baby_private.has_live_request_context() then
        raise exception using errcode = '42501', message = 'B04_SESSION_REVOKED';
    end if;
    if accepted_relationship not in ('MOTHER', 'FATHER', 'GRANDPARENT', 'OTHER') then
        raise exception using errcode = '22023', message = 'B04_INVALID_RELATIONSHIP';
    end if;

    select i.* into target_invite
      from baby_data.invitations i
     where i.token_sha256 = supplied_token_sha256
     for update;

    if not found then
        raise exception using errcode = 'P0002', message = 'B04_INVITE_NOT_FOUND';
    end if;
    if target_invite.status = 'ACCEPTED' then
        raise exception using errcode = '23505', message = 'B04_INVITE_ALREADY_USED';
    end if;
    if target_invite.status = 'REVOKED' then
        raise exception using errcode = '22023', message = 'B04_INVITE_REVOKED';
    end if;
    if target_invite.status = 'EXPIRED' or target_invite.expires_at <= statement_timestamp() then
        raise exception using errcode = '22023', message = 'B04_INVITE_EXPIRED';
    end if;
    if lower(target_invite.email) <> lower(verified_email) then
        raise exception using errcode = '42501', message = 'B04_INVITE_EMAIL_MISMATCH';
    end if;
    if exists (
        select 1 from baby_data.baby_memberships m
         where m.baby_id = target_invite.baby_id
           and m.user_id = request_user_id
           and m.status = 'ACTIVE'
    ) then
        raise exception using errcode = '23505', message = 'B04_ALREADY_MEMBER';
    end if;

    insert into baby_data.baby_memberships (
        baby_id, user_id, role, relationship, display_name, status
    ) values (
        target_invite.baby_id,
        request_user_id,
        'CAREGIVER',
        accepted_relationship,
        left(split_part(verified_email, '@', 1), 80),
        'ACTIVE'
    ) returning membership_id into new_membership_id;

    select c.* into previous_shared_consent
      from baby_data.consents c
     where c.baby_id = target_invite.baby_id
       and c.actor_user_id = request_user_id
       and c.scope = 'SHARED_USE'
     order by c.version desc
     limit 1;

    insert into baby_data.consents (
        baby_id, actor_user_id, scope, status, policy_version, granted_at,
        supersedes_consent_id, version
    ) values (
        target_invite.baby_id,
        request_user_id,
        'SHARED_USE',
        'GRANTED',
        accepted_policy_version,
        clock_timestamp(),
        previous_shared_consent.consent_id,
        coalesce(previous_shared_consent.version + 1, 1)
    );

    update baby_data.invitations
       set status = 'ACCEPTED',
           accepted_by_user_id = request_user_id,
           accepted_at = clock_timestamp(),
           version = version + 1
     where invite_id = target_invite.invite_id;

    return query select target_invite.baby_id, new_membership_id;
end;
$$;

alter function baby_private.accept_invitation(bytea, text, text, text) owner to baby_policy_owner;
revoke all on function baby_private.accept_invitation(bytea, text, text, text)
    from public, anon, authenticated, service_role;
grant execute on function baby_private.accept_invitation(bytea, text, text, text) to baby_app;

-- Bootstrap a baby and its only OWNER membership atomically. Direct RLS cannot safely
-- authorize either half before the other exists, so the narrowly scoped definer checks
-- the live signed request context and owns both inserts.
create or replace function baby_private.create_baby_with_owner(
    baby_alias text,
    baby_birth_date date,
    baby_feeding_mode text,
    baby_timezone text,
    owner_display_name text
)
returns table (created_baby_id uuid, created_membership_id uuid)
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    request_user_id uuid := baby_private.current_request_user_id();
    new_baby_id uuid := pg_catalog.gen_random_uuid();
    new_membership_id uuid := pg_catalog.gen_random_uuid();
begin
    if not baby_private.has_live_request_context() then
        raise exception using errcode = '42501', message = 'B04_SESSION_REVOKED';
    end if;

    insert into baby_data.babies (
        baby_id, owner_user_id, alias, birth_date, feeding_mode, timezone
    ) values (
        new_baby_id, request_user_id, baby_alias, baby_birth_date,
        baby_feeding_mode, baby_timezone
    );
    insert into baby_data.baby_memberships (
        membership_id, baby_id, user_id, role, relationship, display_name
    ) values (
        new_membership_id, new_baby_id, request_user_id,
        'OWNER', 'OTHER', owner_display_name
    );

    return query select new_baby_id, new_membership_id;
end;
$$;

alter function baby_private.create_baby_with_owner(text, date, text, text, text)
    owner to baby_policy_owner;
revoke all on function baby_private.create_baby_with_owner(text, date, text, text, text)
    from public, anon, authenticated, service_role;
grant execute on function baby_private.create_baby_with_owner(text, date, text, text, text)
    to baby_app;

-- Any current caregiver may create shared records, so derived-context invalidation
-- cannot depend on the OWNER-only baby update policy.
create or replace function baby_private.advance_baby_context_revision(target_baby_id uuid)
returns bigint
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    next_revision bigint;
begin
    if not baby_private.has_live_request_context()
       or not exists (
           select 1
             from baby_data.baby_memberships m
             join baby_data.babies b on b.baby_id = m.baby_id
            where m.baby_id = target_baby_id
              and m.user_id = baby_private.current_request_user_id()
              and m.status = 'ACTIVE'
              and b.status = 'ACTIVE'
       ) then
        return null;
    end if;

    update baby_data.babies
       set context_revision = context_revision + 1,
           version = version + 1
     where baby_id = target_baby_id and status = 'ACTIVE'
    returning context_revision into next_revision;
    return next_revision;
end;
$$;

alter function baby_private.advance_baby_context_revision(uuid)
    owner to baby_policy_owner;
revoke all on function baby_private.advance_baby_context_revision(uuid)
    from public, anon, authenticated, service_role;
grant execute on function baby_private.advance_baby_context_revision(uuid) to baby_app;

-- The observed-session registry plus cutoff rules avoid granting runtime access to
-- Supabase's auth schema while covering both seen and previously unseen access JWTs.
create or replace function baby_private.begin_session_revocation(
    requested_scope baby_data.session_revocation_scope
)
returns table (
    created_revocation_id uuid,
    created_target_count integer,
    created_provider_scope text
)
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    request_user_id uuid := baby_private.current_request_user_id();
    request_session_id uuid := baby_private.current_request_session_id();
    new_revocation_id uuid := pg_catalog.gen_random_uuid();
    affected_count integer;
    mapped_scope text;
begin
    if not baby_private.has_live_request_context() then
        raise exception using errcode = '42501', message = 'B04_SESSION_REVOKED';
    end if;

    mapped_scope := case requested_scope
        when 'CURRENT' then 'local'
        when 'OTHERS' then 'others'
        when 'ALL' then 'global'
    end;

    if requested_scope in ('OTHERS', 'ALL') then
        insert into baby_data.session_revocation_rules (
            user_id, scope, requester_session_id
        ) values (
            request_user_id, requested_scope, request_session_id
        );
    end if;

    with target_sessions as (
        select s.session_id as id
          from baby_data.observed_auth_sessions s
         where s.user_id = request_user_id
           and case requested_scope
               when 'CURRENT' then s.session_id = request_session_id
               when 'OTHERS' then s.session_id <> request_session_id
               when 'ALL' then true
           end
        union
        select request_session_id where requested_scope in ('CURRENT', 'ALL')
    ), inserted as (
        insert into baby_data.revoked_sessions (
            session_id, user_id, expires_at, reason
        )
        select id,
               request_user_id,
               clock_timestamp() + interval '30 days',
               case when requested_scope = 'ALL' then 'ALL_SESSIONS' else 'LOGOUT' end
          from target_sessions
        on conflict (session_id) do update
            set expires_at = greatest(
                    baby_data.revoked_sessions.expires_at,
                    excluded.expires_at
                ),
                reason = excluded.reason
        returning 1
    )
    select count(*)::integer into affected_count from inserted;

    insert into baby_data.session_revocation_jobs (
        revocation_id,
        requester_user_id,
        requester_session_id,
        scope,
        target_session_count,
        provider_scope
    ) values (
        new_revocation_id,
        request_user_id,
        request_session_id,
        requested_scope,
        affected_count,
        mapped_scope
    );

    return query select new_revocation_id, affected_count, mapped_scope;
end;
$$;

alter function baby_private.begin_session_revocation(baby_data.session_revocation_scope)
    owner to baby_policy_owner;
revoke all on function baby_private.begin_session_revocation(baby_data.session_revocation_scope)
    from public, anon, authenticated, service_role;
grant execute on function baby_private.begin_session_revocation(baby_data.session_revocation_scope)
    to baby_app;

-- A former caregiver retains only this own-rights path. It blocks their mutable
-- contribution lineage and creates a cleanup job without reopening baby reads.
create or replace function baby_private.request_own_contribution_deletion(
    target_baby_id uuid
)
returns uuid
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    request_user_id uuid := baby_private.current_request_user_id();
    new_job_id uuid := pg_catalog.gen_random_uuid();
    new_context_revision bigint;
begin
    if not baby_private.has_live_request_context() then
        raise exception using errcode = '42501', message = 'B04_SESSION_REVOKED';
    end if;
    if not exists (
        select 1 from baby_data.baby_memberships m
         where m.baby_id = target_baby_id
           and m.user_id = request_user_id
    ) then
        raise exception using errcode = 'P0002', message = 'B04_RESOURCE_NOT_FOUND';
    end if;

    update baby_data.babies b
       set context_revision = context_revision + 1,
           version = version + 1
     where b.baby_id = target_baby_id
       and b.status = 'ACTIVE'
    returning context_revision into new_context_revision;

    if new_context_revision is null then
        raise exception using errcode = '55000', message = 'B04_RESOURCE_DELETING';
    end if;

    insert into baby_data.resource_invalidations (
        baby_id,
        source_resource_type,
        source_resource_id,
        source_version,
        reason,
        context_revision
    )
    select target_baby_id,
           'CARE_EVENT',
           e.care_event_id,
           e.version,
           'DELETION_REQUESTED',
           new_context_revision
      from baby_data.care_events e
     where e.baby_id = target_baby_id
       and e.created_by_user_id = request_user_id
       and e.status = 'ACTIVE';

    update baby_data.care_events e
       set status = 'DELETING', version = version + 1
     where e.baby_id = target_baby_id
       and e.created_by_user_id = request_user_id
       and e.status = 'ACTIVE';

    update baby_data.action_attempts a
       set status = 'DELETING', version = version + 1
     where a.baby_id = target_baby_id
       and a.created_by_user_id = request_user_id
       and a.status = 'ACTIVE';

    update baby_data.normalization_runs n
       set status = 'STALE',
           lease_expires_at = null,
           completed_at = clock_timestamp()
     where n.baby_id = target_baby_id
       and n.entry_id in (
           select e.entry_id
             from baby_data.raw_care_entries e
            where e.baby_id = target_baby_id
              and e.author_user_id = request_user_id
              and e.status not in ('DELETING', 'DELETED')
       )
       and n.status in ('RUNNING', 'COMPLETE', 'FAILED');

    update baby_data.raw_care_entries e
       set status = 'DELETING', version = version + 1
     where e.baby_id = target_baby_id
       and e.author_user_id = request_user_id
       and e.status not in ('DELETING', 'DELETED');

    insert into baby_data.deletion_jobs (
        deletion_job_id,
        requester_user_id,
        baby_id,
        scope,
        resource_id,
        pending_categories
    ) values (
        new_job_id,
        request_user_id,
        target_baby_id,
        'MY_CONTRIBUTIONS',
        request_user_id,
        array['AUDIO', 'RAW_TEXT', 'RECORDS', 'ANALYSES', 'DERIVED_FEATURES', 'TRAINING_COPIES']
    );

    return new_job_id;
end;
$$;

alter function baby_private.request_own_contribution_deletion(uuid)
    owner to baby_policy_owner;
revoke all on function baby_private.request_own_contribution_deletion(uuid)
    from public, anon, authenticated, service_role;
grant execute on function baby_private.request_own_contribution_deletion(uuid) to baby_app;

-- New tables are private even if PostgREST exposed-schema configuration drifts.
do $$
declare
    target_table text;
begin
    foreach target_table in array array[
        'idempotency_records',
        'security_attempts',
        'reauthentication_challenges',
        'reauthentication_proofs',
        'session_revocation_jobs',
        'observed_auth_sessions',
        'session_revocation_rules',
        'guardian_verifications',
        'resource_invalidations'
    ]
    loop
        execute format('alter table baby_data.%I enable row level security', target_table);
        execute format('alter table baby_data.%I force row level security', target_table);
        execute format('revoke all on baby_data.%I from public, anon, authenticated, service_role', target_table);
        execute format('grant select, insert, update on baby_data.%I to baby_app', target_table);
    end loop;
end;
$$;

grant usage, select on sequence baby_data.security_attempts_attempt_id_seq to baby_app;

create policy idempotency_own on baby_data.idempotency_records
    for all to baby_app
    using (user_id = baby_private.current_request_user_id())
    with check (user_id = baby_private.current_request_user_id());

create policy security_attempts_own on baby_data.security_attempts
    for all to baby_app
    using (user_id = baby_private.current_request_user_id())
    with check (user_id = baby_private.current_request_user_id());

create policy reauthentication_challenges_own on baby_data.reauthentication_challenges
    for all to baby_app
    using (user_id = baby_private.current_request_user_id())
    with check (
        user_id = baby_private.current_request_user_id()
        and baby_private.has_active_baby_access(baby_id)
    );

create policy reauthentication_proofs_own on baby_data.reauthentication_proofs
    for all to baby_app
    using (user_id = baby_private.current_request_user_id())
    with check (
        user_id = baby_private.current_request_user_id()
        and baby_private.has_active_baby_access(baby_id)
    );

create policy session_revocation_jobs_own on baby_data.session_revocation_jobs
    for all to baby_app
    using (requester_user_id = baby_private.current_request_user_id())
    with check (requester_user_id = baby_private.current_request_user_id());

create policy observed_auth_sessions_own on baby_data.observed_auth_sessions
    for all to baby_app
    using (user_id = baby_private.current_request_user_id())
    with check (user_id = baby_private.current_request_user_id());

create policy session_revocation_rules_own on baby_data.session_revocation_rules
    for all to baby_app
    using (user_id = baby_private.current_request_user_id())
    with check (user_id = baby_private.current_request_user_id());

create policy guardian_verifications_member_select on baby_data.guardian_verifications
    for select to baby_app
    using (baby_private.has_active_baby_access(baby_id));

create policy resource_invalidations_member_select on baby_data.resource_invalidations
    for select to baby_app
    using (baby_private.has_active_baby_access(baby_id));
create policy resource_invalidations_member_insert on baby_data.resource_invalidations
    for insert to baby_app
    with check (baby_private.has_active_baby_access(baby_id));

-- The application role needs the altered/new objects, while browser-facing roles retain none.
grant select, insert, update on
    baby_data.care_events,
    baby_data.action_attempts,
    baby_data.raw_care_entries,
    baby_data.idempotency_records,
    baby_data.security_attempts,
    baby_data.reauthentication_challenges,
    baby_data.reauthentication_proofs,
    baby_data.session_revocation_jobs,
    baby_data.observed_auth_sessions,
    baby_data.session_revocation_rules,
    baby_data.guardian_verifications,
    baby_data.resource_invalidations
to baby_app;

grant select, insert, update on
    baby_data.revoked_sessions,
    baby_data.baby_memberships,
    baby_data.invitations,
    baby_data.consents,
    baby_data.care_events,
    baby_data.action_attempts,
    baby_data.raw_care_entries,
    baby_data.normalization_runs,
    baby_data.babies,
    baby_data.deletion_jobs,
    baby_data.session_revocation_jobs,
    baby_data.observed_auth_sessions,
    baby_data.session_revocation_rules,
    baby_data.resource_invalidations
to baby_policy_owner;

revoke delete, truncate, references, trigger on
    baby_data.idempotency_records,
    baby_data.security_attempts,
    baby_data.reauthentication_challenges,
    baby_data.reauthentication_proofs,
    baby_data.session_revocation_jobs,
    baby_data.observed_auth_sessions,
    baby_data.session_revocation_rules,
    baby_data.guardian_verifications,
    baby_data.resource_invalidations
from baby_app;

-- Storage receives the same revocation cutoffs through signed JWT claims. Existing upload
-- grants therefore stop working immediately for CURRENT, OTHERS, and ALL scopes.
set local role baby_policy_owner;
create or replace function baby_private.can_upload_audio_object(
    requested_bucket_id text,
    requested_object_key text
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $$
    with request_claims as (
        select coalesce(
            nullif(current_setting('request.jwt.claim', true), ''),
            nullif(current_setting('request.jwt.claims', true), '')
        )::jsonb as claims
    ),
    request_context as (
        select coalesce(
                   nullif(current_setting('request.jwt.claim.sub', true), ''),
                   claims ->> 'sub'
               )::uuid as user_id,
               nullif(claims ->> 'session_id', '')::uuid as session_id,
               to_timestamp((claims ->> 'iat')::double precision) as issued_at
          from request_claims
    )
    select r.user_id is not null
       and r.session_id is not null
       and r.issued_at is not null
       and not exists (
           select 1
             from baby_data.session_revocation_rules rr
            where rr.user_id = r.user_id
              and rr.expires_at > statement_timestamp()
              and r.issued_at <= rr.revoked_at
              and (
                  rr.scope = 'ALL'
                  or rr.scope = 'OTHERS' and r.session_id <> rr.requester_session_id
              )
       )
       and exists (
           select 1
             from baby_data.audio_upload_grants g
             join baby_data.audio_assets a
               on a.baby_id = g.baby_id
              and a.audio_id = g.audio_id
              and a.episode_id = g.episode_id
              and a.bucket_id = g.bucket_id
              and a.object_key = g.object_key
             join baby_data.babies b on b.baby_id = g.baby_id
             join baby_data.baby_memberships m
               on m.baby_id = g.baby_id
              and m.user_id = r.user_id
            where g.bucket_id = requested_bucket_id
              and g.object_key = requested_object_key
              and g.uploader_user_id = r.user_id
              and g.auth_session_id = r.session_id
              and g.canceled_at is null
              and g.completed_at is null
              and g.expires_at > statement_timestamp()
              and g.max_bytes between 1 and 25000000
              and a.status = 'ALLOCATED'
              and b.status = 'ACTIVE'
              and m.status = 'ACTIVE'
              and not exists (
                  select 1
                    from baby_data.revoked_sessions rs
                   where rs.session_id = g.auth_session_id
                     and rs.user_id = g.uploader_user_id
                     and rs.expires_at > statement_timestamp()
              )
       )
      from request_context r
$$;
reset role;

-- Keep immutable provenance stable on the three extended record types.
drop trigger care_events_immutable on baby_data.care_events;
create trigger care_events_immutable before update on baby_data.care_events
    for each row execute function baby_private.protect_immutable_columns(
        'care_event_id', 'baby_id', 'created_by_user_id', 'data_origin', 'recorded_at'
    );

drop trigger action_attempts_immutable on baby_data.action_attempts;
create trigger action_attempts_immutable before update on baby_data.action_attempts
    for each row execute function baby_private.protect_immutable_columns(
        'action_id', 'baby_id', 'episode_id', 'created_by_user_id', 'data_origin', 'recorded_at'
    );

drop trigger raw_entries_immutable on baby_data.raw_care_entries;
create trigger raw_entries_immutable before update on baby_data.raw_care_entries
    for each row execute function baby_private.protect_immutable_columns(
        'entry_id', 'baby_id', 'author_user_id', 'data_origin',
        'confirmed_by_user_id', 'confirmed_at', 'recorded_at'
    );

revoke baby_policy_owner from postgres;
revoke create on schema baby_private from baby_policy_owner;
