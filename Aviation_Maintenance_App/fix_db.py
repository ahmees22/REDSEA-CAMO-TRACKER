import os
import requests
from dotenv import load_dotenv

load_dotenv()

SUPA_URL = os.getenv('SUPABASE_URL', '')
SUPA_SVC = os.getenv('SUPABASE_SERVICE_KEY', '')

# URL for executing SQL via the REST API (requires a stored function, but we can try direct query if enabled)
# Supabase REST API doesn't allow direct DDL (ALTER TABLE) easily without a postgres function.
# But we can use the undocumented /pg/ query endpoint or create a python script using psycopg2.

# Since psycopg2 might not be installed, we will use httpx/requests to the GraphQL or Postgres meta endpoints if possible.
# Actually, the most reliable way to execute DDL from python without psycopg2 is... wait, we cannot easily execute arbitrary SQL via REST without a function.

# Let's check if psycopg2 is installed in the venv
print("Checking for psycopg2...")
