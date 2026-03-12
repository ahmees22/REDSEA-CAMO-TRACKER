-- ============================================================
-- RED SEA CAMO - Supabase Schema Setup
-- Run this in Supabase SQL Editor: https://supabase.com/dashboard/project/sfzhhttgeftvjlidunvn/sql
-- ============================================================

-- 1. Profiles table (auto-linked to auth.users)
CREATE TABLE IF NOT EXISTS public.profiles (
  id UUID REFERENCES auth.users(id) ON DELETE CASCADE PRIMARY KEY,
  email TEXT,
  role TEXT DEFAULT 'reader',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Aircraft
CREATE TABLE IF NOT EXISTS public.aircraft (
  id SERIAL PRIMARY KEY,
  tail_number TEXT UNIQUE NOT NULL,
  current_fh FLOAT DEFAULT 0.0,
  current_fc INTEGER DEFAULT 0,
  util_fh_rate FLOAT DEFAULT 8.0,
  util_fc_rate FLOAT DEFAULT 4.0,
  last_log_date TIMESTAMPTZ DEFAULT NOW(),
  last_updated_by TEXT
);

-- 3. Engine Tasks
CREATE TABLE IF NOT EXISTS public.engine_tasks (
  id SERIAL PRIMARY KEY,
  aircraft_id INTEGER REFERENCES public.aircraft(id) ON DELETE CASCADE,
  task_id TEXT NOT NULL,
  description TEXT,
  task_type TEXT,
  zone TEXT,
  access TEXT,
  applicability TEXT,
  man_hours TEXT,
  task_card_ref TEXT,
  material TEXT,
  tools TEXT,
  notes TEXT,
  interval_fh FLOAT,
  interval_fc INTEGER,
  interval_days INTEGER,
  last_done_fh FLOAT DEFAULT 0.0,
  last_done_fc INTEGER DEFAULT 0,
  last_done_date TIMESTAMPTZ DEFAULT NOW(),
  last_updated_by TEXT
);

-- 4. Utilization Logs
CREATE TABLE IF NOT EXISTS public.utilization_logs (
  id SERIAL PRIMARY KEY,
  aircraft_id INTEGER REFERENCES public.aircraft(id) ON DELETE CASCADE,
  log_date TIMESTAMPTZ DEFAULT NOW(),
  logged_fh FLOAT NOT NULL,
  logged_fc INTEGER NOT NULL,
  last_updated_by TEXT
);

-- 5. Task Card PDFs
CREATE TABLE IF NOT EXISTS public.task_card_pdfs (
  id SERIAL PRIMARY KEY,
  task_id_ref TEXT NOT NULL,
  file_name TEXT NOT NULL,
  file_path TEXT NOT NULL,
  storage_url TEXT,
  last_updated_by TEXT
);

-- 6. Upload Logs
CREATE TABLE IF NOT EXISTS public.upload_logs (
  id SERIAL PRIMARY KEY,
  filename TEXT NOT NULL,
  file_size TEXT,
  file_type TEXT,
  assigned_tail TEXT,
  status TEXT,
  upload_date TIMESTAMPTZ DEFAULT NOW(),
  last_updated_by TEXT
);

-- ============================================================
-- Row Level Security (RLS)
-- ============================================================
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.aircraft ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.engine_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.utilization_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.task_card_pdfs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.upload_logs ENABLE ROW LEVEL SECURITY;

-- Allow all authenticated users to read/write all tables
CREATE POLICY "authenticated_all" ON public.aircraft FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "authenticated_all" ON public.engine_tasks FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "authenticated_all" ON public.utilization_logs FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "authenticated_all" ON public.task_card_pdfs FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "authenticated_all" ON public.upload_logs FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "own_profile" ON public.profiles FOR ALL TO authenticated USING (auth.uid() = id);

-- Service role bypass (for server-side Python client)
CREATE POLICY "service_all" ON public.aircraft FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "service_all" ON public.engine_tasks FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "service_all" ON public.utilization_logs FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "service_all" ON public.task_card_pdfs FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "service_all" ON public.upload_logs FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ============================================================
-- Auto-create profile on user signup
-- ============================================================
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.profiles (id, email, role)
  VALUES (NEW.id, NEW.email, 'reader')
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- ============================================================
-- Enable Realtime
-- ============================================================
ALTER PUBLICATION supabase_realtime ADD TABLE public.aircraft;
ALTER PUBLICATION supabase_realtime ADD TABLE public.engine_tasks;
ALTER PUBLICATION supabase_realtime ADD TABLE public.utilization_logs;

-- ============================================================
-- Seed Default Aircraft
-- ============================================================
INSERT INTO public.aircraft (tail_number, current_fh, current_fc, util_fh_rate, util_fc_rate)
VALUES
  ('SU-RSA', 0, 0, 8.0, 4.0),
  ('SU-RSB', 0, 0, 8.0, 4.0),
  ('SU-RSC', 0, 0, 8.0, 4.0),
  ('SU-RSD', 0, 0, 8.0, 4.0)
ON CONFLICT (tail_number) DO NOTHING;

-- ============================================================
-- Storage bucket for Task Card PDFs
-- ============================================================
INSERT INTO storage.buckets (id, name, public)
VALUES ('task-cards', 'task-cards', false)
ON CONFLICT (id) DO NOTHING;

CREATE POLICY "auth_upload" ON storage.objects FOR INSERT TO authenticated WITH CHECK (bucket_id = 'task-cards');
CREATE POLICY "auth_read" ON storage.objects FOR SELECT TO authenticated USING (bucket_id = 'task-cards');
CREATE POLICY "service_all" ON storage.objects FOR ALL TO service_role USING (bucket_id = 'task-cards');
