-- B-06 durable analysis ownership, retries, lease recovery, and cleanup handoff.

alter table baby_data.analyses
    add column change_version bigint not null default 1 check (change_version >= 1);

alter table baby_data.analyses
    add constraint analyses_one_per_audio unique (audio_id);

-- The public contract already allows a recommendation without a context
-- snapshot. B-08 may add a snapshot later without replacing the audio result.
alter table baby_data.recommendations
    alter column context_snapshot_id drop not null;

create table baby_data.analysis_execution_attempts (
    baby_id uuid not null,
    analysis_id uuid not null,
    attempt_no integer not null check (attempt_no >= 1),
    client_request_id uuid not null,
    request_path text not null check (request_path ~ '^/v1/(episodes|analyses)/'),
    requested_by_user_id uuid not null references auth.users(id) on delete restrict,
    request_session_id uuid not null,
    request_issued_at timestamptz not null,
    execution_token uuid not null,
    status baby_data.analysis_status not null check (
        status in ('RUNNING', 'COMPLETE', 'ABSTAIN', 'FAILED')
    ),
    lease_expires_at timestamptz,
    failure jsonb,
    recorded_at timestamptz not null default clock_timestamp(),
    completed_at timestamptz,
    primary key (analysis_id, attempt_no),
    unique (execution_token),
    foreign key (baby_id, analysis_id)
        references baby_data.analyses (baby_id, analysis_id) on delete restrict,
    check ((status = 'RUNNING') = (lease_expires_at is not null)),
    check ((status in ('COMPLETE', 'ABSTAIN', 'FAILED')) = (completed_at is not null)),
    check ((status = 'FAILED') = (failure is not null))
);

create index analysis_execution_attempts_lease_idx
    on baby_data.analysis_execution_attempts (lease_expires_at)
    where status = 'RUNNING';

alter table baby_data.analysis_execution_attempts enable row level security;
alter table baby_data.analysis_execution_attempts force row level security;

grant select, insert, update on baby_data.analysis_execution_attempts to baby_app;

create policy analysis_attempts_shared_select
    on baby_data.analysis_execution_attempts
    for select to baby_app
    using (baby_private.has_active_baby_access(baby_id));
create policy analysis_attempts_shared_insert
    on baby_data.analysis_execution_attempts
    for insert to baby_app
    with check (
        baby_private.has_active_baby_access(baby_id)
        and requested_by_user_id = baby_private.current_request_user_id()
        and request_session_id = baby_private.current_request_session_id()
    );
create policy analysis_attempts_shared_update
    on baby_data.analysis_execution_attempts
    for update to baby_app
    using (baby_private.has_active_baby_access(baby_id))
    with check (baby_private.has_active_baby_access(baby_id));

create trigger analysis_attempts_immutable before update
    on baby_data.analysis_execution_attempts
    for each row execute function baby_private.protect_immutable_columns(
        'baby_id', 'analysis_id', 'attempt_no', 'client_request_id', 'request_path',
        'requested_by_user_id', 'request_session_id', 'request_issued_at',
        'execution_token', 'recorded_at'
    );

grant baby_policy_owner to postgres;
grant create on schema baby_private to baby_policy_owner;
grant select, insert, update on baby_data.analysis_execution_attempts to baby_policy_owner;
grant select, update on baby_data.analyses to baby_policy_owner;
grant select, update on baby_data.idempotency_records to baby_policy_owner;
grant select on baby_data.audio_assets, baby_data.audio_derivatives,
    baby_data.babies, baby_data.baby_memberships, baby_data.consents,
    baby_data.guardian_verifications, baby_data.revoked_sessions,
    baby_data.session_revocation_rules to baby_policy_owner;

set local role baby_policy_owner;

create or replace function baby_private.enqueue_audio_cleanup_on_analysis_terminal()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog
as $$
begin
    if new.status in ('COMPLETE', 'ABSTAIN', 'FAILED')
       and old.status = 'RUNNING' then
        perform baby_private.enqueue_audio_cleanup_after_analysis(new.audio_id);
    end if;
    return new;
end;
$$;

create or replace function baby_private.expire_analysis_leases()
returns integer
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    target record;
    expired_count integer := 0;
    next_version bigint;
begin
    for target in
        select n.analysis_id, n.baby_id, n.audio_id, n.attempt_no,
               a.client_request_id, a.request_path, a.requested_by_user_id,
               a.execution_token
          from baby_data.analyses n
          join baby_data.analysis_execution_attempts a
            on a.analysis_id = n.analysis_id and a.attempt_no = n.attempt_no
         where n.status = 'RUNNING'
           and n.lease_expires_at <= statement_timestamp()
           and a.status = 'RUNNING'
         for update of n, a skip locked
    loop
        update baby_data.analyses n
           set status = 'FAILED', stage = 'FINISHED', execution_token = null,
               lease_expires_at = null, audio_candidates = '[]'::jsonb,
               abstain_reason = null,
               failure = jsonb_build_object(
                   'code', 'ANALYSIS_LEASE_EXPIRED',
                   'message', 'The analysis lease expired before completion.',
                   'retryable', true
               ),
               completed_at = clock_timestamp(),
               change_version = n.change_version + 1
         where n.analysis_id = target.analysis_id
           and n.status = 'RUNNING'
           and n.attempt_no = target.attempt_no
           and n.execution_token = target.execution_token
        returning n.change_version into next_version;
        if next_version is null then
            continue;
        end if;

        update baby_data.analysis_execution_attempts a
           set status = 'FAILED', lease_expires_at = null,
               failure = jsonb_build_object(
                   'code', 'ANALYSIS_LEASE_EXPIRED',
                   'message', 'The analysis lease expired before completion.',
                   'retryable', true
               ),
               completed_at = clock_timestamp()
         where a.analysis_id = target.analysis_id
           and a.attempt_no = target.attempt_no
           and a.execution_token = target.execution_token
           and a.status = 'RUNNING';

        update baby_data.idempotency_records i
           set status = 'COMPLETE', result_type = 'ANALYSIS',
               result_id = target.analysis_id, response_status = 200,
               completed_at = clock_timestamp()
         where i.user_id = target.requested_by_user_id
           and i.method = 'POST'
           and i.target_path = target.request_path
           and i.idempotency_key = target.client_request_id
           and i.status = 'IN_PROGRESS';

        perform baby_private.record_shared_changes_unchecked(
            target.baby_id,
            jsonb_build_array(jsonb_build_object(
                'resource_type', 'ANALYSIS',
                'resource_id', target.analysis_id,
                'version', next_version,
                'deleted', false
            ))
        );
        perform baby_private.enqueue_audio_cleanup_after_analysis(target.audio_id);
        expired_count := expired_count + 1;
    end loop;
    return expired_count;
end;
$$;

create or replace function baby_private.fence_unavailable_analysis(
    target_analysis_id uuid,
    target_attempt_no integer,
    target_execution_token uuid
)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    target record;
    available boolean;
    failure_code text;
    failure_message text;
    next_version bigint;
begin
    select n.analysis_id, n.baby_id, n.audio_id, n.data_origin::text as data_origin,
           n.attempt_no, n.execution_token, aa.client_request_id, aa.request_path,
           aa.requested_by_user_id, aa.request_session_id, aa.request_issued_at,
           a.status::text as audio_status,
           coalesce(d.status, 'MISSING') as derivative_status
      into target
      from baby_data.analyses n
      join baby_data.analysis_execution_attempts aa
        on aa.analysis_id = n.analysis_id and aa.attempt_no = n.attempt_no
      join baby_data.audio_assets a on a.audio_id = n.audio_id
      left join baby_data.audio_derivatives d
        on d.audio_id = n.audio_id and d.kind = 'PCM_S16LE_SOURCE_RATE'
     where n.analysis_id = target_analysis_id
       and n.status = 'RUNNING'
       and n.attempt_no = target_attempt_no
       and n.execution_token = target_execution_token
       and aa.status = 'RUNNING'
     for update of n, aa, a;
    if not found then
        return false;
    end if;

    select exists (
        select 1
          from baby_data.babies b
          join baby_data.baby_memberships m on m.baby_id = b.baby_id
         where b.baby_id = target.baby_id
           and b.status = 'ACTIVE'
           and m.user_id = target.requested_by_user_id
           and m.status = 'ACTIVE'
    )
    and exists (
        select 1 from baby_data.consents c
         where c.baby_id = target.baby_id
           and c.scope = 'SERVICE_PROCESSING'
           and c.status = 'GRANTED'
           and not exists (
               select 1 from baby_data.consents newer
                where newer.baby_id = c.baby_id
                  and newer.scope = c.scope
                  and (newer.version, newer.recorded_at) > (c.version, c.recorded_at)
           )
    )
    and not exists (
        select 1 from baby_data.revoked_sessions rs
         where rs.user_id = target.requested_by_user_id
           and rs.session_id = target.request_session_id
           and rs.expires_at > statement_timestamp()
    )
    and not exists (
        select 1 from baby_data.session_revocation_rules rr
         where rr.user_id = target.requested_by_user_id
           and rr.expires_at > statement_timestamp()
           and target.request_issued_at <= rr.revoked_at
           and (
               rr.scope = 'ALL'
               or rr.scope = 'OTHERS'
                  and target.request_session_id <> rr.requester_session_id
           )
    )
    and (
        target.data_origin <> 'USER'
        or exists (
            select 1
              from baby_data.guardian_verifications v
              join baby_data.baby_memberships m
                on m.baby_id = v.baby_id and m.user_id = v.subject_user_id
             where v.baby_id = target.baby_id
               and v.status = 'VERIFIED'
               and m.role = 'OWNER' and m.status = 'ACTIVE'
        )
    )
    and target.audio_status = 'READY'
    and target.derivative_status = 'READY'
    into available;

    if available then
        return false;
    end if;

    if target.audio_status in ('DELETING', 'DELETED')
       or target.derivative_status <> 'READY' then
        failure_code := 'SOURCE_DELETED';
        failure_message := 'The analysis input is no longer available.';
    else
        failure_code := 'ACCESS_REVOKED';
        failure_message := 'Access was revoked before analysis completed.';
    end if;

    update baby_data.analyses n
       set status = 'FAILED', stage = 'FINISHED', execution_token = null,
           lease_expires_at = null, quality_status = case
               when failure_code = 'SOURCE_DELETED' then 'INVALID'::baby_data.quality_status
               else n.quality_status
           end,
           audio_candidates = '[]'::jsonb, abstain_reason = null,
           failure = jsonb_build_object(
               'code', failure_code,
               'message', failure_message,
               'retryable', false
           ),
           completed_at = clock_timestamp(),
           change_version = n.change_version + 1
     where n.analysis_id = target.analysis_id
       and n.status = 'RUNNING'
       and n.attempt_no = target.attempt_no
       and n.execution_token = target.execution_token
    returning n.change_version into next_version;
    if next_version is null then
        return false;
    end if;

    update baby_data.analysis_execution_attempts aa
       set status = 'FAILED', lease_expires_at = null,
           failure = jsonb_build_object(
               'code', failure_code,
               'message', failure_message,
               'retryable', false
           ),
           completed_at = clock_timestamp()
     where aa.analysis_id = target.analysis_id
       and aa.attempt_no = target.attempt_no
       and aa.execution_token = target.execution_token
       and aa.status = 'RUNNING';

    update baby_data.idempotency_records i
       set status = 'COMPLETE', result_type = 'ANALYSIS',
           result_id = target.analysis_id, response_status = 200,
           completed_at = clock_timestamp()
     where i.user_id = target.requested_by_user_id
       and i.method = 'POST'
       and i.target_path = target.request_path
       and i.idempotency_key = target.client_request_id
       and i.status = 'IN_PROGRESS';

    perform baby_private.record_shared_changes_unchecked(
        target.baby_id,
        jsonb_build_array(jsonb_build_object(
            'resource_type', 'ANALYSIS',
            'resource_id', target.analysis_id,
            'version', next_version,
            'deleted', false
        ))
    );
    perform baby_private.enqueue_audio_cleanup_after_analysis(target.audio_id);
    return true;
end;
$$;

reset role;

alter function baby_private.enqueue_audio_cleanup_on_analysis_terminal()
    owner to baby_policy_owner;
alter function baby_private.expire_analysis_leases() owner to baby_policy_owner;
alter function baby_private.fence_unavailable_analysis(uuid, integer, uuid)
    owner to baby_policy_owner;

revoke all on function baby_private.enqueue_audio_cleanup_on_analysis_terminal()
    from public, anon, authenticated, service_role, baby_app;
revoke all on function baby_private.expire_analysis_leases()
    from public, anon, authenticated, service_role;
revoke all on function baby_private.fence_unavailable_analysis(uuid, integer, uuid)
    from public, anon, authenticated, service_role;

grant execute on function baby_private.expire_analysis_leases() to baby_app;
grant execute on function baby_private.fence_unavailable_analysis(uuid, integer, uuid)
    to baby_app;

create trigger enqueue_audio_cleanup_after_analysis_terminal
after update of status on baby_data.analyses
for each row execute function baby_private.enqueue_audio_cleanup_on_analysis_terminal();

comment on table baby_data.analysis_execution_attempts is
    'B-06 attempt ledger. Stores fencing metadata only; no audio, model scores, or response body.';
comment on function baby_private.expire_analysis_leases() is
    'Server startup and GET recovery for expired RUNNING analysis leases.';

revoke baby_policy_owner from postgres;
revoke create on schema baby_private from baby_policy_owner;
