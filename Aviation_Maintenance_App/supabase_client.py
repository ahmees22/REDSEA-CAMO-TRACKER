"""
supabase_client.py
Singleton Supabase clients for the CAMO Tracker app.
- supa_admin : service_role key — bypasses RLS, used for all server-side DB ops
- supa_anon  : anon key — used for auth operations only
"""
import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env")

# Anon client — for auth (sign_in, sign_up, OAuth, reset password)
supa_anon: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Admin client — service_role, bypasses RLS for all server DB operations
supa_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY or SUPABASE_KEY)
