import os
import datetime
import tempfile
import re
from math import floor
from functools import wraps
import pandas as pd
from flask import Flask, render_template_string, request, jsonify, send_file, redirect, url_for, session
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'camo-tracker-secret')
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Supabase clients
try:
    from supabase import create_client
    SUPA_URL = os.getenv('SUPABASE_URL', '')
    SUPA_KEY = os.getenv('SUPABASE_KEY', '')
    SUPA_SVC = os.getenv('SUPABASE_SERVICE_KEY', SUPA_KEY)
    supa_anon = create_client(SUPA_URL, SUPA_KEY)
    supa_admin = create_client(SUPA_URL, SUPA_SVC)
    SUPABASE_READY = True
    print("Supabase connected")
except Exception as e:
    print(f"Supabase not available: {e}")
    SUPABASE_READY = False
    supa_anon = supa_admin = None

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            session['user'] = {'email': 'admin@redsea.com', 'id': 'system'}
        return f(*args, **kwargs)
    return decorated

def current_user_email():
    return session.get('user', {}).get('email', 'system')

def db():
    if not SUPABASE_READY or supa_admin is None:
        class EmptyDB:
            def table(self, *args, **kwargs): return self
            def select(self, *args, **kwargs): return self
            def insert(self, *args, **kwargs): return self
            def update(self, *args, **kwargs): return self
            def delete(self, *args, **kwargs): return self
            def eq(self, *args, **kwargs): return self
            def order(self, *args, **kwargs): return self
            def limit(self, *args, **kwargs): return self
            def maybe_single(self, *args, **kwargs): return self
            def execute(self, *args, **kwargs):
                class Result:
                    data = []
                    error = None
                return Result()
        return EmptyDB()
    return supa_admin

def forecast_tasks(aircraft):
    today = datetime.date.today()
    tasks_res = db().table('engine_tasks').select('*').eq('aircraft_id', aircraft['id']).execute()
    tasks = tasks_res.data or []
    forecasts = []

    for t in tasks:
        estimated = []
        reasons = []
        fh_rate = aircraft.get('util_fh_rate', 8) or 8
        fc_rate = aircraft.get('util_fc_rate', 4) or 4
        curr_fh = aircraft.get('current_fh', 0) or 0
        curr_fc = aircraft.get('current_fc', 0) or 0

        if t.get('interval_fh') and t['interval_fh'] > 0:
            due_fh = (t.get('last_done_fh') or 0) + t['interval_fh']
            rem_fh = max(due_fh - curr_fh, 0)
            d_fh = today + datetime.timedelta(days=floor(rem_fh / fh_rate) if fh_rate > 0 else 0)
            estimated.append(('FH', d_fh, rem_fh))
            reasons.append(f"Due at {due_fh} FH")

        if t.get('interval_fc') and t['interval_fc'] > 0:
            due_fc = (t.get('last_done_fc') or 0) + t['interval_fc']
            rem_fc = max(due_fc - curr_fc, 0)
            d_fc = today + datetime.timedelta(days=floor(rem_fc / fc_rate) if fc_rate > 0 else 0)
            estimated.append(('FC', d_fc, rem_fc))
            reasons.append(f"Due at {due_fc} FC")

        if t.get('interval_days') and t['interval_days'] > 0:
            last_dt = t.get('last_done_date')
            if last_dt:
                try:
                    last_d = datetime.datetime.fromisoformat(str(last_dt).replace('Z', '+00:00')).date()
                except:
                    last_d = today
            else:
                last_d = today
            due_d = last_d + datetime.timedelta(days=t['interval_days'])
            estimated.append(('DY', due_d, 0))

        if not estimated:
            continue
        
        estimated.sort(key=lambda x: x[1])
        due_date = estimated[0][1]
        days_left = (due_date - today).days
        status = 'Overdue' if days_left < 0 else 'Warning' if days_left <= 5 else 'Normal'

        pdf_res = db().table('task_card_pdfs').select('id').eq('task_id_ref', t['task_id']).maybe_single().execute()
        pdf_id = (pdf_res.data or {}).get('id')

        forecasts.append({
            'task_id': t['task_id'],
            'description': t.get('description', ''),
            'task_type': t.get('task_type', ''),
            'due_date': due_date.strftime('%Y-%m-%d'),
            'status': status,
            'pdf_id': pdf_id,
            'zone': t.get('zone'),
            'access': t.get('access'),
        })

    return forecasts

# Templates
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Camo-Tracker Login</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-900 flex items-center justify-center min-h-screen">
    <div class="bg-white p-8 rounded-lg shadow-lg w-96">
        <h1 class="text-2xl font-bold text-red-600 mb-4">Camo-Tracker</h1>
        <p class="text-gray-600 mb-4">RED SEA Airlines CAMO System</p>
        <form method="POST">
            <input type="email" name="email" placeholder="Email" class="w-full p-2 border rounded mb-2" required>
            <input type="password" name="password" placeholder="Password" class="w-full p-2 border rounded mb-4" required>
            <button type="submit" class="w-full bg-red-600 text-white p-2 rounded">Login</button>
        </form>
    </div>
</body>
</html>
"""

MAIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Camo-Tracker - {{ aircraft.tail_number }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
</head>
<body class="bg-gray-100">
    <nav class="bg-red-700 text-white px-6 py-4 shadow-lg">
        <div class="flex justify-between items-center">
            <div>
                <h1 class="text-2xl font-bold">Camo-Tracker</h1>
                <p class="text-sm">RED SEA Airlines CAMO System</p>
            </div>
            <div class="flex items-center gap-4">
                <select onchange="location.href='/?tail='+this.value" class="bg-red-800 text-white px-4 py-2 rounded">
                    {% for ac in all_aircraft %}
                    <option value="{{ ac.tail_number }}" {% if ac.tail_number == aircraft.tail_number %}selected{% endif %}>
                        {{ ac.tail_number }}
                    </option>
                    {% endfor %}
                </select>
                <span>{{ user_email }}</span>
                <a href="/logout" class="bg-red-800 px-4 py-2 rounded hover:bg-red-900">Logout</a>
            </div>
        </div>
    </nav>

    <div class="container mx-auto px-4 py-8">
        {% if msg %}
        <div class="bg-blue-100 border-l-4 border-blue-500 text-blue-700 p-4 mb-6">
            {{ msg }}
        </div>
        {% endif %}

        <!-- Stats -->
        <div class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
            <div class="bg-white rounded-lg shadow p-6 text-center">
                <div class="text-3xl font-bold text-gray-800">{{ forecasts|length }}</div>
                <div class="text-gray-500">Total Tasks</div>
            </div>
            <div class="bg-white rounded-lg shadow p-6 text-center">
                <div class="text-3xl font-bold text-red-600">{{ overdue }}</div>
                <div class="text-gray-500">Overdue</div>
            </div>
            <div class="bg-white rounded-lg shadow p-6 text-center">
                <div class="text-3xl font-bold text-yellow-600">{{ warning }}</div>
                <div class="text-gray-500">Warning</div>
            </div>
            <div class="bg-white rounded-lg shadow p-6 text-center">
                <div class="text-3xl font-bold text-blue-600">{{ aircraft.current_fh|round(1) }}</div>
                <div class="text-gray-500">Total FH</div>
            </div>
        </div>

        <!-- Tasks Table -->
        <div class="bg-white rounded-lg shadow overflow-hidden">
            <table class="min-w-full">
                <thead class="bg-gray-50">
                    <tr>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Task ID</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Description</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Due Date</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Action</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-200">
                    {% for task in forecasts %}
                    <tr class="hover:bg-gray-50">
                        <td class="px-6 py-4 font-mono">{{ task.task_id }}</td>
                        <td class="px-6 py-4">{{ task.description[:60] }}</td>
                        <td class="px-6 py-4">{{ task.due_date }}</td>
                        <td class="px-6 py-4">
                            <span class="px-2 py-1 rounded text-xs font-bold 
                                {% if task.status == 'Overdue' %}bg-red-100 text-red-700
                                {% elif task.status == 'Warning' %}bg-yellow-100 text-yellow-700
                                {% else %}bg-green-100 text-green-700{% endif %}">
                                {{ task.status }}
                            </span>
                        </td>
                        <td class="px-6 py-4">
                            {% if task.pdf_id %}
                            <a href="/download_pdf/{{ task.pdf_id }}" class="text-red-600 hover:text-red-800" target="_blank">
                                <i class="fas fa-file-pdf"></i> View
                            </a>
                            {% else %}
                            <span class="text-gray-400">No PDF</span>
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <!-- Upload Section -->
        <div class="mt-8 bg-white rounded-lg shadow p-6">
            <h2 class="text-xl font-bold mb-4">Upload Maintenance Data</h2>
            <form action="/upload_excel" method="POST" enctype="multipart/form-data">
                <input type="hidden" name="tail" value="{{ aircraft.tail_number }}">
                <input type="file" name="files" multiple accept=".xls,.xlsx,.xlsb,.csv" class="mb-4">
                <button type="submit" class="bg-green-600 text-white px-6 py-2 rounded hover:bg-green-700">
                    Upload & Process
                </button>
            </form>
        </div>

        <!-- Update Status Section -->
        <div class="mt-8 bg-white rounded-lg shadow p-6">
            <h2 class="text-xl font-bold mb-4">Update Aircraft Status</h2>
            <form action="/update_util_rates" method="POST">
                <input type="hidden" name="tail" value="{{ aircraft.tail_number }}">
                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-medium mb-1">Current FH</label>
                        <input type="number" step="0.1" name="current_fh" value="{{ aircraft.current_fh }}" class="w-full border rounded px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">Current FC</label>
                        <input type="number" name="current_fc" value="{{ aircraft.current_fc }}" class="w-full border rounded px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">FH Rate (per day)</label>
                        <input type="number" step="0.1" name="fh_rate" value="{{ aircraft.util_fh_rate }}" class="w-full border rounded px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">FC Rate (per day)</label>
                        <input type="number" step="0.1" name="fc_rate" value="{{ aircraft.util_fc_rate }}" class="w-full border rounded px-3 py-2">
                    </div>
                </div>
                <button type="submit" class="mt-4 bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700">
                    Update Status
                </button>
            </form>
        </div>
    </div>
</body>
</html>
"""

# Routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    session['user'] = {'email': 'admin@redsea.com', 'id': 'system'}
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    msg = request.args.get('msg', '')
    tail = request.args.get('tail', '')
    
    aircraft_res = db().table('aircraft').select('*').execute()
    all_aircraft = aircraft_res.data or []
    
    if not all_aircraft:
        try:
            defaults = [
                {'tail_number': 'SU-RSA', 'current_fh': 0, 'current_fc': 0},
                {'tail_number': 'SU-RSB', 'current_fh': 0, 'current_fc': 0},
                {'tail_number': 'SU-RSC', 'current_fh': 0, 'current_fc': 0},
                {'tail_number': 'SU-RSD', 'current_fh': 0, 'current_fc': 0}
            ]
            db().table('aircraft').insert(defaults).execute()
            all_aircraft = db().table('aircraft').select('*').execute().data or []
        except Exception as e:
            return f"Database error: {e}", 500
    
    if not all_aircraft:
        return "No aircraft found", 500
    
    aircraft = next((a for a in all_aircraft if a['tail_number'] == tail), all_aircraft[0])
    
    aircraft['current_fh'] = aircraft.get('current_fh', 0.0)
    aircraft['current_fc'] = aircraft.get('current_fc', 0)
    aircraft['util_fh_rate'] = aircraft.get('util_fh_rate', 8.0)
    aircraft['util_fc_rate'] = aircraft.get('util_fc_rate', 4.0)
    
    forecasts = forecast_tasks(aircraft)
    overdue = sum(1 for f in forecasts if f['status'] == 'Overdue')
    warning = sum(1 for f in forecasts if f['status'] == 'Warning')
    
    return render_template_string(MAIN_TEMPLATE,
        aircraft=aircraft,
        all_aircraft=all_aircraft,
        forecasts=forecasts,
        msg=msg,
        user_email=current_user_email(),
        overdue=overdue,
        warning=warning)

@app.route('/update_util_rates', methods=['POST'])
@login_required
def update_util_rates():
    tail = request.form.get('tail', '')
    fh_r = float(request.form.get('fh_rate', 8))
    fc_r = float(request.form.get('fc_rate', 4))
    cur_fh = float(request.form.get('current_fh', 0))
    cur_fc = float(request.form.get('current_fc', 0))
    
    db().table('aircraft').update({
        'util_fh_rate': fh_r,
        'util_fc_rate': fc_r,
        'current_fh': cur_fh,
        'current_fc': cur_fc,
        'last_updated_by': current_user_email()
    }).eq('tail_number', tail).execute()
    
    return redirect(url_for('index', tail=tail, msg='Aircraft status updated'))

@app.route('/upload_excel', methods=['POST'])
@login_required
def upload_excel():
    files = request.files.getlist('files')
    tail = request.form.get('tail', '')
    processed = []
    
    for file in files:
        if not file or not file.filename:
            continue
        
        try:
            suffix = os.path.splitext(file.filename)[1].lower()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            file.save(tmp.name)
            tmp.close()
            
            # Find aircraft
            ac_res = db().table('aircraft').select('*').execute().data or []
            target = next((a for a in ac_res if a['tail_number'] == tail), None)
            
            if not target:
                continue
            
            # Read Excel
            try:
                df = pd.read_excel(tmp.name, header=None)
            except:
                df = pd.read_csv(tmp.name, header=None)
            
            # Simple parsing - look for task IDs
            batch = []
            for idx, row in df.iterrows():
                first_cell = str(row.iloc[0]) if len(row) > 0 else ""
                # Look for task ID pattern (numbers and dashes)
                if re.match(r'^[\d\-]{5,}$', first_cell.strip()):
                    task_id = first_cell.strip()
                    description = str(row.iloc[1])[:200] if len(row) > 1 else ""
                    
                    batch.append({
                        'aircraft_id': target['id'],
                        'task_id': task_id,
                        'description': description,
                        'task_type': 'MPD',
                        'last_updated_by': current_user_email()
                    })
                    
                    if len(batch) >= 100:
                        db().table('engine_tasks').insert(batch).execute()
                        batch = []
            
            if batch:
                db().table('engine_tasks').insert(batch).execute()
            
            processed.append(file.filename)
            os.unlink(tmp.name)
            
        except Exception as e:
            print(f"Error processing {file.filename}: {e}")
    
    msg = f"Processed: {', '.join(processed)}" if processed else "No files processed"
    return redirect(url_for('index', tail=tail, msg=msg))

@app.route('/download_pdf/<int:pdf_id>')
@login_required
def download_pdf(pdf_id):
    row = db().table('task_card_pdfs').select('*').eq('id', pdf_id).maybe_single().execute().data
    if not row:
        return "PDF not found", 404
    return send_file(row['file_path'], as_attachment=False, mimetype='application/pdf')

@app.route('/api/calendar_data')
@login_required
def calendar_data():
    return jsonify([])

@app.route('/api/search')
@login_required
def search():
    return jsonify([])

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
