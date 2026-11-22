-- =====================================================================
--  SA AIM LABS · Supabase cloud sync schema
--  Supabase Dashboard → SQL Editor 에 이 파일을 그대로 붙여 실행하세요.
--  한 사람당 한 행(user_id = auth.users.id)에 앱 전체 기록(payload JSONB)을
--  저장하고, RLS 로 본인 행만 읽고 쓸 수 있게 막습니다.
-- =====================================================================

create extension if not exists "pgcrypto";

create table if not exists public.user_profiles (
  user_id    uuid primary key references auth.users (id) on delete cascade,
  nickname   text,
  payload    jsonb not null default '{}'::jsonb,
  app_version text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on table public.user_profiles is 'SA AIM LABS 사용자별 클라우드 백업 (훈련 기록·순위표·설정)';
comment on column public.user_profiles.payload is 'records / leaderboard / rotation / settings 스냅샷';

-- ---------------------------------------------------------------- RLS
alter table public.user_profiles enable row level security;

drop policy if exists "user_profiles select own" on public.user_profiles;
create policy "user_profiles select own"
  on public.user_profiles for select
  using (auth.uid() = user_id);

drop policy if exists "user_profiles insert own" on public.user_profiles;
create policy "user_profiles insert own"
  on public.user_profiles for insert
  with check (auth.uid() = user_id);

drop policy if exists "user_profiles update own" on public.user_profiles;
create policy "user_profiles update own"
  on public.user_profiles for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "user_profiles delete own" on public.user_profiles;
create policy "user_profiles delete own"
  on public.user_profiles for delete
  using (auth.uid() = user_id);

-- ---------------------------------------------------------------- updated_at
create or replace function public.touch_user_profiles()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  new.nickname = coalesce(new.nickname, old.nickname);
  return new;
end;
$$;

drop trigger if exists trg_user_profiles_touch on public.user_profiles;
create trigger trg_user_profiles_touch
  before update on public.user_profiles
  for each row execute function public.touch_user_profiles();

-- ---------------------------------------------------------------- 권한
-- anon 키로 접근하되 RLS 가 본인 행만 허용합니다.
grant select, insert, update, delete on public.user_profiles to anon, authenticated;

-- 앱은 upsert(on_conflict=user_id) 를 쓰므로 PK 가 있으면 충분합니다.
create index if not exists user_profiles_updated_at_idx
  on public.user_profiles (updated_at desc);
