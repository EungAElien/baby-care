\set ON_ERROR_STOP on

-- This script runs through one physical psql connection. Request identity is
-- transaction-local so rollback/commit cannot leak it to the next request.
begin;
set local role baby_app;
select set_config('baby.request_user_id', '10000000-0000-4000-8000-000000000001', true);
select set_config('baby.request_session_id', '10000000-0000-4000-8000-000000000011', true);
do $$
begin
    if (select count(*) from baby_data.babies) <> 2 then
        raise exception 'owner request did not see the expected two active memberships';
    end if;
end;
$$;
rollback;

begin;
set local role baby_app;
do $$
begin
    if baby_private.current_request_user_id() is not null
       or baby_private.current_request_session_id() is not null then
        raise exception 'rolled-back request context leaked into the next transaction';
    end if;
    if (select count(*) from baby_data.babies) <> 0 then
        raise exception 'missing request context did not fail closed';
    end if;
end;
$$;

select set_config('baby.request_user_id', '10000000-0000-4000-8000-000000000002', true);
select set_config('baby.request_session_id', '10000000-0000-4000-8000-000000000012', true);
do $$
begin
    if (select count(*) from baby_data.babies) <> 1 then
        raise exception 'caregiver request inherited another user access';
    end if;
end;
$$;

-- Reusing the connection for another user requires an explicit replacement in
-- the same transaction; stale values are never accepted implicitly.
select set_config('baby.request_user_id', '10000000-0000-4000-8000-000000000001', true);
select set_config('baby.request_session_id', '10000000-0000-4000-8000-000000000011', true);
do $$
begin
    if (select count(*) from baby_data.babies) <> 2 then
        raise exception 'explicit cross-user context replacement failed';
    end if;
end;
$$;
commit;

begin;
set local role baby_app;
do $$
begin
    if baby_private.current_request_user_id() is not null
       or baby_private.current_request_session_id() is not null then
        raise exception 'committed request context leaked into the next transaction';
    end if;
    if (select count(*) from baby_data.babies) <> 0 then
        raise exception 'post-commit request without context did not fail closed';
    end if;
end;
$$;
rollback;

select 'context_reset_ok' as result;
