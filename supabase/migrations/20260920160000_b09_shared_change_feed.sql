-- B-09 durable shared-change feed.
--
-- The feed revision is deliberately separate from resource versions and the
-- derived-context revision. Revisions are allocated by updating one row per
-- baby inside the caller's business transaction. PostgreSQL sequence values are
-- not transactional, so a sequence is not safe as an acknowledged change-feed
-- boundary.

-- B-04 deliberately revoked this temporary ownership grant after its migration.
-- Restore it only while this migration creates the narrowly scoped definer
-- functions, then revoke it again at the end.
grant baby_policy_owner to postgres;
grant create on schema baby_private to baby_policy_owner;

create type baby_data.shared_change_resource_type as enum (
    'BABY',
    'MEMBERSHIP',
    'CARE_EVENT',
    'EPISODE',
    'ANALYSIS',
    'RECOMMENDATION',
    'STATE_OBSERVATION',
    'OUTCOME',
    'DELETION'
);

create table baby_data.shared_change_feed_state (
    baby_id uuid primary key references baby_data.babies(baby_id) on delete restrict,
    -- Revision 1 is a synthetic full-sync checkpoint. It lets databases that
    -- already contain B-04 rows migrate without inventing historical changes.
    current_revision bigint not null default 1 check (current_revision >= 1),
    retained_from_revision bigint not null default 1
        check (retained_from_revision >= 1),
    history_retention interval not null default interval '90 days'
        check (history_retention >= interval '1 day'),
    updated_at timestamptz not null default clock_timestamp(),
    check (retained_from_revision <= current_revision)
);

create or replace function baby_private.initialize_shared_change_feed()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog
as $$
begin
    insert into baby_data.shared_change_feed_state (baby_id)
    values (new.baby_id)
    on conflict (baby_id) do nothing;
    return new;
end;
$$;

alter function baby_private.initialize_shared_change_feed()
    owner to baby_policy_owner;
revoke all on function baby_private.initialize_shared_change_feed()
    from public, anon, authenticated, service_role, baby_app;

create trigger initialize_shared_change_feed_after_baby_insert
after insert on baby_data.babies
for each row execute function baby_private.initialize_shared_change_feed();

insert into baby_data.shared_change_feed_state (baby_id)
select baby_id from baby_data.babies
on conflict (baby_id) do nothing;

create table baby_data.shared_changes (
    baby_id uuid not null references baby_data.shared_change_feed_state(baby_id)
        on delete restrict,
    change_revision bigint not null check (change_revision >= 2),
    resource_type baby_data.shared_change_resource_type not null,
    resource_id uuid not null,
    resource_version bigint not null check (resource_version >= 1),
    deleted boolean not null,
    recorded_at timestamptz not null default clock_timestamp(),
    primary key (baby_id, change_revision, resource_type, resource_id)
);

create index shared_changes_revision_lookup_idx
    on baby_data.shared_changes (baby_id, change_revision)
    include (resource_type, resource_id, resource_version, deleted, recorded_at);

alter table baby_data.shared_change_feed_state enable row level security;
alter table baby_data.shared_change_feed_state force row level security;
alter table baby_data.shared_changes enable row level security;
alter table baby_data.shared_changes force row level security;

revoke all on baby_data.shared_change_feed_state
    from public, anon, authenticated, service_role;
revoke all on baby_data.shared_changes
    from public, anon, authenticated, service_role;

-- PostgreSQL requires UPDATE privilege and an UPDATE USING policy for
-- SELECT ... FOR SHARE. The policy's false WITH CHECK prevents mutation.
grant select, update on baby_data.shared_change_feed_state to baby_app;
grant select on baby_data.shared_changes to baby_app;

create policy shared_change_feed_state_member_select
    on baby_data.shared_change_feed_state
    for select to baby_app
    using (baby_private.has_active_baby_access(baby_id));

-- Row-locking SELECTs are also checked against the UPDATE policy. Existing
-- authorized rows may be locked, but the false WITH CHECK makes every direct
-- mutation fail; only the policy-owner writer can advance the counter.
create policy shared_change_feed_state_member_lock
    on baby_data.shared_change_feed_state
    for update to baby_app
    using (baby_private.has_active_baby_access(baby_id))
    with check (false);

create policy shared_changes_member_select
    on baby_data.shared_changes
    for select to baby_app
    using (baby_private.has_active_baby_access(baby_id));

-- Internal writer. It is intentionally not executable by baby_app: callers must
-- pass through the active-membership wrapper below, or through a narrowly scoped
-- policy-owner function such as former-member contribution deletion.
create or replace function baby_private.record_shared_changes_unchecked(
    target_baby_id uuid,
    change_items jsonb
)
returns bigint
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    next_revision bigint;
    item_count integer;
    distinct_item_count integer;
begin
    if jsonb_typeof(change_items) <> 'array'
       or jsonb_array_length(change_items) = 0 then
        raise exception using errcode = '22023', message = 'B09_INVALID_CHANGE_BATCH';
    end if;

    if exists (
        select 1
          from jsonb_array_elements(change_items) as item(value)
         where jsonb_typeof(value) <> 'object'
            or not value ?& array['resource_type', 'resource_id', 'version', 'deleted']
            or value ->> 'resource_type' not in (
                'BABY', 'MEMBERSHIP', 'CARE_EVENT', 'EPISODE', 'ANALYSIS',
                'RECOMMENDATION', 'STATE_OBSERVATION', 'OUTCOME', 'DELETION'
            )
            or value ->> 'resource_id' !~
                '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
            or value ->> 'version' !~ '^[1-9][0-9]*$'
            or jsonb_typeof(value -> 'deleted') <> 'boolean'
    ) then
        raise exception using errcode = '22023', message = 'B09_INVALID_CHANGE_ITEM';
    end if;

    select count(*)::integer,
           count(distinct (value ->> 'resource_type', value ->> 'resource_id'))::integer
      into item_count, distinct_item_count
      from jsonb_array_elements(change_items) as item(value);
    if item_count <> distinct_item_count then
        raise exception using errcode = '22023', message = 'B09_DUPLICATE_CHANGE_ITEM';
    end if;

    insert into baby_data.shared_change_feed_state (baby_id)
    values (target_baby_id)
    on conflict (baby_id) do nothing;

    -- This row update is transactional and serializes writers for one baby.
    -- A rollback restores the counter and the inserted change rows together.
    update baby_data.shared_change_feed_state
       set current_revision = current_revision + 1,
           updated_at = clock_timestamp()
     where baby_id = target_baby_id
    returning current_revision into next_revision;

    if next_revision is null then
        raise exception using errcode = 'P0002', message = 'B09_FEED_STATE_NOT_FOUND';
    end if;

    insert into baby_data.shared_changes (
        baby_id,
        change_revision,
        resource_type,
        resource_id,
        resource_version,
        deleted
    )
    select target_baby_id,
           next_revision,
           (value ->> 'resource_type')::baby_data.shared_change_resource_type,
           (value ->> 'resource_id')::uuid,
           (value ->> 'version')::bigint,
           (value ->> 'deleted')::boolean
      from jsonb_array_elements(change_items) as item(value);

    return next_revision;
end;
$$;

alter function baby_private.record_shared_changes_unchecked(uuid, jsonb)
    owner to baby_policy_owner;
revoke all on function baby_private.record_shared_changes_unchecked(uuid, jsonb)
    from public, anon, authenticated, service_role, baby_app;
grant execute on function baby_private.record_shared_changes_unchecked(uuid, jsonb)
    to baby_policy_owner;

create or replace function baby_private.record_shared_changes(
    target_baby_id uuid,
    change_items jsonb
)
returns bigint
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    access_is_active boolean;
begin
    if not baby_private.has_live_request_context() then
        raise exception using errcode = '42501', message = 'B04_SESSION_REVOKED';
    end if;

    -- Reject an unrelated baby before taking its serialization lock. This first
    -- check is not the authorization boundary; it is repeated under locks below.
    select true into access_is_active
      from baby_data.babies b
      join baby_data.baby_memberships m on m.baby_id = b.baby_id
     where b.baby_id = target_baby_id
       and b.status = 'ACTIVE'
       and m.user_id = baby_private.current_request_user_id()
       and m.status = 'ACTIVE';
    if access_is_active is not true then
        raise exception using errcode = 'P0002', message = 'B09_RESOURCE_NOT_FOUND';
    end if;

    -- The feed row is always the first shared lock boundary. The caller may
    -- already hold it, but taking it here also covers bootstrap paths such as
    -- invitation acceptance and prevents state/auth-row lock inversion.
    perform 1
      from baby_data.shared_change_feed_state s
     where s.baby_id = target_baby_id
     for update;
    if not found then
        raise exception using errcode = 'P0002', message = 'B09_RESOURCE_NOT_FOUND';
    end if;

    -- SHARE locks then define the authorization boundary for this transaction.
    -- A concurrent membership removal or baby deletion either completes first
    -- and makes this fail, or waits until this authorized transaction commits.
    access_is_active := null;
    select true into access_is_active
      from baby_data.babies b
      join baby_data.baby_memberships m on m.baby_id = b.baby_id
     where b.baby_id = target_baby_id
       and b.status = 'ACTIVE'
       and m.user_id = baby_private.current_request_user_id()
       and m.status = 'ACTIVE'
     for share of b, m;

    if access_is_active is not true then
        raise exception using errcode = 'P0002', message = 'B09_RESOURCE_NOT_FOUND';
    end if;

    return baby_private.record_shared_changes_unchecked(target_baby_id, change_items);
end;
$$;

alter function baby_private.record_shared_changes(uuid, jsonb)
    owner to baby_policy_owner;
revoke all on function baby_private.record_shared_changes(uuid, jsonb)
    from public, anon, authenticated, service_role;
grant execute on function baby_private.record_shared_changes(uuid, jsonb) to baby_app;

-- Retention is purpose-specific: the first operational value is 90 days, not
-- the unrelated seven-day idempotency/audio rule. A scheduler is a later B-13
-- operation. When it is installed, it must call this function; the floor and
-- deletion advance atomically so an absent history range can never look empty.
create or replace function baby_private.prune_shared_change_history(
    cutoff timestamptz default (statement_timestamp() - interval '90 days')
)
returns bigint
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    feed_state record;
    prune_through bigint;
    deleted_rows bigint := 0;
    affected_rows bigint;
begin
    for feed_state in
        select baby_id, current_revision
          from baby_data.shared_change_feed_state
         order by baby_id
         for update
    loop
        select max(revision_group.change_revision)
          into prune_through
          from (
              select change_revision
                from baby_data.shared_changes
               where baby_id = feed_state.baby_id
               group by change_revision
              having max(recorded_at) < cutoff
          ) as revision_group;

        if prune_through is not null then
            update baby_data.shared_change_feed_state
               set retained_from_revision = greatest(
                       retained_from_revision,
                       least(prune_through, current_revision)
                   ),
                   updated_at = clock_timestamp()
             where baby_id = feed_state.baby_id;
            delete from baby_data.shared_changes
             where baby_id = feed_state.baby_id
               and change_revision <= prune_through;
            get diagnostics affected_rows = row_count;
            deleted_rows := deleted_rows + affected_rows;
        end if;
    end loop;
    return deleted_rows;
end;
$$;

alter function baby_private.prune_shared_change_history(timestamptz)
    owner to baby_policy_owner;
revoke all on function baby_private.prune_shared_change_history(timestamptz)
    from public, anon, authenticated, service_role, baby_app;
grant execute on function baby_private.prune_shared_change_history(timestamptz)
    to postgres;

grant select, insert, update on baby_data.shared_change_feed_state to baby_policy_owner;
grant select, insert, update, delete on baby_data.shared_changes to baby_policy_owner;

-- Extend the B-04 former-member right without opening the feed to former
-- members. Only confirmed shared CareEvents produce tombstones. Private drafts,
-- consent, deletion job IDs, and cleanup progress never affect the shared feed.
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
    new_baby_version bigint;
    target_event_count bigint;
    emitted_changes jsonb;
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

    -- All feed-aware mutations take this row before changing business rows.
    -- This prevents a create/update that started concurrently with the bulk
    -- deletion from falling outside both the tombstone batch and a later feed
    -- revision, and keeps the state -> business-row lock order consistent.
    perform 1
      from baby_data.shared_change_feed_state s
      join baby_data.babies b on b.baby_id = s.baby_id
     where s.baby_id = target_baby_id
       and b.status = 'ACTIVE'
     for update of s;
    if not found then
        raise exception using errcode = '55000', message = 'B04_RESOURCE_DELETING';
    end if;

    select count(*) into target_event_count
      from baby_data.care_events e
     where e.baby_id = target_baby_id
       and e.created_by_user_id = request_user_id
       and e.status = 'ACTIVE';

    if target_event_count > 0 then
        update baby_data.babies b
           set context_revision = context_revision + 1,
               version = version + 1
         where b.baby_id = target_baby_id
           and b.status = 'ACTIVE'
        returning context_revision, version
             into new_context_revision, new_baby_version;

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
               e.version + 1,
               'DELETION_REQUESTED',
               new_context_revision
          from baby_data.care_events e
         where e.baby_id = target_baby_id
           and e.created_by_user_id = request_user_id
           and e.status = 'ACTIVE';

        with updated_events as (
            update baby_data.care_events e
               set status = 'DELETING',
                   updated_by_user_id = request_user_id,
                   version = version + 1
             where e.baby_id = target_baby_id
               and e.created_by_user_id = request_user_id
               and e.status = 'ACTIVE'
            returning e.care_event_id, e.version
        )
        select jsonb_agg(
                   jsonb_build_object(
                       'resource_type', 'CARE_EVENT',
                       'resource_id', care_event_id,
                       'version', version,
                       'deleted', true
                   )
                   order by care_event_id
               )
          into emitted_changes
          from updated_events;

        emitted_changes := emitted_changes || jsonb_build_array(
            jsonb_build_object(
                'resource_type', 'BABY',
                'resource_id', target_baby_id,
                'version', new_baby_version,
                'deleted', false
            )
        );

        perform baby_private.record_shared_changes_unchecked(
            target_baby_id,
            emitted_changes
        );
    else
        if not exists (
            select 1 from baby_data.babies b
             where b.baby_id = target_baby_id and b.status = 'ACTIVE'
        ) then
            raise exception using errcode = '55000', message = 'B04_RESOURCE_DELETING';
        end if;
    end if;

    update baby_data.action_attempts a
       set status = 'DELETING',
           updated_by_user_id = request_user_id,
           version = version + 1
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

revoke baby_policy_owner from postgres;
revoke create on schema baby_private from baby_policy_owner;
