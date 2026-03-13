import os, datetime, threading, webbrowser, json, re, tempfile
from math import floor
from functools import wraps
import pandas as pd
from flask import (Flask, render_template_string, request, jsonify,
                   send_file, redirect, url_for, session)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv, set_key
#from google import genai

load_dotenv()

app = Flask(__name__, static_folder=os.path.abspath(os.path.dirname(__name__)), static_url_path='/static')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'camo-tracker-secret')
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.abspath(os.path.dirname(__name__)), 'uploads')
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# ── Supabase clients ────────────────────────────────────────────────────────
try:
    from supabase import create_client
    SUPA_URL = os.getenv('SUPABASE_URL', '')
    SUPA_KEY = os.getenv('SUPABASE_KEY', '')
    SUPA_SVC = os.getenv('SUPABASE_SERVICE_KEY', SUPA_KEY)
    supa_anon  = create_client(SUPA_URL, SUPA_KEY)   # auth ops
    supa_admin = create_client(SUPA_URL, SUPA_SVC)   # DB ops (bypasses RLS)
    SUPABASE_READY = True
    print("✅ Supabase connected")
except Exception as e:
    print(f"⚠️  Supabase not available: {e}")
    SUPABASE_READY = False
    supa_anon = supa_admin = None

# ── Auth middleware ──────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def current_user_email():
    return session.get('user', {}).get('email', 'system')

def db():
    """Shortcut to the admin Supabase client."""
    return supa_admin

# ── Universal Excel Opener (unchanged logic) ─────────────────────────────────
class _CsvFakeExcel:
    def __init__(self, df, name='CSV_IMPORT'):
        self._df = df
        self.sheet_names = [name]
    def parse(self, sheet_name=None, **kwargs):
        return self._df

def open_any_excel(file_path):
    import shutil
    suffix = os.path.splitext(file_path)[1].lower()
    with open(file_path, 'rb') as f:
        magic = f.read(8)
    is_zip = magic[:4] == b'PK\x03\x04'
    is_cfb = magic[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
    is_text = not is_zip and not is_cfb
    last_err = 'Unknown'

    if suffix == '.xlsb' and is_zip:
        try:
            import pyxlsb
            with pyxlsb.open_workbook(file_path) as wb: _ = wb.sheets
            xls = pd.ExcelFile(file_path, engine='pyxlsb')
            return xls, xls.sheet_names, 'pyxlsb', None
        except Exception as e: last_err = str(e)

    engines = ['openpyxl'] if is_zip else (['xlrd','pyxlsb'] if is_cfb else ['openpyxl','xlrd','pyxlsb',None])
    for eng in engines:
        try:
            xls = pd.ExcelFile(file_path, engine=eng)
            return xls, xls.sheet_names, eng, None
        except Exception as e: last_err = str(e)

    if suffix == '.xlsb':
        alt = file_path.replace('.xlsb', '.xlsx')
        try:
            shutil.copy2(file_path, alt)
            for eng in ['openpyxl', 'xlrd', None]:
                try:
                    xls = pd.ExcelFile(alt, engine=eng)
                    return xls, xls.sheet_names, eng, None
                except Exception as e: last_err = str(e)
        finally:
            try: os.remove(alt)
            except: pass

    if is_text:
        for enc in ['utf-8-sig', 'utf-8', 'cp1252', 'latin-1']:
            for sep in [',', ';', '\t', '|']:
                try:
                    df = pd.read_csv(file_path, encoding=enc, sep=sep,
                                     header=None, dtype=str, on_bad_lines='skip')
                    if df.shape[1] >= 2 and len(df) >= 3:
                        fake = _CsvFakeExcel(df)
                        return fake, fake.sheet_names, 'csv', None
                except Exception as e: last_err = str(e)

    return None, [], None, last_err

def read_sheet(xls_obj, sheet, engine):
    """Read a sheet from either a real ExcelFile or a _CsvFakeExcel."""
    if isinstance(xls_obj, _CsvFakeExcel):
        return xls_obj.parse()
    return pd.read_excel(xls_obj if hasattr(xls_obj, 'io') else xls_obj._reader._reader.file_path
                         if hasattr(xls_obj, '_reader') else xls_obj,
                         sheet_name=sheet, header=None, engine=engine)

# ── Forecast Engine ──────────────────────────────────────────────────────────
def forecast_tasks(aircraft: dict) -> list:
    today = datetime.date.today()
    tasks_res = db().table('engine_tasks').select('*').eq('aircraft_id', aircraft['id']).execute()
    tasks = tasks_res.data or []
    forecasts = []

    for t in tasks:
        estimated = []
        reasons   = []
        fh_rate   = aircraft.get('util_fh_rate', 8) or 8
        fc_rate   = aircraft.get('util_fc_rate', 4) or 4
        curr_fh   = aircraft.get('current_fh', 0) or 0
        curr_fc   = aircraft.get('current_fc', 0) or 0

        if t.get('interval_fh') and t['interval_fh'] > 0:
            due_fh = (t.get('last_done_fh') or 0) + t['interval_fh']
            rem_fh = max(due_fh - curr_fh, 0)
            d_fh = today + datetime.timedelta(days=floor(rem_fh / fh_rate))
            estimated.append(('FH', d_fh, rem_fh))
            reasons.append(f"Due at {due_fh} FH (Rem: {rem_fh:.0f} FH @ {fh_rate} FH/day)")

        if t.get('interval_fc') and t['interval_fc'] > 0:
            due_fc = (t.get('last_done_fc') or 0) + t['interval_fc']
            rem_fc = max(due_fc - curr_fc, 0)
            d_fc = today + datetime.timedelta(days=floor(rem_fc / fc_rate))
            estimated.append(('FC', d_fc, rem_fc))
            reasons.append(f"Due at {due_fc} FC (Rem: {rem_fc:.0f} FC @ {fc_rate} FC/day)")

        if t.get('interval_days') and t['interval_days'] > 0:
            last_dt = t.get('last_done_date')
            if last_dt:
                try:
                    last_d = datetime.datetime.fromisoformat(str(last_dt).replace('Z','+00:00')).date()
                except: last_d = today
            else: last_d = today
            due_d = last_d + datetime.timedelta(days=t['interval_days'])
            rem_d = max((due_d - today).days, 0)
            estimated.append(('DY', due_d, rem_d))
            reasons.append(f"Calendar Due: {due_d} (Rem: {rem_d} days)")

        if not estimated: continue
        estimated.sort(key=lambda x: x[1])
        lim = estimated[0]
        due_date = lim[1]
        days_left = (due_date - today).days
        status = 'Overdue' if days_left < 0 else 'Warning' if days_left <= 5 else 'Normal'

        # PDF link
        pdf_res = db().table('task_card_pdfs').select('id').eq('task_id_ref', t['task_id']).maybe_single().execute()
        pdf_id = (pdf_res.data or {}).get('id')

        final_reason = reasons[0] if reasons else ''
        for i, (typ, _, _) in enumerate(estimated):
            if typ == lim[0] and i < len(reasons): final_reason = reasons[i]; break

        forecasts.append({
            'task_id': t['task_id'], 'description': t.get('description',''),
            'task_type': t.get('task_type',''), 'due_date': due_date.strftime('%Y-%m-%d'),
            'status': status, 'pdf_id': pdf_id,
            'reasoning': f"'{lim[0]}' rule: {final_reason}",
            'zone': t.get('zone'), 'access': t.get('access'),
            'applicability': t.get('applicability'), 'man_hours': t.get('man_hours'),
            'task_card_ref': t.get('task_card_ref'), 'material': t.get('material'),
            'tools': t.get('tools'), 'notes': t.get('notes'),
        })

    return forecasts

# ═══════════════════════════════════════════════════════════════
# LOGIN / AUTH TEMPLATE
# ═══════════════════════════════════════════════════════════════
LOGIN_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Camo-Tracker — Login</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
  body{font-family:'Segoe UI',sans-serif;}
  .tab-btn{transition:all .25s;}
  .tab-btn.active{border-color:#dc2626;color:#dc2626;font-weight:700;}
  .input-field{width:100%;padding:.75rem 1rem .75rem 2.75rem;border:2px solid #e5e7eb;border-radius:.5rem;background:#f9fafb;font-size:.95rem;transition:border .2s;}
  .input-field:focus{outline:none;border-color:#dc2626;background:#fff;}
</style>
</head>
<body class="min-h-screen bg-gradient-to-br from-gray-900 via-red-950 to-gray-900 flex items-center justify-center p-4">
<div class="w-full max-w-md">
  <!-- Logo -->
  <div class="text-center mb-8">
    <div class="inline-flex items-center justify-center w-20 h-20 bg-white rounded-full shadow-2xl mb-4 border-4 border-red-600">
      <i class="fas fa-plane-departure text-3xl text-red-600"></i>
    </div>
    <h1 class="text-3xl font-extrabold text-white tracking-tight">Camo-Tracker</h1>
    <p class="text-gray-400 text-sm mt-1">RED SEA Airlines · CAMO System</p>
  </div>
  <div class="bg-white rounded-2xl shadow-2xl overflow-hidden">
    {% if mode == 'otp' %}
    <!-- OTP / Reset Password Form -->
    <div class="p-8">
      <h2 class="text-xl font-bold text-gray-800 mb-1"><i class="fas fa-key text-red-500 mr-2"></i>Password Reset</h2>
      <p class="text-gray-500 text-sm mb-6">Enter the OTP sent to your email and choose a new password.</p>
      {% if error %}<div class="bg-red-50 border-l-4 border-red-500 text-red-700 p-3 rounded mb-4 text-sm">{{ error }}</div>{% endif %}
      <form method="POST" action="/verify-otp">
        <div class="mb-4 relative">
          <i class="fas fa-envelope absolute left-3 top-3.5 text-gray-400"></i>
          <input name="email" type="email" placeholder="Your email" required class="input-field">
        </div>
        <div class="mb-4 relative">
          <i class="fas fa-hashtag absolute left-3 top-3.5 text-gray-400"></i>
          <input name="token" type="text" placeholder="OTP / Verification Code" required class="input-field">
        </div>
        <div class="mb-6 relative">
          <i class="fas fa-lock absolute left-3 top-3.5 text-gray-400"></i>
          <input name="password" type="password" placeholder="New Password" required class="input-field">
        </div>
        <button type="submit" class="w-full bg-red-600 hover:bg-red-700 text-white font-bold py-3 rounded-lg transition">Reset Password</button>
        <a href="/login" class="block text-center text-sm text-red-600 mt-4 hover:underline">← Back to Login</a>
      </form>
    </div>
    {% elif mode == 'forgot' %}
    <!-- Forgot Password Form -->
    <div class="p-8">
      <h2 class="text-xl font-bold text-gray-800 mb-1"><i class="fas fa-envelope-open-text text-red-500 mr-2"></i>Forgot Password</h2>
      <p class="text-gray-500 text-sm mb-6">Enter your email and we will send an OTP to reset your password.</p>
      {% if error %}<div class="bg-red-50 border-l-4 border-red-500 text-red-700 p-3 rounded mb-4 text-sm">{{ error }}</div>{% endif %}
      {% if success %}<div class="bg-green-50 border-l-4 border-green-500 text-green-700 p-3 rounded mb-4 text-sm">{{ success }}</div>{% endif %}
      <form method="POST" action="/forgot-password">
        <div class="mb-6 relative">
          <i class="fas fa-envelope absolute left-3 top-3.5 text-gray-400"></i>
          <input name="email" type="email" placeholder="Your account email" required class="input-field">
        </div>
        <button type="submit" class="w-full bg-red-600 hover:bg-red-700 text-white font-bold py-3 rounded-lg transition">Send OTP</button>
        <a href="/login" class="block text-center text-sm text-red-600 mt-4 hover:underline">← Back to Login</a>
      </form>
    </div>
    {% else %}
    <!-- Login / Register Tabs -->
    <div class="flex border-b">
      <button id="tab-login" onclick="switchTab('login')" class="tab-btn active flex-1 py-4 text-sm border-b-2 border-transparent">
        <i class="fas fa-sign-in-alt mr-1"></i> Login
      </button>
      <button id="tab-register" onclick="switchTab('register')" class="tab-btn flex-1 py-4 text-sm border-b-2 border-transparent text-gray-500">
        <i class="fas fa-user-plus mr-1"></i> Register
      </button>
    </div>
    <div class="p-8">
      {% if error %}<div class="bg-red-50 border-l-4 border-red-500 text-red-700 p-3 rounded mb-5 text-sm"><i class="fas fa-exclamation-circle mr-2"></i>{{ error }}</div>{% endif %}
      {% if success %}<div class="bg-green-50 border-l-4 border-green-500 text-green-700 p-3 rounded mb-5 text-sm"><i class="fas fa-check-circle mr-2"></i>{{ success }}</div>{% endif %}
      <!-- LOGIN FORM -->
      <form id="form-login" method="POST" action="/login">
        <div class="mb-4 relative">
          <i class="fas fa-envelope absolute left-3 top-3.5 text-gray-400"></i>
          <input name="email" type="email" placeholder="Email address" required class="input-field">
        </div>
        <div class="mb-2 relative">
          <i class="fas fa-lock absolute left-3 top-3.5 text-gray-400"></i>
          <input name="password" type="password" placeholder="Password" required class="input-field">
        </div>
        <div class="text-right mb-6">
          <a href="/forgot-password" class="text-xs text-red-600 hover:underline">Forgot Password?</a>
        </div>
        <button type="submit" class="w-full bg-red-600 hover:bg-red-700 text-white font-bold py-3 rounded-lg transition flex items-center justify-center gap-2">
          <i class="fas fa-shield-alt"></i> Secure Login
        </button>
      </form>
      <!-- REGISTER FORM -->
      <form id="form-register" method="POST" action="/register" class="hidden">
        <div class="mb-4 relative">
          <i class="fas fa-envelope absolute left-3 top-3.5 text-gray-400"></i>
          <input name="email" type="email" placeholder="Email address" required class="input-field">
        </div>
        <div class="mb-6 relative">
          <i class="fas fa-lock absolute left-3 top-3.5 text-gray-400"></i>
          <input name="password" type="password" placeholder="Password (min 6 chars)" required minlength="6" class="input-field">
        </div>
        <button type="submit" class="w-full bg-gray-800 hover:bg-black text-white font-bold py-3 rounded-lg transition flex items-center justify-center gap-2">
          <i class="fas fa-user-plus"></i> Create Account
        </button>
      </form>
      <!-- GOOGLE OAUTH -->
      <div class="mt-5">
        <div class="relative flex items-center"><div class="flex-1 border-t border-gray-200"></div><span class="px-3 text-xs text-gray-400">or</span><div class="flex-1 border-t border-gray-200"></div></div>
        <a href="/auth/google" class="mt-4 w-full flex items-center justify-center gap-3 border-2 border-gray-200 hover:border-gray-400 rounded-lg py-3 font-semibold text-gray-700 transition bg-white hover:bg-gray-50">
          <svg class="w-5 h-5" viewBox="0 0 533.5 544.3"><path d="M533.5 278.4c0-18.5-1.5-37.1-4.7-55.3H272.1v104.8h147c-6.1 33.8-25.7 63.7-54.4 82.7v68h87.7c51.5-47.4 81.1-117.4 81.1-200.2z" fill="#4285f4"/><path d="M272.1 544.3c73.4 0 135.3-24.1 180.4-65.7l-87.7-68c-24.4 16.6-55.9 26-92.6 26-71 0-131.2-47.9-152.8-112.3H28.9v70.1c46.2 91.9 140.3 149.9 243.2 149.9z" fill="#34a853"/><path d="M119.3 324.3c-11.4-33.8-11.4-70.4 0-104.2V150H28.9c-38.6 76.9-38.6 167.5 0 244.4l90.4-70.1z" fill="#fbbc04"/><path d="M272.1 107.7c38.8-.6 76.3 14 104.4 40.8l77.7-77.7C405 24.6 339.7-.8 272.1 0 169.2 0 75.1 58 28.9 150l90.4 70.1c21.5-64.5 81.8-112.4 152.8-112.4z" fill="#ea4335"/></svg>
          Continue with Google
        </a>
      </div>
    </div>
    {% endif %}
  </div>
  <p class="text-center text-gray-500 text-xs mt-6">© {{ year }} RED SEA Airlines · CAMO Department</p>
</div>
<script>
function switchTab(t){
  document.getElementById('form-login').classList.toggle('hidden', t!=='login');
  document.getElementById('form-register').classList.toggle('hidden', t!=='register');
  ['login','register'].forEach(x=>{
    let b=document.getElementById('tab-'+x);
    b.classList.toggle('active',x===t);
    b.classList.toggle('text-gray-500',x!==t);
    b.classList.toggle('border-red-600',x===t);
    b.classList.toggle('border-transparent',x!==t);
  });
}
</script>
</body></html>
"""


# ═══════════════════════════════════════════════════════════════
# AUTH ROUTES
# ═══════════════════════════════════════════════════════════════
@app.route('/login', methods=['GET','POST'])
def login():
    if 'user' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email    = request.form.get('email','').strip()
        password = request.form.get('password','').strip()
        try:
            res = supa_anon.auth.sign_in_with_password({"email": email, "password": password})
            session['user'] = {'email': res.user.email, 'id': str(res.user.id)}
            session['access_token'] = res.session.access_token
            return redirect(url_for('index'))
        except Exception as e:
            return render_template_string(LOGIN_TEMPLATE, mode='login',
                error="Invalid email or password.", success=None, year=datetime.date.today().year)
    return render_template_string(LOGIN_TEMPLATE, mode='login', error=None, success=None, year=datetime.date.today().year)

@app.route('/register', methods=['POST'])
def register():
    email    = request.form.get('email','').strip()
    password = request.form.get('password','').strip()
    try:
        supa_anon.auth.sign_up({"email": email, "password": password})
        return render_template_string(LOGIN_TEMPLATE, mode='login', error=None,
            success="Account created! Check your email to confirm, then log in.", year=datetime.date.today().year)
    except Exception as e:
        return render_template_string(LOGIN_TEMPLATE, mode='login',
            error=str(e), success=None, year=datetime.date.today().year)

@app.route('/logout')
def logout():
    try: supa_anon.auth.sign_out()
    except: pass
    session.clear()
    return redirect(url_for('login'))

@app.route('/forgot-password', methods=['GET','POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email','').strip()
        try:
            supa_anon.auth.reset_password_for_email(email)
            return render_template_string(LOGIN_TEMPLATE, mode='forgot', error=None,
                success="OTP sent! Check your email inbox.", year=datetime.date.today().year)
        except Exception as e:
            return render_template_string(LOGIN_TEMPLATE, mode='forgot',
                error=str(e), success=None, year=datetime.date.today().year)
    return render_template_string(LOGIN_TEMPLATE, mode='forgot', error=None, success=None, year=datetime.date.today().year)

@app.route('/verify-otp', methods=['POST'])
def verify_otp():
    email    = request.form.get('email','').strip()
    token    = request.form.get('token','').strip()
    password = request.form.get('password','').strip()
    try:
        res = supa_anon.auth.verify_otp({"email": email, "token": token, "type": "recovery"})
        supa_anon.auth.update_user({"password": password})
        return render_template_string(LOGIN_TEMPLATE, mode='login', error=None,
            success="Password reset! Please log in.", year=datetime.date.today().year)
    except Exception as e:
        return render_template_string(LOGIN_TEMPLATE, mode='otp',
            error=str(e), success=None, year=datetime.date.today().year)

@app.route('/auth/google')
def auth_google():
    try:
        res = supa_anon.auth.sign_in_with_oauth({"provider": "google",
              "options": {"redirect_to": request.host_url + "auth/callback"}})
        return redirect(res.url)
    except Exception as e:
        return redirect(url_for('login'))

@app.route('/auth/callback')
def auth_callback():
    # Supabase returns token in URL fragment — JS must extract and POST it
    return render_template_string("""
<!DOCTYPE html><html><head><title>Authenticating...</title></head>
<body>
<script>
const hash = window.location.hash.substring(1);
const params = new URLSearchParams(hash);
const access_token = params.get('access_token');
if(access_token){
  fetch('/api/auth/session', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({access_token})
  }).then(r=>r.json()).then(d=>{ window.location.href='/'; });
} else { window.location.href='/login'; }
</script>
<p style="font-family:sans-serif;text-align:center;margin-top:100px">Authenticating...</p>
</body></html>
""")

@app.route('/api/auth/session', methods=['POST'])
def api_auth_session():
    data = request.json or {}
    token = data.get('access_token','')
    try:
        user_res = supa_anon.auth.get_user(token)
        session['user'] = {'email': user_res.user.email, 'id': str(user_res.user.id)}
        session['access_token'] = token
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 401

# ═══════════════════════════════════════════════════════════════
# MAIN INDEX  
# ═══════════════════════════════════════════════════════════════
@app.route('/')
@login_required
def index():
    msg  = request.args.get('msg','')
    tail = request.args.get('tail','')
    aircraft_res = db().table('aircraft').select('*').execute()
    all_aircraft = aircraft_res.data or []
    if not all_aircraft:
        return render_template_string(LOGIN_TEMPLATE, mode='login', error='No aircraft in DB. Run schema SQL first.', success=None, year=datetime.date.today().year)
    aircraft = next((a for a in all_aircraft if a['tail_number']==tail), all_aircraft[0])
    forecasts = forecast_tasks(aircraft)
    user_email = current_user_email()
    supa_url = os.getenv('SUPABASE_URL','')
    supa_key = os.getenv('SUPABASE_KEY','')
    return render_template_string(MAIN_TEMPLATE,
        aircraft=aircraft, all_aircraft=all_aircraft, forecasts=forecasts,
        msg=msg, user_email=user_email, supa_url=supa_url, supa_key=supa_key)

# ═══════════════════════════════════════════════════════════════
# API: Calendar / Search / Logs
# ═══════════════════════════════════════════════════════════════
@app.route('/api/calendar_data')
@login_required
def calendar_data():
    tail = request.args.get('tail','')
    ac_res = db().table('aircraft').select('*').eq('tail_number', tail).maybe_single().execute()
    ac = ac_res.data
    if not ac: return jsonify([])
    forecasts = forecast_tasks(ac)
    events = []
    for f in forecasts:
        color = '#ef4444' if f['status']=='Overdue' else '#f59e0b' if f['status']=='Warning' else '#3b82f6'
        events.append({'title': f['task_id'], 'start': f['due_date'], 'color': color,
                       'extendedProps': f})
    return jsonify(events)

@app.route('/api/search')
@login_required
def search():
    q = request.args.get('q','').strip().upper()
    tail = request.args.get('tail','')
    ac_res = db().table('aircraft').select('id').eq('tail_number', tail).maybe_single().execute()
    ac = ac_res.data
    if not ac or not q: return jsonify([])
    tasks = db().table('engine_tasks').select('*').eq('aircraft_id', ac['id']).execute().data or []
    results = [t for t in tasks if q in str(t.get('task_id','')).upper() or q in str(t.get('description','')).upper()][:30]
    forecasts = []
    for t in results:
        pdf = db().table('task_card_pdfs').select('id').eq('task_id_ref', t['task_id']).maybe_single().execute().data
        forecasts.append({**t, 'pdf_id': (pdf or {}).get('id'), 'status':'Normal', 'due_date':'N/A', 'reasoning':'Search result'})
    return jsonify(forecasts)

@app.route('/api/upload_logs')
@login_required
def upload_logs():
    logs = db().table('upload_logs').select('*').order('upload_date', desc=True).limit(50).execute().data or []
    return jsonify(logs)

@app.route('/update_util_rates', methods=['POST'])
@login_required
def update_util_rates():
    tail   = request.form.get('tail','')
    fh_r   = float(request.form.get('fh_rate', 8))
    fc_r   = float(request.form.get('fc_rate', 4))
    cur_fh = float(request.form.get('current_fh', 0))
    cur_fc = float(request.form.get('current_fc', 0))
    db().table('aircraft').update({
        'util_fh_rate': fh_r, 'util_fc_rate': fc_r,
        'current_fh': cur_fh, 'current_fc': cur_fc,
        'last_updated_by': current_user_email()
    }).eq('tail_number', tail).execute()
    ac   = db().table('aircraft').select('id').eq('tail_number', tail).maybe_single().execute().data
    if ac:
        db().table('utilization_logs').insert({
            'aircraft_id': ac['id'], 'logged_fh': cur_fh, 'logged_fc': cur_fc,
            'last_updated_by': current_user_email()
        }).execute()
    return redirect(url_for('index', tail=tail, msg='Aircraft status updated successfully.'))

@app.route('/api/update_task_metadata', methods=['POST'])
@login_required
def update_task_metadata():
    data = request.json or {}
    task_id = data.get('task_id')
    if not task_id: return jsonify({'error':'No task_id'}), 400
    update = {k: data[k] for k in ['last_done_fh','last_done_fc','last_done_date','notes'] if k in data}
    update['last_updated_by'] = current_user_email()
    db().table('engine_tasks').update(update).eq('task_id', task_id).execute()
    return jsonify({'ok': True})

# ═══════════════════════════════════════════════════════════════
# UPLOAD EXCEL  
# ═══════════════════════════════════════════════════════════════
@app.route('/upload_excel', methods=['POST'])
@login_required
def upload_excel():
    files = request.files.getlist('files')
    tail  = request.form.get('tail','')
    error_msgs, processed = [], []

    for file in files:
        if not file or not file.filename: continue
        suffix = os.path.splitext(file.filename)[1].lower()
        try:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            file.save(tmp.name); tmp.close()
            file_path = tmp.name

            # Identify target aircraft from filename
            fn_upper = file.filename.upper()
            ac_res = db().table('aircraft').select('*').execute().data or []
            target = next((a for a in ac_res if a['tail_number'].replace('-','') in fn_upper.replace('-','')), None)
            if not target and tail:
                target = next((a for a in ac_res if a['tail_number']==tail), None)
            if not target:
                error_msgs.append(f"Could not match {file.filename} to any aircraft."); continue

            # Log upload
            log_row = {'filename': file.filename, 'file_type': suffix, 'assigned_tail': target['tail_number'],
                       'status': 'Processing', 'last_updated_by': current_user_email()}
            db().table('upload_logs').insert(log_row).execute()

            xls, sheet_names, read_engine, err = open_any_excel(file_path)
            if xls is None:
                error_msgs.append(f"Cannot open {file.filename}: {err}"); continue

            # Delete old tasks
            db().table('engine_tasks').delete().eq('aircraft_id', target['id']).execute()

            gemini_key = os.getenv('GEMINI_API_KEY','')
            if not gemini_key:
                error_msgs.append("Gemini API Key missing!"); continue

            for sheet in sheet_names:
                try:
                    if isinstance(xls, _CsvFakeExcel):
                        df = xls.parse()
                    else:
                        df = pd.read_excel(file_path, sheet_name=sheet, header=None, engine=read_engine)
                except Exception as se:
                    continue
                if df.empty or len(df) < 3: continue

                csv_snip = df.head(15).to_csv(index=False)
                prompt = f"""You are a Senior Aviation CAMO Engineer. Parse this Excel/CSV sheet.
Output ONLY valid JSON:
{{"is_task_list":true,"header_row_index":4,"aircraft_status":{{"current_fh":12500,"current_fc":8000,"report_date":"2024-03-10"}},"columns":{{"task_id":0,"description":1,"interval_fh":8,"interval_fc":9,"interval_dy":10,"last_done_date":12,"last_done_fh":13,"last_done_fc":14}}}}
If not a task sheet return: {{"is_task_list":false}}
CSV:
{csv_snip}"""
                try:
                    client = genai.Client(api_key=gemini_key)
                    raw    = client.models.generate_content(model='gemini-2.0-flash', contents=prompt).text.strip()
                    s,e    = raw.find('{'), raw.rfind('}')
                    res    = json.loads(raw[s:e+1]) if s!=-1 else {}
                except: continue

                if not res.get('is_task_list'): continue
                col_map    = res.get('columns', {})
                header_idx = int(res.get('header_row_index', 0))
                ai_status  = res.get('aircraft_status', {})
                pkg        = sheet.replace('TASK LIST','').strip() or 'GENERAL'

                if ai_status:
                    upd = {'last_updated_by': current_user_email()}
                    if ai_status.get('current_fh'): upd['current_fh'] = float(ai_status['current_fh'])
                    if ai_status.get('current_fc'): upd['current_fc'] = int(ai_status['current_fc'])
                    db().table('aircraft').update(upd).eq('id', target['id']).execute()

                if 'task_id' not in col_map: continue
                df_data = df.iloc[header_idx+1:].dropna(how='all')

                batch = []
                for _, row in df_data.iterrows():
                    def gv(k):
                        idx = col_map.get(k)
                        if idx is not None:
                            try:
                                v = row.iloc[int(idx)]
                                if pd.notna(v) and str(v).strip() not in ('','nan'): return v
                            except: pass
                        return None

                    tid = gv('task_id')
                    if not tid: continue

                    # Parse interval
                    fh,fc,dy = None,None,None
                    if gv('interval_fh'):
                        try: fh = float(gv('interval_fh'))
                        except: pass
                    if gv('interval_fc'):
                        try: fc = int(float(gv('interval_fc')))
                        except: pass
                    if gv('interval_dy'):
                        try: dy = int(float(gv('interval_dy')))
                        except: pass
                    # Unified interval string fallback
                    istr = str(gv('interval') or '')
                    if istr and not fh:
                        m = re.search(r'([\d,\.]+)\s*FH', istr, re.I)
                        if m: fh = float(m.group(1).replace(',',''))
                    if istr and not fc:
                        m = re.search(r'([\d,\.]+)\s*FC', istr, re.I)
                        if m: fc = int(float(m.group(1).replace(',','')))

                    last_date = None
                    vd = gv('last_done_date')
                    if vd:
                        if isinstance(vd, datetime.datetime): last_date = vd.isoformat()
                        else:
                            pd_d = pd.to_datetime(vd, errors='coerce')
                            if pd.notna(pd_d): last_date = pd_d.isoformat()

                    batch.append({
                        'aircraft_id': target['id'], 'task_id': str(tid).strip(),
                        'description': f"[{pkg}] {str(gv('description') or 'N/A').strip()}",
                        'task_type': pkg, 'zone': str(gv('zone') or '')[:50] or None,
                        'access': str(gv('access') or '')[:50] or None,
                        'applicability': str(gv('applicability') or '')[:50] or None,
                        'man_hours': str(gv('man_hours') or '')[:20] or None,
                        'task_card_ref': str(gv('task_card_ref') or '')[:50] or None,
                        'material': str(gv('material') or '')[:200] or None,
                        'tools': str(gv('tools') or '')[:200] or None,
                        'notes': str(gv('notes') or '')[:500] or None,
                        'interval_fh': fh, 'interval_fc': fc, 'interval_days': dy,
                        'last_done_fh': float(gv('last_done_fh') or 0) if gv('last_done_fh') else 0.0,
                        'last_done_fc': int(float(gv('last_done_fc') or 0)) if gv('last_done_fc') else 0,
                        'last_done_date': last_date or datetime.datetime.utcnow().isoformat(),
                        'last_updated_by': current_user_email()
                    })
                    if len(batch) >= 100:
                        db().table('engine_tasks').insert(batch).execute(); batch=[]
                if batch:
                    db().table('engine_tasks').insert(batch).execute()

            processed.append(target['tail_number'])
            db().table('upload_logs').update({'status':'Success','file_size':f"{len(sheet_names)} sheets"}).eq('filename', file.filename).execute()

        except Exception as e:
            error_msgs.append(f"Error: {file.filename}: {str(e)[:120]}")
        finally:
            try: os.remove(file_path)
            except: pass

    msg = ("Processed: " + ", ".join(set(processed)) + ". " if processed else "")
    if error_msgs: msg += "| Warnings: " + " | ".join(error_msgs)
    return redirect(url_for('index', tail=tail, msg=msg))

# ═══════════════════════════════════════════════════════════════
# PDF UPLOAD / DOWNLOAD  
# ═══════════════════════════════════════════════════════════════
@app.route('/upload_pdf', methods=['POST'])
@login_required
def upload_pdf():
    file    = request.files.get('pdf_file')
    task_id = request.form.get('task_id_ref','').strip()
    tail    = request.form.get('tail','')
    if not file or not task_id: return redirect(url_for('index', tail=tail, msg='Missing file or task ID.'))
    fname = secure_filename(file.filename)
    fpath = os.path.join(app.config['UPLOAD_FOLDER'], fname)
    file.save(fpath)
    # Remove old PDF for this task
    db().table('task_card_pdfs').delete().eq('task_id_ref', task_id).execute()
    db().table('task_card_pdfs').insert({
        'task_id_ref': task_id, 'file_name': fname, 'file_path': fpath,
        'last_updated_by': current_user_email()
    }).execute()
    return redirect(url_for('index', tail=tail, msg=f'PDF uploaded for task {task_id}.'))

@app.route('/download_pdf/<int:pdf_id>')
@login_required
def download_pdf(pdf_id):
    row = db().table('task_card_pdfs').select('*').eq('id', pdf_id).maybe_single().execute().data
    if not row: return "PDF not found", 404
    return send_file(row['file_path'], as_attachment=False, mimetype='application/pdf')

# ═══════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════
@app.route('/save_gemini_key', methods=['POST'])
@login_required
def save_gemini_key():
    key  = request.form.get('gemini_key','').strip()
    tail = request.form.get('tail','')
    if key:
        set_key('.env', 'GEMINI_API_KEY', key)
        load_dotenv(override=True)
    return redirect(url_for('index', tail=tail, msg='Gemini API Key saved.'))

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
MAIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Camo-Tracker — {{ aircraft.tail_number }}</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<link href="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.9/index.global.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.9/index.global.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<style>
body{font-family:'Segoe UI',sans-serif;background:#f1f5f9;}
.status-Overdue{background:#fee2e2;border-left:4px solid #ef4444;color:#991b1b;}
.status-Warning{background:#fef9c3;border-left:4px solid #f59e0b;color:#92400e;}
.status-Normal{background:#eff6ff;border-left:4px solid #3b82f6;color:#1e3a5f;}
.tab-content{display:none;}.tab-content.active{display:block;}
#pdf-modal{display:none;position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.7);}
</style>
</head>
<body>
<!-- NAVBAR -->
<nav class="bg-gray-900 text-white px-6 py-3 flex items-center justify-between shadow-lg">
  <div class="flex items-center gap-3">
    <img src="/static/REDSEA Airlines Logo.png" class="h-10 rounded" onerror="this.style.display='none'">
    <div><p class="font-bold text-lg leading-tight">Camo-Tracker</p><p class="text-xs text-gray-400">RED SEA Airlines · CAMO</p></div>
  </div>
  <div class="flex items-center gap-4">
    <!-- Aircraft Selector -->
    <select onchange="location.href='/?tail='+this.value" class="bg-gray-700 text-white text-sm rounded px-3 py-1.5 border border-gray-600">
      {% for ac in all_aircraft %}
      <option value="{{ ac.tail_number }}" {% if ac.tail_number==aircraft.tail_number %}selected{% endif %}>{{ ac.tail_number }}</option>
      {% endfor %}
    </select>
    <span class="text-xs text-gray-400 hidden md:block"><i class="fas fa-user mr-1"></i>{{ user_email }}</span>
    <a href="/logout" class="bg-red-600 hover:bg-red-700 text-xs px-3 py-1.5 rounded transition"><i class="fas fa-sign-out-alt mr-1"></i>Logout</a>
  </div>
</nav>

{% if msg %}
<div id="msg-bar" class="{% if 'Error' in msg or 'Warning' in msg or 'error' in msg %}bg-amber-50 border-amber-400 text-amber-800{% else %}bg-green-50 border-green-400 text-green-800{% endif %} border-l-4 px-6 py-3 text-sm flex justify-between items-center">
  <span><i class="fas fa-info-circle mr-2"></i>{{ msg }}</span>
  <button onclick="document.getElementById('msg-bar').remove()" class="ml-4 font-bold">×</button>
</div>
{% endif %}

<!-- MAIN TABS -->
<div class="max-w-screen-2xl mx-auto px-4 py-4">
  <!-- Tab Buttons -->
  <div class="flex gap-2 mb-4 flex-wrap">
    {% for tab,icon,label in [('tasks','fa-tasks','Tasks Log'),('calendar','fa-calendar-alt','Calendar'),('data','fa-database','Data Management')] %}
    <button onclick="switchTab('{{tab}}')" id="btn-{{tab}}" class="tab-switch px-4 py-2 rounded text-sm font-semibold border-2 transition {% if loop.first %}bg-red-600 text-white border-red-600{% else %}bg-white text-gray-600 border-gray-200 hover:border-red-400{% endif %}">
      <i class="fas {{icon}} mr-1"></i>{{label}}
    </button>
    {% endfor %}
  </div>

  <!-- TASKS TAB -->
  <div id="tab-tasks" class="tab-content active">
    <!-- Status Bar -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
      {% set overdue=forecasts|selectattr('status','eq','Overdue')|list|length %}
      {% set warning=forecasts|selectattr('status','eq','Warning')|list|length %}
      <div class="bg-white rounded-lg p-4 shadow text-center"><p class="text-2xl font-bold text-gray-800">{{ forecasts|length }}</p><p class="text-xs text-gray-500 mt-1">Total Tasks</p></div>
      <div class="bg-red-50 rounded-lg p-4 shadow text-center"><p class="text-2xl font-bold text-red-600">{{ overdue }}</p><p class="text-xs text-gray-500 mt-1">Overdue</p></div>
      <div class="bg-yellow-50 rounded-lg p-4 shadow text-center"><p class="text-2xl font-bold text-yellow-600">{{ warning }}</p><p class="text-xs text-gray-500 mt-1">≤5 Days</p></div>
      <div class="bg-blue-50 rounded-lg p-4 shadow text-center"><p class="text-2xl font-bold text-blue-600">{{ aircraft.current_fh|round(1) }}</p><p class="text-xs text-gray-500 mt-1">Current FH</p></div>
    </div>
    <!-- Task Table -->
    <div class="bg-white rounded-lg shadow overflow-auto">
      <table class="min-w-full text-xs">
        <thead class="bg-gray-800 text-white sticky top-0">
          <tr>
            {% for h in ['Task ID','Description','Package','Due Date','Status','FH Interval','FC Interval','Days Interval','Zone','Access','Man-Hrs','PDF'] %}
            <th class="px-3 py-2 text-left font-semibold">{{h}}</th>{% endfor %}
          </tr>
        </thead>
        <tbody>
          {% for f in forecasts %}
          <tr class="status-{{f.status}} border-b border-white hover:opacity-80 transition">
            <td class="px-3 py-1.5 font-mono font-bold">{{f.task_id}}</td>
            <td class="px-3 py-1.5 max-w-xs truncate" title="{{f.description}}">{{f.description}}</td>
            <td class="px-3 py-1.5">{{f.task_type}}</td>
            <td class="px-3 py-1.5 font-semibold">{{f.due_date}}</td>
            <td class="px-3 py-1.5"><span class="px-2 py-0.5 rounded text-xs font-bold {% if f.status=='Overdue' %}bg-red-200 text-red-800{% elif f.status=='Warning' %}bg-yellow-200 text-yellow-800{% else %}bg-blue-200 text-blue-800{% endif %}">{{f.status}}</span></td>
            <td class="px-3 py-1.5">-</td><td class="px-3 py-1.5">-</td><td class="px-3 py-1.5">-</td>
            <td class="px-3 py-1.5">{{f.zone or '-'}}</td>
            <td class="px-3 py-1.5">{{f.access or '-'}}</td>
            <td class="px-3 py-1.5">{{f.man_hours or '-'}}</td>
            <td class="px-3 py-1.5">
              {% if f.pdf_id %}<button onclick="openPdfModal('/download_pdf/{{f.pdf_id}}','{{f.task_id}}')" class="bg-gray-800 text-white px-2 py-0.5 rounded text-xs"><i class="fas fa-file-pdf text-red-400 mr-1"></i>View</button>
              {% else %}<span class="text-gray-400 text-xs">Missing</span>{% endif %}
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  <!-- CALENDAR TAB -->
  <div id="tab-calendar" class="tab-content">
    <div class="bg-white rounded-lg shadow p-4"><div id="calendar-el"></div></div>
  </div>

  <!-- DATA MANAGEMENT TAB -->
  <div id="tab-data" class="tab-content">
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- Upload Excel -->
      <div class="bg-white p-6 rounded-lg shadow">
        <form action="/upload_excel" method="POST" enctype="multipart/form-data">
          <input type="hidden" name="tail" value="{{ aircraft.tail_number }}">
          <h3 class="font-bold text-lg mb-2"><i class="fas fa-file-excel text-green-600 mr-2"></i>Master DB Ingestion</h3>
          <p class="text-sm text-gray-500 mb-4">Upload MASTER files (.xlsb/.xlsx/.xls/.csv). System auto-matches to aircraft by filename.</p>
          <input type="file" name="files" multiple accept=".xls,.xlsx,.xlsb,.xlsm,.ods,.csv" class="block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded file:border-0 file:font-semibold file:bg-green-50 file:text-green-700 mb-4 border border-gray-200 p-2 rounded">
          <button type="submit" class="w-full bg-green-600 hover:bg-green-700 text-white font-bold py-2 rounded"><i class="fas fa-upload mr-2"></i>Upload & Process</button>
        </form>
      </div>
      <!-- Util Rates -->
      <div class="bg-white p-6 rounded-lg shadow">
        <form action="/update_util_rates" method="POST">
          <input type="hidden" name="tail" value="{{ aircraft.tail_number }}">
          <h3 class="font-bold text-lg mb-4"><i class="fas fa-tachometer-alt text-blue-600 mr-2"></i>Aircraft Status</h3>
          <div class="grid grid-cols-2 gap-3">
            <div><label class="text-xs font-semibold text-gray-600">Current FH</label><input name="current_fh" type="number" step="0.1" value="{{ aircraft.current_fh }}" class="w-full border rounded px-3 py-2 text-sm mt-1"></div>
            <div><label class="text-xs font-semibold text-gray-600">Current FC</label><input name="current_fc" type="number" value="{{ aircraft.current_fc }}" class="w-full border rounded px-3 py-2 text-sm mt-1"></div>
            <div><label class="text-xs font-semibold text-gray-600">FH Rate/day</label><input name="fh_rate" type="number" step="0.1" value="{{ aircraft.util_fh_rate }}" class="w-full border rounded px-3 py-2 text-sm mt-1"></div>
            <div><label class="text-xs font-semibold text-gray-600">FC Rate/day</label><input name="fc_rate" type="number" step="0.1" value="{{ aircraft.util_fc_rate }}" class="w-full border rounded px-3 py-2 text-sm mt-1"></div>
          </div>
          <button type="submit" class="mt-4 w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 rounded"><i class="fas fa-save mr-2"></i>Save Status</button>
        </form>
      </div>
      <!-- Gemini Key -->
      <div class="bg-white p-6 rounded-lg shadow lg:col-span-2">
        <form action="/save_gemini_key" method="POST" class="flex gap-3 items-end">
          <input type="hidden" name="tail" value="{{ aircraft.tail_number }}">
          <div class="flex-1"><label class="text-xs font-semibold text-gray-600 block mb-1"><i class="fas fa-robot text-purple-600 mr-1"></i>Gemini API Key</label>
          <input name="gemini_key" type="password" placeholder="AIza..." class="w-full border rounded px-3 py-2 text-sm"></div>
          <button type="submit" class="bg-purple-600 hover:bg-purple-700 text-white font-bold py-2 px-5 rounded whitespace-nowrap">Save Key</button>
        </form>
      </div>
    </div>
  </div>
</div>

<!-- PDF Modal -->
<div id="pdf-modal">
  <div class="flex flex-col h-full">
    <div class="bg-gray-900 text-white px-6 py-3 flex justify-between items-center">
      <span id="pdf-title" class="font-bold"></span>
      <div class="flex gap-3">
        <a id="pdf-download" href="#" download class="bg-blue-600 hover:bg-blue-700 px-4 py-1.5 rounded text-sm"><i class="fas fa-download mr-1"></i>Download</a>
        <button onclick="closePdfModal()" class="bg-red-600 hover:bg-red-700 px-4 py-1.5 rounded text-sm"><i class="fas fa-times mr-1"></i>Close</button>
      </div>
    </div>
    <iframe id="pdf-frame" src="" class="flex-1 w-full border-0"></iframe>
  </div>
</div>

<script>
// Tab switching
function switchTab(t){
  document.querySelectorAll('.tab-content').forEach(el=>el.classList.remove('active'));
  document.getElementById('tab-'+t).classList.add('active');
  document.querySelectorAll('.tab-switch').forEach(b=>{
    b.classList.toggle('bg-red-600',false);b.classList.toggle('text-white',false);b.classList.toggle('border-red-600',false);
    b.classList.add('bg-white','text-gray-600','border-gray-200');
  });
  let btn=document.getElementById('btn-'+t);
  btn.classList.remove('bg-white','text-gray-600','border-gray-200');
  btn.classList.add('bg-red-600','text-white','border-red-600');
  if(t==='calendar') initCalendar();
}

// PDF Modal
function openPdfModal(url,taskId){
  document.getElementById('pdf-frame').src=url;
  document.getElementById('pdf-download').href=url;
  document.getElementById('pdf-title').textContent='Task Card: '+taskId;
  document.getElementById('pdf-modal').style.display='flex';
  document.getElementById('pdf-modal').style.flexDirection='column';
}
function closePdfModal(){
  document.getElementById('pdf-modal').style.display='none';
  document.getElementById('pdf-frame').src='';
}

// FullCalendar
let calInit=false;
function initCalendar(){
  if(calInit) return; calInit=true;
  fetch('/api/calendar_data?tail={{ aircraft.tail_number }}')
    .then(r=>r.json()).then(events=>{
      new FullCalendar.Calendar(document.getElementById('calendar-el'),{
        initialView:'dayGridMonth', height:'auto', events:events,
        eventClick:function(info){
          let p=info.event.extendedProps;
          if(p.pdf_id) openPdfModal('/download_pdf/'+p.pdf_id, p.task_id);
        }
      }).render();
    });
}

// Supabase Realtime
const {createClient}=supabase;
const sb=createClient('{{ supa_url }}','{{ supa_key }}');
sb.channel('realtime-aircraft')
  .on('postgres_changes',{event:'*',schema:'public',table:'aircraft'},()=>{ window.location.reload(); })
  .on('postgres_changes',{event:'*',schema:'public',table:'engine_tasks'},()=>{ window.location.reload(); })
  .subscribe();
</script>
</body></html>"""

if __name__ == '__main__':
    print("Starting Aviation Maintenance Planning System (Camo-Tracker)...")
    threading.Timer(1.5, lambda: webbrowser.open('http://127.0.0.1:5000')).start()
    app.run(debug=False, port=5000)

