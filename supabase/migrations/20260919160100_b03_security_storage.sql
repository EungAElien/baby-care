-- B-03 least-privilege database role, RLS, and private Storage upload gate.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'baby_app') then
        create role baby_app nologin noinherit nobypassrls;
    else
        alter role baby_app nologin noinherit nobypassrls;
    end if;

    if not exists (select 1 from pg_roles where rolname = 'baby_policy_owner') then
        create role baby_policy_owner nologin noinherit bypassrls;
    else
        alter role baby_policy_owner nologin noinherit bypassrls;
    end if;
end;
$$;

-- Supabase migrations run as a managed administration role. Temporarily make
-- both local migration identities members so SECURITY DEFINER ownership can be
-- transferred, then revoke the memberships at the end of this migration.
grant baby_policy_owner to postgres;
grant create on schema baby_private to baby_policy_owner;

revoke all on schema baby_data from public, anon, authenticated, service_role;
revoke all on schema baby_private from public, anon, authenticated, service_role;
revoke create on schema public from public, anon, authenticated;

grant usage on schema baby_data to baby_app, baby_policy_owner;
grant usage on schema baby_private to baby_app, baby_policy_owner;
grant select, insert, update on all tables in schema baby_data to baby_app;

revoke delete, truncate, references, trigger on all tables in schema baby_data from baby_app;
revoke all on all tables in schema baby_data from anon, authenticated, service_role;
revoke all on all sequences in schema baby_data from public, anon, authenticated, service_role;
revoke all on all functions in schema baby_data from public, anon, authenticated, service_role;
revoke all on all functions in schema baby_private from public, anon, authenticated, service_role;

alter default privileges for role postgres in schema baby_data
    revoke all on tables from public, anon, authenticated, service_role;
alter default privileges for role postgres in schema baby_data
    revoke all on sequences from public, anon, authenticated, service_role;
alter default privileges for role postgres in schema baby_data
    revoke execute on functions from public, anon, authenticated, service_role;
alter default privileges for role postgres in schema baby_private
    revoke execute on functions from public, anon, authenticated, service_role;

create or replace function baby_private.current_request_user_id()
returns uuid
language sql
stable
set search_path = pg_catalog
as $$
    select nullif(current_setting('baby.request_user_id', true), '')::uuid
$$;

create or replace function baby_private.current_request_session_id()
returns uuid
language sql
stable
set search_path = pg_catalog
as $$
    select nullif(current_setting('baby.request_session_id', true), '')::uuid
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
$$;

create or replace function baby_private.has_active_baby_access(target_baby_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $$
    select baby_private.has_live_request_context()
       and exists (
           select 1
             from baby_data.babies b
             join baby_data.baby_memberships m on m.baby_id = b.baby_id
            where b.baby_id = target_baby_id
              and b.status = 'ACTIVE'
              and m.user_id = baby_private.current_request_user_id()
              and m.status = 'ACTIVE'
       )
$$;

create or replace function baby_private.is_active_owner(target_baby_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $$
    select baby_private.has_live_request_context()
       and exists (
           select 1
             from baby_data.babies b
             join baby_data.baby_memberships m on m.baby_id = b.baby_id
            where b.baby_id = target_baby_id
              and b.status = 'ACTIVE'
              and m.user_id = baby_private.current_request_user_id()
              and m.role = 'OWNER'
              and m.status = 'ACTIVE'
       )
$$;

create or replace function baby_private.has_membership_history(target_baby_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $$
    select baby_private.has_live_request_context()
       and exists (
           select 1
             from baby_data.baby_memberships m
            where m.baby_id = target_baby_id
              and m.user_id = baby_private.current_request_user_id()
       )
$$;

create or replace function baby_private.can_modify_shared_record(
    target_baby_id uuid,
    original_author_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $$
    select baby_private.has_active_baby_access(target_baby_id)
       and (
           original_author_id = baby_private.current_request_user_id()
           or baby_private.is_active_owner(target_baby_id)
       )
$$;

create or replace function baby_private.entry_owned_by_request_user(
    target_baby_id uuid,
    target_entry_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $$
    select baby_private.has_active_baby_access(target_baby_id)
       and exists (
           select 1
             from baby_data.raw_care_entries e
            where e.baby_id = target_baby_id
              and e.entry_id = target_entry_id
              and e.author_user_id = baby_private.current_request_user_id()
       )
$$;

create or replace function baby_private.entry_visible_to_request_user(
    target_baby_id uuid,
    target_entry_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $$
    select baby_private.has_active_baby_access(target_baby_id)
       and exists (
           select 1
             from baby_data.raw_care_entries e
            where e.baby_id = target_baby_id
              and e.entry_id = target_entry_id
              and (
                  e.author_user_id = baby_private.current_request_user_id()
                  or e.status = 'CONFIRMED'
              )
       )
$$;

grant select on
    baby_data.babies,
    baby_data.baby_memberships,
    baby_data.revoked_sessions,
    baby_data.raw_care_entries,
    baby_data.audio_assets,
    baby_data.audio_upload_grants
to baby_policy_owner;

alter function baby_private.has_live_request_context() owner to baby_policy_owner;
alter function baby_private.has_active_baby_access(uuid) owner to baby_policy_owner;
alter function baby_private.is_active_owner(uuid) owner to baby_policy_owner;
alter function baby_private.has_membership_history(uuid) owner to baby_policy_owner;
alter function baby_private.can_modify_shared_record(uuid, uuid) owner to baby_policy_owner;
alter function baby_private.entry_owned_by_request_user(uuid, uuid) owner to baby_policy_owner;
alter function baby_private.entry_visible_to_request_user(uuid, uuid) owner to baby_policy_owner;

grant execute on function baby_private.current_request_user_id() to baby_app;
grant execute on function baby_private.current_request_session_id() to baby_app;
grant execute on function baby_private.current_request_user_id() to baby_policy_owner;
grant execute on function baby_private.current_request_session_id() to baby_policy_owner;
grant execute on function baby_private.has_live_request_context() to baby_app;
grant execute on function baby_private.has_active_baby_access(uuid) to baby_app;
grant execute on function baby_private.is_active_owner(uuid) to baby_app;
grant execute on function baby_private.has_membership_history(uuid) to baby_app;
grant execute on function baby_private.can_modify_shared_record(uuid, uuid) to baby_app;
grant execute on function baby_private.entry_owned_by_request_user(uuid, uuid) to baby_app;
grant execute on function baby_private.entry_visible_to_request_user(uuid, uuid) to baby_app;

create or replace function baby_private.protect_immutable_columns()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
    column_name text;
begin
    foreach column_name in array tg_argv loop
        if to_jsonb(new) -> column_name is distinct from to_jsonb(old) -> column_name then
            raise exception using
                errcode = '23514',
                message = format('%s.%s is server-owned and immutable', tg_table_name, column_name);
        end if;
    end loop;
    return new;
end;
$$;

create or replace function baby_private.stamp_request_user_fields()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
    actor_id uuid;
    column_name text;
    payload jsonb;
begin
    if current_user <> 'baby_app' then
        return new;
    end if;

    actor_id := baby_private.current_request_user_id();
    if actor_id is null or not baby_private.has_live_request_context() then
        raise exception using errcode = '42501', message = 'missing or revoked request context';
    end if;

    payload := to_jsonb(new);
    foreach column_name in array tg_argv loop
        if payload ? column_name then
            payload := jsonb_set(payload, array[column_name], to_jsonb(actor_id), true);
        end if;
    end loop;
    new := jsonb_populate_record(new, payload);
    return new;
end;
$$;

create or replace function baby_private.stamp_request_session_field()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
    request_session_id uuid;
begin
    if current_user <> 'baby_app' then
        return new;
    end if;
    request_session_id := baby_private.current_request_session_id();
    if request_session_id is null or not baby_private.has_live_request_context() then
        raise exception using errcode = '42501', message = 'missing or revoked request context';
    end if;
    new.auth_session_id := request_session_id;
    return new;
end;
$$;

create or replace function baby_private.stamp_confirmation()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
    if current_user = 'baby_app'
       and new.status = 'CONFIRMED'
       and (tg_op = 'INSERT' or old.status <> 'CONFIRMED') then
        if not baby_private.has_live_request_context() then
            raise exception using errcode = '42501', message = 'missing or revoked request context';
        end if;
        new.confirmed_by_user_id := baby_private.current_request_user_id();
        new.confirmed_at := clock_timestamp();
    end if;
    return new;
end;
$$;

create or replace function baby_private.enforce_version_and_touch()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
    if new.version <> old.version + 1 then
        raise exception using errcode = '40001', message = format('%s version must increment by exactly one', tg_table_name);
    end if;
    new.updated_at := clock_timestamp();
    return new;
end;
$$;

create trigger babies_stamp_owner before insert on baby_data.babies
    for each row execute function baby_private.stamp_request_user_fields('owner_user_id');
create trigger invitations_stamp_inviter before insert on baby_data.invitations
    for each row execute function baby_private.stamp_request_user_fields('inviter_user_id');
create trigger consents_stamp_actor before insert on baby_data.consents
    for each row execute function baby_private.stamp_request_user_fields('actor_user_id');
create trigger observation_sessions_stamp_creator before insert on baby_data.observation_sessions
    for each row execute function baby_private.stamp_request_user_fields('created_by_user_id');
create trigger episodes_stamp_creator before insert on baby_data.episodes
    for each row execute function baby_private.stamp_request_user_fields('created_by_user_id');
create trigger audio_assets_stamp_creator before insert on baby_data.audio_assets
    for each row execute function baby_private.stamp_request_user_fields('created_by_user_id');
create trigger upload_grants_stamp_uploader before insert on baby_data.audio_upload_grants
    for each row execute function baby_private.stamp_request_user_fields('uploader_user_id');
create trigger upload_grants_stamp_session before insert on baby_data.audio_upload_grants
    for each row execute function baby_private.stamp_request_session_field();
create trigger analyses_stamp_creator before insert on baby_data.analyses
    for each row execute function baby_private.stamp_request_user_fields('created_by_user_id');
create trigger care_events_stamp_creator before insert on baby_data.care_events
    for each row execute function baby_private.stamp_request_user_fields('created_by_user_id', 'updated_by_user_id');
create trigger care_events_stamp_updater before update on baby_data.care_events
    for each row execute function baby_private.stamp_request_user_fields('updated_by_user_id');
create trigger caregiver_observations_stamp_creator before insert on baby_data.caregiver_observations
    for each row execute function baby_private.stamp_request_user_fields('created_by_user_id');
create trigger action_attempts_stamp_creator before insert on baby_data.action_attempts
    for each row execute function baby_private.stamp_request_user_fields('created_by_user_id', 'updated_by_user_id');
create trigger action_attempts_stamp_updater before update on baby_data.action_attempts
    for each row execute function baby_private.stamp_request_user_fields('updated_by_user_id');
create trigger action_groups_stamp_creator before insert on baby_data.action_groups
    for each row execute function baby_private.stamp_request_user_fields('created_by_user_id');
create trigger outcomes_stamp_creator before insert on baby_data.outcomes
    for each row execute function baby_private.stamp_request_user_fields('created_by_user_id', 'updated_by_user_id');
create trigger outcomes_stamp_updater before update on baby_data.outcomes
    for each row execute function baby_private.stamp_request_user_fields('updated_by_user_id');
create trigger raw_entries_stamp_author before insert on baby_data.raw_care_entries
    for each row execute function baby_private.stamp_request_user_fields('author_user_id');
create trigger raw_entries_stamp_confirmation_insert before insert on baby_data.raw_care_entries
    for each row execute function baby_private.stamp_confirmation();
create trigger raw_entries_stamp_confirmation before update on baby_data.raw_care_entries
    for each row execute function baby_private.stamp_confirmation();
create trigger state_observations_stamp_actor before insert on baby_data.state_observations
    for each row execute function baby_private.stamp_request_user_fields('created_by_user_id', 'confirmed_by_user_id');
create trigger label_annotations_stamp_confirmer before insert on baby_data.label_annotations
    for each row execute function baby_private.stamp_request_user_fields('confirmed_by_user_id');
create trigger reminder_settings_stamp_recipient before insert on baby_data.reminder_settings
    for each row execute function baby_private.stamp_request_user_fields('recipient_user_id');
create trigger reminders_stamp_recipient before insert on baby_data.reminders
    for each row execute function baby_private.stamp_request_user_fields('recipient_user_id');
create trigger record_coverage_stamp_creator before insert on baby_data.record_coverage
    for each row execute function baby_private.stamp_request_user_fields('created_by_user_id');
create trigger deletion_jobs_stamp_requester before insert on baby_data.deletion_jobs
    for each row execute function baby_private.stamp_request_user_fields('requester_user_id');

create trigger babies_immutable before update on baby_data.babies
    for each row execute function baby_private.protect_immutable_columns('baby_id', 'owner_user_id', 'recorded_at');
create trigger memberships_immutable before update on baby_data.baby_memberships
    for each row execute function baby_private.protect_immutable_columns('membership_id', 'baby_id', 'user_id', 'role', 'recorded_at');
create trigger invitations_immutable before update on baby_data.invitations
    for each row execute function baby_private.protect_immutable_columns('invite_id', 'baby_id', 'inviter_user_id', 'token_sha256', 'recorded_at');
create trigger episodes_immutable before update on baby_data.episodes
    for each row execute function baby_private.protect_immutable_columns('episode_id', 'baby_id', 'created_by_user_id', 'data_origin', 'recorded_at');
create trigger observation_sessions_immutable before update on baby_data.observation_sessions
    for each row execute function baby_private.protect_immutable_columns('session_id', 'baby_id', 'created_by_user_id', 'source_id', 'recorded_at');
create trigger audio_assets_immutable before update on baby_data.audio_assets
    for each row execute function baby_private.protect_immutable_columns('audio_id', 'baby_id', 'episode_id', 'created_by_user_id', 'bucket_id', 'object_key', 'data_origin', 'recorded_at');
create trigger upload_grants_immutable before update on baby_data.audio_upload_grants
    for each row execute function baby_private.protect_immutable_columns('upload_id', 'baby_id', 'episode_id', 'audio_id', 'uploader_user_id', 'auth_session_id', 'bucket_id', 'object_key', 'max_bytes', 'recorded_at');
create trigger analyses_immutable before update on baby_data.analyses
    for each row execute function baby_private.protect_immutable_columns('analysis_id', 'baby_id', 'episode_id', 'audio_id', 'created_by_user_id', 'data_origin', 'recorded_at');
create trigger care_events_immutable before update on baby_data.care_events
    for each row execute function baby_private.protect_immutable_columns('care_event_id', 'baby_id', 'created_by_user_id', 'recorded_at');
create trigger caregiver_observations_immutable before update on baby_data.caregiver_observations
    for each row execute function baby_private.protect_immutable_columns('observation_id', 'baby_id', 'episode_id', 'created_by_user_id', 'recorded_at');
create trigger action_attempts_immutable before update on baby_data.action_attempts
    for each row execute function baby_private.protect_immutable_columns('action_id', 'baby_id', 'episode_id', 'created_by_user_id', 'recorded_at');
create trigger action_groups_immutable before update on baby_data.action_groups
    for each row execute function baby_private.protect_immutable_columns('action_group_id', 'baby_id', 'episode_id', 'created_by_user_id', 'recorded_at');
create trigger outcomes_immutable before update on baby_data.outcomes
    for each row execute function baby_private.protect_immutable_columns('outcome_id', 'baby_id', 'created_by_user_id', 'recorded_at');
create trigger raw_entries_immutable before update on baby_data.raw_care_entries
    for each row execute function baby_private.protect_immutable_columns('entry_id', 'baby_id', 'author_user_id', 'confirmed_by_user_id', 'confirmed_at', 'recorded_at');
create trigger state_observations_immutable before update on baby_data.state_observations
    for each row execute function baby_private.protect_immutable_columns('state_observation_id', 'baby_id', 'created_by_user_id', 'confirmed_by_user_id', 'recorded_at');
create trigger label_annotations_immutable before update on baby_data.label_annotations
    for each row execute function baby_private.protect_immutable_columns('annotation_id', 'baby_id', 'entry_id', 'confirmed_by_user_id', 'recorded_at');
create trigger reminder_settings_immutable before update on baby_data.reminder_settings
    for each row execute function baby_private.protect_immutable_columns('reminder_setting_id', 'baby_id', 'recipient_user_id', 'kind', 'recorded_at');
create trigger reminders_immutable before update on baby_data.reminders
    for each row execute function baby_private.protect_immutable_columns('reminder_id', 'baby_id', 'recipient_user_id', 'recorded_at');
create trigger record_coverage_immutable before update on baby_data.record_coverage
    for each row execute function baby_private.protect_immutable_columns('record_coverage_id', 'baby_id', 'created_by_user_id', 'recorded_at');
create trigger deletion_jobs_immutable before update on baby_data.deletion_jobs
    for each row execute function baby_private.protect_immutable_columns('deletion_job_id', 'baby_id', 'requester_user_id', 'scope', 'resource_id', 'requested_at');

do $$
declare
    target_table text;
begin
    for target_table in
        select c.table_name
          from information_schema.columns c
          join information_schema.columns v
            on v.table_schema = c.table_schema
           and v.table_name = c.table_name
           and v.column_name = 'version'
         where c.table_schema = 'baby_data'
           and c.column_name = 'updated_at'
    loop
        execute format(
            'create trigger %I_version_touch before update on baby_data.%I for each row execute function baby_private.enforce_version_and_touch()',
            target_table,
            target_table
        );
    end loop;
end;
$$;

do $$
declare
    target_table text;
begin
    for target_table in select tablename from pg_tables where schemaname = 'baby_data'
    loop
        execute format('alter table baby_data.%I enable row level security', target_table);
        execute format('alter table baby_data.%I force row level security', target_table);
    end loop;
end;
$$;

create policy babies_select on baby_data.babies
    for select to baby_app
    using (baby_private.has_active_baby_access(baby_id));
create policy babies_insert on baby_data.babies
    for insert to baby_app
    with check (
        baby_private.has_live_request_context()
        and owner_user_id = baby_private.current_request_user_id()
        and status = 'ACTIVE'
    );
create policy babies_update on baby_data.babies
    for update to baby_app
    using (baby_private.is_active_owner(baby_id))
    with check (baby_private.is_active_owner(baby_id));

create policy memberships_select on baby_data.baby_memberships
    for select to baby_app
    using (baby_private.has_active_baby_access(baby_id));
create policy memberships_insert on baby_data.baby_memberships
    for insert to baby_app
    with check (
        baby_private.is_active_owner(baby_id)
        or (
            role = 'OWNER'
            and status = 'ACTIVE'
            and user_id = baby_private.current_request_user_id()
            and exists (
                select 1 from baby_data.babies b
                 where b.baby_id = baby_memberships.baby_id
                   and b.owner_user_id = baby_private.current_request_user_id()
                   and b.status = 'ACTIVE'
            )
        )
    );
create policy memberships_update on baby_data.baby_memberships
    for update to baby_app
    using (
        baby_private.is_active_owner(baby_id)
        or (user_id = baby_private.current_request_user_id() and status = 'ACTIVE')
    )
    with check (
        baby_private.is_active_owner(baby_id)
        or user_id = baby_private.current_request_user_id()
    );

create policy invitations_all_owner on baby_data.invitations
    for all to baby_app
    using (baby_private.is_active_owner(baby_id))
    with check (baby_private.is_active_owner(baby_id));

create policy consents_select on baby_data.consents
    for select to baby_app
    using (
        actor_user_id = baby_private.current_request_user_id()
        and baby_private.has_membership_history(baby_id)
        or (
            scope in ('SERVICE_PROCESSING', 'AUDIO_RETENTION', 'BABY_TRAINING')
            and baby_private.has_active_baby_access(baby_id)
        )
    );
create policy consents_insert on baby_data.consents
    for insert to baby_app
    with check (
        actor_user_id = baby_private.current_request_user_id()
        and (
            scope in ('CONTRIBUTOR_TRAINING', 'SHARED_USE')
            and baby_private.has_membership_history(baby_id)
            or scope in ('SERVICE_PROCESSING', 'AUDIO_RETENTION', 'BABY_TRAINING')
            and baby_private.is_active_owner(baby_id)
        )
    );

create policy user_preferences_own on baby_data.user_preferences
    for all to baby_app
    using (user_id = baby_private.current_request_user_id() and baby_private.has_live_request_context())
    with check (user_id = baby_private.current_request_user_id() and baby_private.has_live_request_context());

create policy revoked_sessions_own on baby_data.revoked_sessions
    for select to baby_app
    using (user_id = baby_private.current_request_user_id());
create policy revoked_sessions_insert_own on baby_data.revoked_sessions
    for insert to baby_app
    with check (user_id = baby_private.current_request_user_id());

do $$
declare
    target_table text;
begin
    foreach target_table in array array[
        'observation_sessions',
        'observation_windows',
        'episodes',
        'audio_assets',
        'analyses',
        'context_snapshots',
        'recommendations',
        'caregiver_observations',
        'action_groups',
        'action_group_members',
        'state_observations',
        'record_coverage'
    ]
    loop
        execute format(
            'create policy %I_shared_select on baby_data.%I for select to baby_app using (baby_private.has_active_baby_access(baby_id))',
            target_table,
            target_table
        );
        execute format(
            'create policy %I_shared_insert on baby_data.%I for insert to baby_app with check (baby_private.has_active_baby_access(baby_id))',
            target_table,
            target_table
        );
        execute format(
            'create policy %I_shared_update on baby_data.%I for update to baby_app using (baby_private.has_active_baby_access(baby_id)) with check (baby_private.has_active_baby_access(baby_id))',
            target_table,
            target_table
        );
    end loop;
end;
$$;

create policy upload_grants_select on baby_data.audio_upload_grants
    for select to baby_app
    using (
        uploader_user_id = baby_private.current_request_user_id()
        and baby_private.has_active_baby_access(baby_id)
    );
create policy upload_grants_insert on baby_data.audio_upload_grants
    for insert to baby_app
    with check (
        uploader_user_id = baby_private.current_request_user_id()
        and auth_session_id = baby_private.current_request_session_id()
        and baby_private.has_active_baby_access(baby_id)
    );
create policy upload_grants_update on baby_data.audio_upload_grants
    for update to baby_app
    using (
        uploader_user_id = baby_private.current_request_user_id()
        and baby_private.has_active_baby_access(baby_id)
    )
    with check (
        uploader_user_id = baby_private.current_request_user_id()
        and baby_private.has_active_baby_access(baby_id)
    );

do $$
declare
    target_table text;
begin
    foreach target_table in array array['care_events', 'action_attempts', 'outcomes']
    loop
        execute format(
            'create policy %I_record_select on baby_data.%I for select to baby_app using (baby_private.has_active_baby_access(baby_id))',
            target_table,
            target_table
        );
        execute format(
            'create policy %I_record_insert on baby_data.%I for insert to baby_app with check (baby_private.has_active_baby_access(baby_id) and created_by_user_id = baby_private.current_request_user_id())',
            target_table,
            target_table
        );
        execute format(
            'create policy %I_record_update on baby_data.%I for update to baby_app using (baby_private.can_modify_shared_record(baby_id, created_by_user_id)) with check (baby_private.can_modify_shared_record(baby_id, created_by_user_id) and updated_by_user_id = baby_private.current_request_user_id())',
            target_table,
            target_table
        );
    end loop;
end;
$$;

create policy raw_entries_author_select on baby_data.raw_care_entries
    for select to baby_app
    using (baby_private.entry_visible_to_request_user(baby_id, entry_id));
create policy raw_entries_author_insert on baby_data.raw_care_entries
    for insert to baby_app
    with check (
        author_user_id = baby_private.current_request_user_id()
        and baby_private.has_active_baby_access(baby_id)
    );
create policy raw_entries_author_update on baby_data.raw_care_entries
    for update to baby_app
    using (
        author_user_id = baby_private.current_request_user_id()
        and baby_private.has_active_baby_access(baby_id)
    )
    with check (
        author_user_id = baby_private.current_request_user_id()
        and baby_private.has_active_baby_access(baby_id)
    );

create policy normalization_runs_author_select on baby_data.normalization_runs
    for select to baby_app
    using (baby_private.entry_owned_by_request_user(baby_id, entry_id));
create policy normalization_runs_author_insert on baby_data.normalization_runs
    for insert to baby_app
    with check (baby_private.entry_owned_by_request_user(baby_id, entry_id));
create policy normalization_runs_author_update on baby_data.normalization_runs
    for update to baby_app
    using (baby_private.entry_owned_by_request_user(baby_id, entry_id))
    with check (baby_private.entry_owned_by_request_user(baby_id, entry_id));

create policy label_annotations_visible_select on baby_data.label_annotations
    for select to baby_app
    using (baby_private.entry_visible_to_request_user(baby_id, entry_id));
create policy label_annotations_author_insert on baby_data.label_annotations
    for insert to baby_app
    with check (baby_private.entry_owned_by_request_user(baby_id, entry_id));
create policy label_annotations_author_update on baby_data.label_annotations
    for update to baby_app
    using (baby_private.entry_owned_by_request_user(baby_id, entry_id))
    with check (baby_private.entry_owned_by_request_user(baby_id, entry_id));

create policy reminder_settings_personal on baby_data.reminder_settings
    for all to baby_app
    using (
        recipient_user_id = baby_private.current_request_user_id()
        and baby_private.has_active_baby_access(baby_id)
    )
    with check (
        recipient_user_id = baby_private.current_request_user_id()
        and baby_private.has_active_baby_access(baby_id)
    );

create policy reminders_personal on baby_data.reminders
    for all to baby_app
    using (
        recipient_user_id = baby_private.current_request_user_id()
        and baby_private.has_active_baby_access(baby_id)
    )
    with check (
        recipient_user_id = baby_private.current_request_user_id()
        and baby_private.has_active_baby_access(baby_id)
    );

create policy deletion_jobs_requester_select on baby_data.deletion_jobs
    for select to baby_app
    using (
        requester_user_id = baby_private.current_request_user_id()
        and baby_private.has_membership_history(baby_id)
    );
create policy deletion_jobs_requester_insert on baby_data.deletion_jobs
    for insert to baby_app
    with check (
        requester_user_id = baby_private.current_request_user_id()
        and (
            scope = 'ALL' and baby_private.is_active_owner(baby_id)
            or scope = 'MY_CONTRIBUTIONS' and baby_private.has_membership_history(baby_id)
            or scope in ('CARE_EVENT', 'CARE_ENTRY') and baby_private.has_active_baby_access(baby_id)
        )
    );
create policy deletion_jobs_requester_update on baby_data.deletion_jobs
    for update to baby_app
    using (requester_user_id = baby_private.current_request_user_id())
    with check (requester_user_id = baby_private.current_request_user_id());

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('baby-audio', 'baby-audio', false, 25000000, null)
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

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
               nullif(claims ->> 'session_id', '')::uuid as session_id
          from request_claims
    )
    select r.user_id is not null
       and r.session_id is not null
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

create or replace function baby_private.deny_browser_storage_read()
returns boolean
language plpgsql
stable
set search_path = pg_catalog
as $$
begin
    raise exception using
        errcode = '42501',
        message = 'browser audio listing, reading, and signing are not allowed';
end;
$$;

alter function baby_private.can_upload_audio_object(text, text) owner to baby_policy_owner;

-- Function EXECUTE defaults are permissive in PostgreSQL. Revoke them after
-- every helper exists, then grant back only the two explicitly required paths.
revoke execute on all functions in schema baby_private
    from public, anon, authenticated, service_role;
grant execute on function baby_private.current_request_user_id() to baby_app;
grant execute on function baby_private.current_request_session_id() to baby_app;
grant execute on function baby_private.has_live_request_context() to baby_app;
grant execute on function baby_private.has_active_baby_access(uuid) to baby_app;
grant execute on function baby_private.is_active_owner(uuid) to baby_app;
grant execute on function baby_private.has_membership_history(uuid) to baby_app;
grant execute on function baby_private.can_modify_shared_record(uuid, uuid) to baby_app;
grant execute on function baby_private.entry_owned_by_request_user(uuid, uuid) to baby_app;
grant execute on function baby_private.entry_visible_to_request_user(uuid, uuid) to baby_app;
grant usage on schema baby_private to authenticated;
grant execute on function baby_private.can_upload_audio_object(text, text) to authenticated;
grant execute on function baby_private.deny_browser_storage_read() to authenticated;

drop policy if exists baby_audio_read_denied on storage.objects;
create policy baby_audio_read_denied
on storage.objects
for select
to authenticated
using (
    case
        when bucket_id = 'baby-audio' then baby_private.deny_browser_storage_read()
        else false
    end
);

drop policy if exists baby_audio_insert_authorized on storage.objects;
create policy baby_audio_insert_authorized
on storage.objects
for insert
to authenticated
with check (
    bucket_id = 'baby-audio'
    and baby_private.can_upload_audio_object(bucket_id, name)
);

comment on role baby_app is 'NOLOGIN least-privilege role used by a separate runtime login; no DDL and no RLS bypass.';
comment on role baby_policy_owner is 'NOLOGIN policy helper owner with BYPASSRLS and SELECT only on authorization inputs.';
comment on function baby_private.can_upload_audio_object(text, text) is 'Boolean-only Storage policy gate; not in an exposed Data API schema.';
comment on policy baby_audio_insert_authorized on storage.objects is 'Authenticated insert only. Browser SELECT is an explicit error and no UPDATE or DELETE policy is granted.';
comment on policy baby_audio_read_denied on storage.objects is 'Raises an authorization error instead of returning a misleading successful empty list for private audio.';

revoke baby_policy_owner from postgres;
revoke create on schema baby_private from baby_policy_owner;
