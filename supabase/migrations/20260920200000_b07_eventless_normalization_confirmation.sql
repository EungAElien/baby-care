-- B-07 first slice: eventless normalization, confirmation lineage, and
-- refetchable confirmed state observations. Browser roles remain unable to
-- access baby_data/baby_private; FastAPI continues to operate as baby_app.

alter table baby_data.raw_care_entries
    add column original_author_user_id uuid references auth.users(id) on delete restrict,
    add column confirmed_resources jsonb
        check (confirmed_resources is null or jsonb_typeof(confirmed_resources) = 'object');

update baby_data.raw_care_entries
   set original_author_user_id = author_user_id;

alter table baby_data.raw_care_entries
    alter column original_author_user_id set not null;

alter table baby_data.normalization_runs
    add column attempt_no integer not null default 1 check (attempt_no >= 1),
    add column provider_call_executed boolean not null default false;

create unique index normalization_one_running_per_revision
    on baby_data.normalization_runs (entry_id, input_revision)
    where status = 'RUNNING';

-- B-05 already added the shared StateObservation serialization columns. B-07
-- adds updater provenance and replaces its generic visual mapping version with
-- the confirmed-care mapping used by eventless confirmation.
alter table baby_data.state_observations
    add column updated_by_user_id uuid references auth.users(id) on delete restrict,
    alter column visual_mapping_version set default 'care-visual-v1';

update baby_data.state_observations
   set observation_source = case source
           when 'REPORTED_BY_OTHER' then 'REPORTED_BY_OTHER'
           else 'SELF_REPORTED'
       end,
       visual_state_code = case
           when 'UNKNOWN' = any(state_codes)
             or ('CRYING' = any(state_codes) and 'CALM' = any(state_codes))
             or ('ASLEEP' = any(state_codes) and 'AWAKE' = any(state_codes))
               then 'NEUTRAL'
           when 'CRYING' = any(state_codes) then 'CRYING'
           when 'FUSSING' = any(state_codes) then 'FUSSING'
           when 'ASLEEP' = any(state_codes) then 'ASLEEP'
           when 'SLEEPY_APPEARING' = any(state_codes) then 'SLEEPY_APPEARING'
           when 'CALM' = any(state_codes) then 'CALM'
           when 'AWAKE' = any(state_codes) then 'AWAKE'
           when 'CHEERFUL_APPEARING' = any(state_codes) then 'CHEERFUL_APPEARING'
           else 'NEUTRAL'
       end,
       visual_mapping_version = 'care-visual-v1',
       updated_by_user_id = created_by_user_id;

alter table baby_data.state_observations
    alter column updated_by_user_id set not null;

alter table baby_data.state_observations drop column source;

create or replace function baby_private.set_raw_entry_lineage()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
    if new.original_author_user_id is null then
        if new.supersedes_entry_id is null then
            new.original_author_user_id := coalesce(
                new.author_user_id,
                baby_private.current_request_user_id()
            );
        else
            select e.original_author_user_id
              into new.original_author_user_id
              from baby_data.raw_care_entries e
             where e.baby_id = new.baby_id
               and e.entry_id = new.supersedes_entry_id
               and e.status = 'CONFIRMED';
            if new.original_author_user_id is null then
                raise exception using errcode = 'P0002', message = 'B07_BASE_ENTRY_NOT_FOUND';
            end if;
        end if;
    end if;
    return new;
end;
$$;

create trigger raw_entries_lineage
before insert on baby_data.raw_care_entries
for each row execute function baby_private.set_raw_entry_lineage();

drop trigger raw_entries_immutable on baby_data.raw_care_entries;
create trigger raw_entries_immutable before update on baby_data.raw_care_entries
    for each row execute function baby_private.protect_immutable_columns(
        'entry_id', 'baby_id', 'author_user_id', 'original_author_user_id',
        'data_origin', 'confirmed_by_user_id', 'confirmed_at', 'recorded_at'
    );

drop trigger state_observations_stamp_actor on baby_data.state_observations;
create trigger state_observations_stamp_actor before insert on baby_data.state_observations
    for each row execute function baby_private.stamp_request_user_fields(
        'created_by_user_id', 'updated_by_user_id', 'confirmed_by_user_id'
    );
create trigger state_observations_stamp_updater before update on baby_data.state_observations
    for each row execute function baby_private.stamp_request_user_fields('updated_by_user_id');

drop trigger state_observations_immutable on baby_data.state_observations;
create trigger state_observations_immutable before update on baby_data.state_observations
    for each row execute function baby_private.protect_immutable_columns(
        'state_observation_id', 'baby_id', 'created_by_user_id',
        'confirmed_by_user_id', 'data_origin', 'recorded_at'
    );

drop policy state_observations_shared_update on baby_data.state_observations;
create policy state_observations_author_or_owner_update on baby_data.state_observations
    for update to baby_app
    using (baby_private.can_modify_shared_record(baby_id, created_by_user_id))
    with check (
        baby_private.can_modify_shared_record(baby_id, created_by_user_id)
        and updated_by_user_id = baby_private.current_request_user_id()
    );

-- A correction entry is private to its author, but an active owner may correct
-- a confirmed contribution. Let that actor retire the prior entry's labels so
-- the same confirmed fact is not counted twice.
drop policy label_annotations_author_update on baby_data.label_annotations;
create policy label_annotations_author_or_owner_update on baby_data.label_annotations
    for update to baby_app
    using (
        baby_private.entry_owned_by_request_user(baby_id, entry_id)
        or baby_private.is_active_owner(baby_id)
    )
    with check (
        baby_private.entry_owned_by_request_user(baby_id, entry_id)
        or baby_private.is_active_owner(baby_id)
    );

grant baby_policy_owner to postgres;
grant create on schema baby_private to baby_policy_owner;

-- Startup recovery is intentionally data-blind: callers receive only a count.
-- The policy owner can update expired author-private rows despite FORCE RLS.
set local role baby_policy_owner;
create or replace function baby_private.expire_normalization_leases()
returns bigint
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    expired_count bigint;
begin
    with expired as (
        update baby_data.normalization_runs n
           set status = 'FAILED',
               lease_expires_at = null,
               failure = jsonb_build_object(
                   'code', 'NORMALIZATION_LEASE_EXPIRED',
                   'message', 'The normalization lease expired before completion.',
                   'retryable', true
               ),
               completed_at = clock_timestamp()
         where n.status = 'RUNNING'
           and n.lease_expires_at <= clock_timestamp()
        returning n.entry_id, n.input_revision
    ), affected as (
        update baby_data.raw_care_entries e
           set status = 'NEEDS_MANUAL_REVIEW',
               version = version + 1
          from expired x
         where e.entry_id = x.entry_id
           and e.input_revision = x.input_revision
           and e.status = 'NORMALIZING'
        returning e.entry_id
    )
    select count(*) into expired_count from expired;
    return expired_count;
end;
$$;

-- A late provider result may arrive after session or membership revocation. This
-- narrow function only fences that run; it cannot read or return its content.
create or replace function baby_private.reject_normalization_completion(
    target_run_id uuid,
    target_execution_token uuid
)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    request_user_id uuid := baby_private.current_request_user_id();
    target_entry_id uuid;
    target_revision bigint;
begin
    if request_user_id is null then
        return false;
    end if;
    update baby_data.normalization_runs n
       set status = 'FAILED',
           lease_expires_at = null,
           failure = jsonb_build_object(
               'code', 'ACCESS_REVOKED',
               'message', 'Access was revoked before normalization completion.',
               'retryable', false
           ),
           completed_at = clock_timestamp()
      from baby_data.raw_care_entries e
     where n.run_id = target_run_id
       and n.execution_token = target_execution_token
       and n.status = 'RUNNING'
       and e.entry_id = n.entry_id
       and e.author_user_id = request_user_id
    returning n.entry_id, n.input_revision
         into target_entry_id, target_revision;
    if target_entry_id is null then
        return false;
    end if;
    update baby_data.raw_care_entries e
       set status = 'NEEDS_MANUAL_REVIEW', version = version + 1
     where e.entry_id = target_entry_id
       and e.input_revision = target_revision
       and e.status = 'NORMALIZING';
    return true;
end;
$$;
reset role;

alter function baby_private.expire_normalization_leases() owner to baby_policy_owner;
alter function baby_private.reject_normalization_completion(uuid, uuid)
    owner to baby_policy_owner;

revoke all on function baby_private.expire_normalization_leases()
    from public, anon, authenticated, service_role;
revoke all on function baby_private.reject_normalization_completion(uuid, uuid)
    from public, anon, authenticated, service_role;
grant execute on function baby_private.expire_normalization_leases() to baby_app;
grant execute on function baby_private.reject_normalization_completion(uuid, uuid)
    to baby_app;

revoke baby_policy_owner from postgres;
revoke create on schema baby_private from baby_policy_owner;
