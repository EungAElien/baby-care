-- B-05 audio intake, verification, derived PCM, and durable cleanup.
--
-- Browser clients may upload only the exact private object reserved by FastAPI.
-- All metadata reads, completion, playback signing, and deletion stay behind the
-- least-privilege baby_app role or the narrow worker functions below.

alter table baby_data.idempotency_records
    drop constraint if exists idempotency_records_response_status_check;
alter table baby_data.idempotency_records
    add constraint idempotency_records_response_status_check
    check (response_status between 200 and 599);

-- EpisodeDetail was already canonical in 1.1.1, but the B-03 physical tables
-- omitted fields needed to serialize real outcomes/state observations. Additive
-- columns let B-05 return linked rows instead of contract-shaped empty arrays.
alter table baby_data.outcomes
    add column time_precision baby_data.time_precision not null default 'UNKNOWN',
    add column data_origin baby_data.data_origin not null default 'USER';

alter table baby_data.state_observations
    add column time_precision baby_data.time_precision not null default 'UNKNOWN',
    add column observation_source text not null default 'SELF_REPORTED'
        check (observation_source in ('SELF_REPORTED', 'REPORTED_BY_OTHER')),
    add column confirmation_status text not null default 'USER_CONFIRMED'
        check (confirmation_status in ('USER_CONFIRMED', 'USER_CORRECTED')),
    add column visual_state_code text not null default 'NEUTRAL'
        check (visual_state_code in (
            'CRYING', 'FUSSING', 'CALM', 'SLEEPY_APPEARING',
            'ASLEEP', 'AWAKE', 'CHEERFUL_APPEARING', 'NEUTRAL'
        )),
    add column visual_mapping_version text not null default 'state-visual-v1',
    add column data_origin baby_data.data_origin not null default 'USER';

alter table baby_data.audio_assets
    add column declared_mime_type text,
    add column declared_bytes bigint check (declared_bytes is null or declared_bytes between 1 and 25000000),
    add column declared_duration_seconds numeric(8,3)
        check (declared_duration_seconds is null or declared_duration_seconds between 0 and 60),
    add column declared_checksum_sha256 text
        check (declared_checksum_sha256 is null or declared_checksum_sha256 ~ '^[0-9a-f]{64}$'),
    add column verified_container text,
    add column verified_codec text,
    add column verified_sample_rate_hz integer
        check (verified_sample_rate_hz is null or verified_sample_rate_hz between 1 and 192000),
    add column verified_channels integer
        check (verified_channels is null or verified_channels between 1 and 2),
    add column decoder_version text,
    add column verification_token uuid,
    add column verification_lease_expires_at timestamptz,
    add column received_at timestamptz,
    add column delete_after timestamptz;

alter table baby_data.audio_assets
    add constraint audio_assets_verification_lease_shape check (
        (status = 'VERIFYING' and verification_token is not null
            and verification_lease_expires_at is not null)
        or
        (status <> 'VERIFYING' and verification_token is null
            and verification_lease_expires_at is null)
    ),
    add constraint audio_assets_delete_after_shape check (
        delete_after is null or received_at is not null
    );

alter table baby_data.audio_upload_grants
    drop constraint if exists audio_upload_grants_bucket_id_object_key_key;

alter table baby_data.audio_upload_grants
    add column declared_mime_type text,
    -- The default preserves B-03 grants created by older deployments/tests. B-05
    -- always writes the exact declared byte reservation explicitly.
    add column expected_bytes integer not null default 25000000
        check (expected_bytes between 1 and 25000000),
    add column declared_duration_seconds numeric(8,3)
        check (declared_duration_seconds is null or declared_duration_seconds between 0 and 60),
    add column declared_checksum_sha256 text
        check (declared_checksum_sha256 is null or declared_checksum_sha256 ~ '^[0-9a-f]{64}$'),
    add column reserved_bytes integer not null default 0
        check (reserved_bytes between 0 and 25000000),
    add column superseded_at timestamptz,
    add column reservation_released_at timestamptz;

alter table baby_data.audio_upload_grants
    add constraint audio_upload_grants_one_terminal_state check (
        (canceled_at is not null)::integer
        + (completed_at is not null)::integer
        + (superseded_at is not null)::integer <= 1
    );

create unique index audio_upload_grants_one_active_per_object
    on baby_data.audio_upload_grants (bucket_id, object_key)
    where canceled_at is null
      and completed_at is null
      and superseded_at is null
      and reservation_released_at is null;

create index audio_upload_grants_user_reservation_idx
    on baby_data.audio_upload_grants (uploader_user_id, expires_at)
    include (reserved_bytes)
    where reservation_released_at is null;

create table baby_data.audio_upload_daily_usage (
    user_id uuid not null references auth.users(id) on delete cascade,
    usage_date date not null,
    allocated_bytes bigint not null default 0
        check (allocated_bytes between 0 and 250000000),
    allocation_count integer not null default 0 check (allocation_count >= 0),
    updated_at timestamptz not null default clock_timestamp(),
    primary key (user_id, usage_date)
);

create table baby_data.audio_verification_runs (
    verification_run_id uuid primary key default extensions.gen_random_uuid(),
    run_token uuid not null unique,
    baby_id uuid not null,
    episode_id uuid not null,
    audio_id uuid not null,
    upload_id uuid not null,
    uploader_user_id uuid not null references auth.users(id) on delete restrict,
    client_request_id uuid not null,
    status text not null check (status in ('RUNNING', 'COMPLETE', 'REJECTED', 'STALE', 'FAILED')),
    lease_expires_at timestamptz,
    failure_code text,
    recorded_at timestamptz not null default clock_timestamp(),
    completed_at timestamptz,
    unique (uploader_user_id, client_request_id),
    foreign key (baby_id, episode_id, audio_id)
        references baby_data.audio_assets (baby_id, episode_id, audio_id) on delete restrict,
    foreign key (baby_id, upload_id)
        references baby_data.audio_upload_grants (baby_id, upload_id) on delete restrict,
    check ((status = 'RUNNING') = (lease_expires_at is not null)),
    check ((status = 'RUNNING') = (completed_at is null))
);

create table baby_data.audio_derivatives (
    derivative_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null,
    audio_id uuid not null,
    kind text not null check (kind = 'PCM_S16LE_SOURCE_RATE'),
    bucket_id text not null default 'baby-audio' check (bucket_id = 'baby-audio'),
    object_key text not null
        check (object_key !~ '(^|/)\.\.(/|$)' and char_length(object_key) between 1 and 512),
    mime_type text not null check (mime_type = 'audio/wav'),
    bytes bigint not null check (bytes between 1 and 25000000),
    duration_seconds numeric(8,3) not null check (duration_seconds between 0 and 60),
    checksum_sha256 text not null check (checksum_sha256 ~ '^[0-9a-f]{64}$'),
    sample_rate_hz integer not null check (sample_rate_hz between 1 and 96000),
    channels integer not null check (channels between 1 and 2),
    decoder_version text not null,
    preprocessing_boundary_version text not null,
    status text not null check (status in ('READY', 'DELETING', 'DELETED')),
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (bucket_id, object_key),
    unique (audio_id, kind),
    foreign key (baby_id, audio_id)
        references baby_data.audio_assets (baby_id, audio_id) on delete restrict
);

create table baby_data.audio_cleanup_jobs (
    cleanup_job_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null,
    audio_id uuid not null,
    reason text not null check (reason in (
        'CANCELED', 'REJECTED', 'NO_RETENTION_ANALYSIS_FINISHED',
        'NO_RETENTION_TIMEOUT', 'RETENTION_EXPIRED', 'CONSENT_REVOKED', 'ORPHAN'
    )),
    status text not null default 'PENDING'
        check (status in ('PENDING', 'RUNNING', 'FAILED', 'COMPLETE')),
    attempt_no integer not null default 0 check (attempt_no >= 0),
    execution_token uuid,
    lease_expires_at timestamptz,
    not_before timestamptz not null default clock_timestamp(),
    next_attempt_at timestamptz not null default clock_timestamp(),
    last_error_code text check (
        last_error_code is null or last_error_code ~ '^[A-Z0-9_]{1,80}$'
    ),
    recorded_at timestamptz not null default clock_timestamp(),
    completed_at timestamptz,
    foreign key (baby_id, audio_id)
        references baby_data.audio_assets (baby_id, audio_id) on delete restrict,
    check ((status = 'RUNNING') = (execution_token is not null and lease_expires_at is not null)),
    check ((status = 'COMPLETE') = (completed_at is not null))
);

create unique index audio_cleanup_jobs_one_open_per_audio
    on baby_data.audio_cleanup_jobs (audio_id)
    where status <> 'COMPLETE';

create index audio_cleanup_jobs_claim_idx
    on baby_data.audio_cleanup_jobs (next_attempt_at, not_before, recorded_at)
    where status in ('PENDING', 'FAILED');

-- Tighten immutable upload provenance now that reissue history can share one key.
drop trigger audio_assets_immutable on baby_data.audio_assets;
create trigger audio_assets_immutable before update on baby_data.audio_assets
    for each row execute function baby_private.protect_immutable_columns(
        'audio_id', 'baby_id', 'episode_id', 'created_by_user_id', 'bucket_id',
        'object_key', 'declared_mime_type', 'declared_bytes',
        'declared_duration_seconds', 'declared_checksum_sha256', 'data_origin',
        'recorded_at'
    );

drop trigger outcomes_immutable on baby_data.outcomes;
create trigger outcomes_immutable before update on baby_data.outcomes
    for each row execute function baby_private.protect_immutable_columns(
        'outcome_id', 'baby_id', 'created_by_user_id', 'data_origin', 'recorded_at'
    );

drop trigger state_observations_immutable on baby_data.state_observations;
create trigger state_observations_immutable before update on baby_data.state_observations
    for each row execute function baby_private.protect_immutable_columns(
        'state_observation_id', 'baby_id', 'created_by_user_id',
        'confirmed_by_user_id', 'data_origin', 'recorded_at'
    );

drop trigger upload_grants_immutable on baby_data.audio_upload_grants;
create trigger upload_grants_immutable before update on baby_data.audio_upload_grants
    for each row execute function baby_private.protect_immutable_columns(
        'upload_id', 'baby_id', 'episode_id', 'audio_id', 'uploader_user_id',
        'auth_session_id', 'bucket_id', 'object_key', 'method', 'max_bytes',
        'declared_mime_type', 'expected_bytes', 'declared_duration_seconds',
        'declared_checksum_sha256', 'reserved_bytes', 'recorded_at'
    );

create trigger audio_derivatives_immutable before update on baby_data.audio_derivatives
    for each row execute function baby_private.protect_immutable_columns(
        'derivative_id', 'baby_id', 'audio_id', 'kind', 'bucket_id', 'object_key',
        'mime_type', 'bytes', 'duration_seconds', 'checksum_sha256',
        'sample_rate_hz', 'channels', 'decoder_version',
        'preprocessing_boundary_version', 'recorded_at'
    );
create trigger audio_derivatives_version_touch before update on baby_data.audio_derivatives
    for each row execute function baby_private.enforce_version_and_touch();

do $$
declare
    target_table text;
begin
    foreach target_table in array array[
        'audio_upload_daily_usage', 'audio_verification_runs',
        'audio_derivatives', 'audio_cleanup_jobs'
    ]
    loop
        execute format('alter table baby_data.%I enable row level security', target_table);
        execute format('alter table baby_data.%I force row level security', target_table);
        execute format(
            'revoke all on baby_data.%I from public, anon, authenticated, service_role',
            target_table
        );
        execute format(
            'grant select, insert, update on baby_data.%I to baby_app',
            target_table
        );
    end loop;
end;
$$;

create policy audio_upload_daily_usage_own
    on baby_data.audio_upload_daily_usage for all to baby_app
    using (user_id = baby_private.current_request_user_id())
    with check (user_id = baby_private.current_request_user_id());

create policy audio_verification_runs_uploader
    on baby_data.audio_verification_runs for all to baby_app
    using (
        uploader_user_id = baby_private.current_request_user_id()
        and baby_private.has_active_baby_access(baby_id)
    )
    with check (
        uploader_user_id = baby_private.current_request_user_id()
        and baby_private.has_active_baby_access(baby_id)
    );

create policy audio_derivatives_member_select
    on baby_data.audio_derivatives for select to baby_app
    using (baby_private.has_active_baby_access(baby_id));
create policy audio_derivatives_member_insert
    on baby_data.audio_derivatives for insert to baby_app
    with check (baby_private.has_active_baby_access(baby_id));
create policy audio_derivatives_member_update
    on baby_data.audio_derivatives for update to baby_app
    using (baby_private.has_active_baby_access(baby_id))
    with check (baby_private.has_active_baby_access(baby_id));

create policy audio_cleanup_jobs_member_select
    on baby_data.audio_cleanup_jobs for select to baby_app
    using (baby_private.has_active_baby_access(baby_id));
create policy audio_cleanup_jobs_member_insert
    on baby_data.audio_cleanup_jobs for insert to baby_app
    with check (baby_private.has_active_baby_access(baby_id));
create policy audio_cleanup_jobs_member_update
    on baby_data.audio_cleanup_jobs for update to baby_app
    using (baby_private.has_active_baby_access(baby_id))
    with check (baby_private.has_active_baby_access(baby_id));

-- Only MIME types decoded by the pinned B-05 image are accepted at the bucket
-- boundary. Completion still treats the declaration as untrusted and probes bytes.
update storage.buckets
   set public = false,
       file_size_limit = 25000000,
       allowed_mime_types = array[
           'audio/wav', 'audio/x-wav', 'audio/webm', 'audio/mp4', 'audio/aac'
       ]
 where id = 'baby-audio';

-- Reissued grants keep the immutable object key, while only one unfinished grant
-- can authorize a new Storage object insert.
grant baby_policy_owner to postgres;
grant create on schema baby_private to baby_policy_owner;
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
              and g.superseded_at is null
              and g.reservation_released_at is null
              and g.expires_at > statement_timestamp()
              and g.max_bytes between 1 and 25000000
              and g.expected_bytes between 1 and g.max_bytes
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

grant select, insert, update on
    baby_data.audio_assets,
    baby_data.audio_upload_grants,
    baby_data.audio_verification_runs,
    baby_data.audio_derivatives,
    baby_data.audio_cleanup_jobs,
    baby_data.analyses
to baby_policy_owner;

set local role baby_policy_owner;
create or replace function baby_private.enqueue_audio_cleanup_on_consent_change()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog
as $$
begin
    if new.scope not in ('SERVICE_PROCESSING', 'AUDIO_RETENTION')
       or new.status = 'GRANTED' then
        return new;
    end if;

    update baby_data.audio_upload_grants g
       set canceled_at = case
               when g.completed_at is null and g.superseded_at is null
                   then clock_timestamp()
               else g.canceled_at
           end,
           reservation_released_at = coalesce(
               g.reservation_released_at, clock_timestamp()
           )
     where g.baby_id = new.baby_id
       and g.completed_at is null
       and g.superseded_at is null;

    update baby_data.audio_verification_runs r
       set status = 'STALE', lease_expires_at = null,
           failure_code = 'CONSENT_REVOKED', completed_at = clock_timestamp()
     where r.baby_id = new.baby_id and r.status = 'RUNNING';

    with targets as (
        update baby_data.audio_assets a
           set status = 'DELETING', retention_until = null,
               verification_token = null, verification_lease_expires_at = null,
               version = a.version + 1
         where a.baby_id = new.baby_id
           and a.status in ('ALLOCATED', 'VERIFYING', 'READY', 'REJECTED')
           and (new.scope = 'SERVICE_PROCESSING' or a.retention_until is not null)
        returning a.baby_id, a.audio_id
    )
    insert into baby_data.audio_cleanup_jobs (baby_id, audio_id, reason)
    select baby_id, audio_id, 'CONSENT_REVOKED' from targets
    on conflict (audio_id) where status <> 'COMPLETE' do nothing;
    return new;
end;
$$;

create or replace function baby_private.enqueue_audio_cleanup_after_analysis(
    target_audio_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    target record;
begin
    select a.audio_id, a.baby_id, a.status::text as status
      into target
      from baby_data.audio_assets a
     where a.audio_id = target_audio_id
       and a.retention_until is null
       and a.status = 'READY'
       and exists (
           select 1 from baby_data.analyses n
            where n.audio_id = a.audio_id
              and n.status in ('COMPLETE', 'ABSTAIN', 'FAILED')
       )
     for update;
    if not found then
        return false;
    end if;

    update baby_data.audio_assets
       set status = 'DELETING', version = version + 1
     where audio_id = target.audio_id and status = 'READY';
    insert into baby_data.audio_cleanup_jobs (baby_id, audio_id, reason)
    values (target.baby_id, target.audio_id, 'NO_RETENTION_ANALYSIS_FINISHED')
    on conflict (audio_id) where status <> 'COMPLETE' do nothing;
    return true;
end;
$$;

create or replace function baby_private.claim_audio_cleanup_jobs(
    worker_token uuid,
    max_jobs integer default 20
)
returns table (
    cleanup_job_id uuid,
    audio_id uuid,
    bucket_id text,
    object_keys text[],
    reason text,
    attempt_no integer
)
language plpgsql
security definer
set search_path = pg_catalog
as $$
#variable_conflict use_column
declare
    claimed record;
begin
    if worker_token is null or max_jobs not between 1 and 100 then
        raise exception using errcode = '22023', message = 'B05_INVALID_CLEANUP_CLAIM';
    end if;

    update baby_data.audio_upload_grants
       set reservation_released_at = clock_timestamp()
     where reservation_released_at is null
       and canceled_at is null
       and completed_at is null
       and superseded_at is null
       and expires_at <= statement_timestamp();

    update baby_data.audio_cleanup_jobs
       set status = 'FAILED', execution_token = null, lease_expires_at = null,
           next_attempt_at = clock_timestamp(), last_error_code = 'LEASE_EXPIRED'
     where status = 'RUNNING' and lease_expires_at <= statement_timestamp();

    insert into baby_data.audio_cleanup_jobs (baby_id, audio_id, reason)
    select a.baby_id,
           a.audio_id,
           case
               when a.retention_until is not null then 'RETENTION_EXPIRED'
               when a.delete_after is not null then 'NO_RETENTION_TIMEOUT'
               else 'ORPHAN'
           end
      from baby_data.audio_assets a
     where a.status in ('ALLOCATED', 'VERIFYING', 'READY', 'REJECTED')
       and (
           a.retention_until <= statement_timestamp()
           or (a.retention_until is null and a.delete_after <= statement_timestamp())
           or (
               a.status = 'VERIFYING'
               and a.verification_lease_expires_at <= statement_timestamp()
           )
           or (
               a.status = 'ALLOCATED'
               and a.recorded_at <= statement_timestamp() - interval '1 hour'
           )
       )
    on conflict (audio_id) where status <> 'COMPLETE' do nothing;

    for claimed in
        select j.cleanup_job_id, j.audio_id, j.baby_id, j.reason, j.attempt_no
          from baby_data.audio_cleanup_jobs j
         where j.status in ('PENDING', 'FAILED')
           and j.not_before <= statement_timestamp()
           and j.next_attempt_at <= statement_timestamp()
         order by j.next_attempt_at, j.recorded_at
         for update skip locked
         limit max_jobs
    loop
        update baby_data.audio_cleanup_jobs j
           set status = 'RUNNING', execution_token = worker_token,
               lease_expires_at = clock_timestamp() + interval '5 minutes',
               attempt_no = j.attempt_no + 1, last_error_code = null
         where j.cleanup_job_id = claimed.cleanup_job_id;

        update baby_data.audio_assets a
           set status = 'DELETING', verification_token = null,
               verification_lease_expires_at = null, version = a.version + 1
         where a.audio_id = claimed.audio_id
           and a.status not in ('DELETING', 'DELETED');

        cleanup_job_id := claimed.cleanup_job_id;
        audio_id := claimed.audio_id;
        select a.bucket_id,
               array_prepend(
                   a.object_key,
                   coalesce(array_agg(d.object_key order by d.object_key)
                       filter (where d.object_key is not null), '{}'::text[])
               )
          into bucket_id, object_keys
          from baby_data.audio_assets a
          left join baby_data.audio_derivatives d
            on d.audio_id = a.audio_id and d.status <> 'DELETED'
         where a.audio_id = claimed.audio_id
         group by a.bucket_id, a.object_key;
        reason := claimed.reason;
        attempt_no := claimed.attempt_no + 1;
        return next;
    end loop;
end;
$$;

create or replace function baby_private.finish_audio_cleanup_job(
    target_cleanup_job_id uuid,
    worker_token uuid,
    succeeded boolean,
    failure_code text default null
)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    target_audio_id uuid;
begin
    select j.audio_id into target_audio_id
      from baby_data.audio_cleanup_jobs j
     where j.cleanup_job_id = target_cleanup_job_id
       and j.status = 'RUNNING'
       and j.execution_token = worker_token
       and j.lease_expires_at > statement_timestamp()
     for update;
    if not found then
        return false;
    end if;

    if succeeded then
        update baby_data.audio_derivatives d
           set status = 'DELETED', version = d.version + 1
         where d.audio_id = target_audio_id and d.status <> 'DELETED';
        update baby_data.audio_assets a
           set status = 'DELETED', retention_until = null, delete_after = null,
               verification_token = null, verification_lease_expires_at = null,
               version = a.version + 1
         where a.audio_id = target_audio_id and a.status <> 'DELETED';
        update baby_data.audio_cleanup_jobs j
           set status = 'COMPLETE', execution_token = null, lease_expires_at = null,
               completed_at = clock_timestamp(), last_error_code = null
         where j.cleanup_job_id = target_cleanup_job_id;
    else
        if failure_code is null or failure_code !~ '^[A-Z0-9_]{1,80}$' then
            raise exception using errcode = '22023', message = 'B05_INVALID_CLEANUP_FAILURE';
        end if;
        update baby_data.audio_cleanup_jobs j
           set status = 'FAILED', execution_token = null, lease_expires_at = null,
               next_attempt_at = clock_timestamp()
                   + least(3600, power(2, j.attempt_no)::integer) * interval '1 second',
               last_error_code = failure_code
         where j.cleanup_job_id = target_cleanup_job_id;
    end if;
    return true;
end;
$$;
reset role;

create trigger audio_cleanup_on_consent_change
after insert on baby_data.consents
for each row execute function baby_private.enqueue_audio_cleanup_on_consent_change();

alter function baby_private.can_upload_audio_object(text, text) owner to baby_policy_owner;
alter function baby_private.enqueue_audio_cleanup_on_consent_change()
    owner to baby_policy_owner;
alter function baby_private.enqueue_audio_cleanup_after_analysis(uuid) owner to baby_policy_owner;
alter function baby_private.claim_audio_cleanup_jobs(uuid, integer) owner to baby_policy_owner;
alter function baby_private.finish_audio_cleanup_job(uuid, uuid, boolean, text)
    owner to baby_policy_owner;

revoke all on function baby_private.can_upload_audio_object(text, text)
    from public, anon, authenticated, service_role;
revoke all on function baby_private.enqueue_audio_cleanup_on_consent_change()
    from public, anon, authenticated, service_role, baby_app;
revoke all on function baby_private.enqueue_audio_cleanup_after_analysis(uuid)
    from public, anon, authenticated, service_role;
revoke all on function baby_private.claim_audio_cleanup_jobs(uuid, integer)
    from public, anon, authenticated, service_role;
revoke all on function baby_private.finish_audio_cleanup_job(uuid, uuid, boolean, text)
    from public, anon, authenticated, service_role;

grant execute on function baby_private.can_upload_audio_object(text, text) to authenticated;
grant execute on function baby_private.enqueue_audio_cleanup_after_analysis(uuid) to baby_app;
grant execute on function baby_private.claim_audio_cleanup_jobs(uuid, integer) to baby_app;
grant execute on function baby_private.finish_audio_cleanup_job(uuid, uuid, boolean, text)
    to baby_app;

comment on function baby_private.claim_audio_cleanup_jobs(uuid, integer) is
    'Server worker claim. Returns exact private keys, never signed URLs or credentials.';

revoke baby_policy_owner from postgres;
revoke create on schema baby_private from baby_policy_owner;

comment on table baby_data.audio_derivatives is
    'Server-created source-rate PCM interchange artifact. B-06 owns model resampling/normalization.';
comment on table baby_data.audio_cleanup_jobs is
    'Durable Storage deletion work; SQL metadata reaches DELETED only after provider deletion succeeds.';
