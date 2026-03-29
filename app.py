import os, datetime, threading, webbrowser, json, re, tempfile
from math import floor
from functools import wraps
import pandas as pd
from flask import (Flask, render_template_string, request, jsonify,
                   send_file, redirect, url_for, session)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv, set_key
import google.generativeai as genai  # ✅ تم التصحيح هنا

load_dotenv()

app = Flask(__name__, static_folder=os.path.abspath(os.path.join(os.path.dirname(__file__), 'static')), static_url_path='/static')
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
        # Bypass login logic entirely: auto-inject a standard user session if empty
        if 'user' not in session:
            session['user'] = {'email': 'admin@redsea.com', 'id': 'system'}
        return f(*args, **kwargs)
    return decorated

def current_user_email():
    return session.get('user', {}).get('email', 'system')

def db():
    """Shortcut to the admin Supabase client with safety check."""
    if not SUPABASE_READY or supa_admin is None:
        print("❌ Supabase DB access attempted but not ready.")
        class Silencer:
            def table(self, *a, **k): return self
            def select(self, *a, **k): return self
            def insert(self, *a, **k): return self
            def update(self, *a, **k): return self
            def delete(self, *a, **k): return self
            def eq(self, *a, **k): return self
            def order(self, *a, **k): return self
            def limit(self, *a, **k): return self
            def maybe_single(self, *a, **k): return self
            def execute(self, *a, **k):
                class Res: data = None; error = "DB Not Connected"
                return Res()
        return Silencer()
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
    # Login system is completely bypassed
    if 'user' not in session:
        session['user'] = {'email': 'admin@redsea.com', 'id': 'system'}
    return redirect(url_for('index'))

@app.route('/register', methods=['POST'])
def register():
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

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
    
    # Self-healing: Seed default aircraft if DB is empty
    if not all_aircraft:
        try:
            # Insert only the tail number to avoid PGRST204 errors if other columns don't exist yet
            defaults = [
                {'tail_number': 'SU-RSA'},
                {'tail_number': 'SU-RSB'},
                {'tail_number': 'SU-RSC'},
                {'tail_number': 'SU-RSD'}
            ]
            db().table('aircraft').insert(defaults).execute()
        except Exception:
            # If this also fails, return a clear error requiring manual SQL run
            return render_template_string(LOGIN_TEMPLATE, mode='login', 
                error=f'Database Error: Please run the SQL migration in Supabase to add current_fc and current_fh.', success=None, year=datetime.date.today().year)
        
        # Reload aircraft
        try:
            all_aircraft = db().table('aircraft').select('*').execute().data or []
        except Exception:
            all_aircraft = []

    if not all_aircraft:
        return render_template_string(LOGIN_TEMPLATE, mode='login', 
            error=f'Critical Error: Could not load or seed aircraft. Please check Supabase table schema.', success=None, year=datetime.date.today().year)

    aircraft = next((a for a in all_aircraft if a['tail_number']==tail), all_aircraft[0])
    
    # Safely handle missing columns dynamically when forecasting
    aircraft['current_fh'] = aircraft.get('current_fh', 0.0)
    aircraft['current_fc'] = aircraft.get('current_fc', 0)
    aircraft['util_fh_rate'] = aircraft.get('util_fh_rate', 8.0)
    aircraft['util_fc_rate'] = aircraft.get('util_fc_rate', 4.0)

    forecasts = forecast_tasks(aircraft)
    user_email = current_user_email()
    supa_url = os.getenv('SUPABASE_URL','')
    supa_key = os.getenv('SUPABASE_KEY','')
    overdue = sum(1 for f in forecasts if f['status'] == 'Overdue')
    warning = sum(1 for f in forecasts if f['status'] == 'Warning')
    return render_template_string(MAIN_TEMPLATE,
        aircraft=aircraft, all_aircraft=all_aircraft, forecasts=forecasts,
        msg=msg, user_email=user_email, supa_url=supa_url, supa_key=supa_key,
        overdue=overdue, warning=warning)

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

            # Native Heuristic Parser (No Gemini AI)
            for sheet in sheet_names:
                try:
                    if isinstance(xls, _CsvFakeExcel):
                        df = xls.parse()
                    else:
                        df = pd.read_excel(file_path, sheet_name=sheet, header=None, engine=read_engine)
                except Exception as se:
                    error_msgs.append(f"Error reading sheet {sheet}: {str(se)}")
                    continue
                if df.empty or len(df) < 2: continue

                # 1. Heuristic Header Search
                header_idx = 0
                col_map = {}
                found_task = False
                
                # Scan first 20 rows for header
                for r in range(min(20, len(df))):
                    row_vals = [str(x).upper() for x in df.iloc[r] if pd.notna(x)]
                    # Keywords for Task ID
                    if any(k in v for v in row_vals for k in ["TASK NO", "TASK ID", "M.P. REF", "MPD NO", "TASK NUMBER"]):
                        header_idx = r
                        found_task = True
                        # Map columns
                        for i, val in enumerate(df.iloc[r]):
                            v = str(val).upper()
                            if any(k in v for k in ["TASK NO", "TASK ID", "M.P. REF", "MPD NO", "TASK NUMBER", "TCM TASK"]): col_map['task_id'] = i
                            elif "DESC" in v or "NOMENCLATURE" in v: col_map['description'] = i
                            elif ("FH" in v or "HOURS" in v) and "INTERVAL" in v: col_map['interval_fh'] = i
                            elif ("FC" in v or "CYCLES" in v) and "INTERVAL" in v: col_map['interval_fc'] = i
                            elif ("DY" in v or "DAYS" in v) and "INTERVAL" in v: col_map['interval_dy'] = i
                            elif "LAST DONE" in v and i not in col_map.values():
                                col_map['last_done_date'] = i
                                col_map['last_done_fh'] = i + 1
                                col_map['last_done_fc'] = i + 2
                            elif "DATE" in v and "DONE" in v: col_map['last_done_date'] = i
                            elif "FH" in v and "DONE" in v: col_map['last_done_fh'] = i
                            elif "FC" in v and "DONE" in v: col_map['last_done_fc'] = i
                            elif "ZONE" in v: col_map['zone'] = i
                            elif "ACCESS" in v: col_map['access'] = i
                        break

                if not found_task:
                    error_msgs.append(f"Sheet '{sheet}' skipped: Could not find header row with 'Task ID' keywords.")
                    continue

                # 2. Extract Aircraft Status (optional) from top rows
                ai_status = {}
                for r in range(min(header_idx, len(df))):
                    for i, val in enumerate(df.iloc[r]):
                        v = str(val).upper()
                        if "TOTAL" in v and ("FH" in v or "HOURS" in v):
                            try:
                                # Look for number in next cell
                                next_val = df.iloc[r, i
