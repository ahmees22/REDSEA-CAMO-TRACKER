import os
import datetime
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify

app = Flask(__name__)
app.config['SECRET_KEY'] = 'camo-tracker-secret-key'

# بيانات تجريبية (بدون Supabase)
aircraft_data = [
    {'tail_number': 'SU-RSA', 'current_fh': 1250.5, 'current_fc': 850, 'util_fh_rate': 8, 'util_fc_rate': 4},
    {'tail_number': 'SU-RSB', 'current_fh': 980.0, 'current_fc': 620, 'util_fh_rate': 8, 'util_fc_rate': 4},
    {'tail_number': 'SU-RSC', 'current_fh': 2100.0, 'current_fc': 1450, 'util_fh_rate': 8, 'util_fc_rate': 4},
    {'tail_number': 'SU-RSD', 'current_fh': 340.0, 'current_fc': 210, 'util_fh_rate': 8, 'util_fc_rate': 4},
]

tasks_data = {
    'SU-RSA': [
        {'task_id': '78-11-01', 'description': 'LANDING GEAR INSPECTION', 'interval_fh': 500, 'last_done_fh': 1050, 'due_date': '2026-04-15'},
        {'task_id': '78-11-02', 'description': 'ENGINE OIL CHANGE', 'interval_fh': 200, 'last_done_fh': 1150, 'due_date': '2026-04-20'},
        {'task_id': '78-11-03', 'description': 'HYDRAULIC FILTER REPLACEMENT', 'interval_fh': 300, 'last_done_fh': 1100, 'due_date': '2026-05-01'},
    ],
    'SU-RSB': [
        {'task_id': '78-11-01', 'description': 'LANDING GEAR INSPECTION', 'interval_fh': 500, 'last_done_fh': 800, 'due_date': '2026-04-10'},
        {'task_id': '78-11-02', 'description': 'ENGINE OIL CHANGE', 'interval_fh': 200, 'last_done_fh': 900, 'due_date': '2026-04-25'},
    ],
    'SU-RSC': [
        {'task_id': '78-11-01', 'description': 'LANDING GEAR INSPECTION', 'interval_fh': 500, 'last_done_fh': 1900, 'due_date': '2026-04-05'},
        {'task_id': '78-11-03', 'description': 'HYDRAULIC FILTER REPLACEMENT', 'interval_fh': 300, 'last_done_fh': 1950, 'due_date': '2026-04-18'},
    ],
    'SU-RSD': [
        {'task_id': '78-11-02', 'description': 'ENGINE OIL CHANGE', 'interval_fh': 200, 'last_done_fh': 250, 'due_date': '2026-05-10'},
    ],
}

# قالب الصفحة الرئيسية المبسط
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
                <a href="/logout" class="bg-red-800 px-4 py-2 rounded hover:bg-red-900">Logout</a>
            </div>
        </div>
    </nav>

    <div class="container mx-auto px-4 py-8">
        {% if msg %}
        <div class="bg-green-100 border-l-4 border-green-500 text-green-700 p-4 mb-6">
            {{ msg }}
        </div>
        {% endif %}

        <!-- Aircraft Stats -->
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
                <div class="text-gray-500">Due in 5 days</div>
            </div>
            <div class="bg-white rounded-lg shadow p-6 text-center">
                <div class="text-3xl font-bold text-blue-600">{{ aircraft.current_fh }}</div>
                <div class="text-gray-500">Total Flight Hours</div>
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
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-200">
                    {% for task in forecasts %}
                    <tr class="hover:bg-gray-50">
                        <td class="px-6 py-4 font-mono font-bold">{{ task.task_id }}</td>
                        <td class="px-6 py-4">{{ task.description }}</td>
                        <td class="px-6 py-4">{{ task.due_date }}</td>
                        <td class="px-6 py-4">
                            <span class="px-2 py-1 rounded text-xs font-bold 
                                {% if task.status == 'Overdue' %}bg-red-100 text-red-700
                                {% elif task.status == 'Warning' %}bg-yellow-100 text-yellow-700
                                {% else %}bg-green-100 text-green-700{% endif %}">
                                {{ task.status }}
                            </span>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <!-- Update Form -->
        <div class="mt-8 bg-white rounded-lg shadow p-6">
            <h2 class="text-xl font-bold mb-4">Update Aircraft Utilization</h2>
            <form action="/update" method="POST">
                <input type="hidden" name="tail" value="{{ aircraft.tail_number }}">
                <div class="grid grid-cols-2 gap-4 mb-4">
                    <div>
                        <label class="block text-sm font-medium mb-1">Current Flight Hours</label>
                        <input type="number" step="0.1" name="current_fh" value="{{ aircraft.current_fh }}" class="w-full border rounded px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">Current Flight Cycles</label>
                        <input type="number" name="current_fc" value="{{ aircraft.current_fc }}" class="w-full border rounded px-3 py-2">
                    </div>
                </div>
                <button type="submit" class="bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700">
                    Update Status
                </button>
            </form>
        </div>
    </div>
</body>
</html>
"""

# قالب تسجيل الدخول
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Camo-Tracker Login</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-900 flex items-center justify-center min-h-screen">
    <div class="bg-white p-8 rounded-lg shadow-lg w-96">
        <div class="text-center mb-6">
            <i class="fas fa-plane-departure text-4xl text-red-600"></i>
            <h1 class="text-2xl font-bold text-gray-800 mt-2">Camo-Tracker</h1>
            <p class="text-gray-500">RED SEA Airlines</p>
        </div>
        <form method="POST">
            <input type="email" name="email" placeholder="Email" class="w-full p-2 border rounded mb-3" required>
            <input type="password" name="password" placeholder="Password" class="w-full p-2 border rounded mb-4" required>
            <button type="submit" class="w-full bg-red-600 text-white p-2 rounded hover:bg-red-700">
                Login
            </button>
        </form>
    </div>
</body>
</html>
"""

# Routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        session['user'] = {'email': request.form.get('email'), 'id': 'user123'}
        return redirect(url_for('index'))
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    msg = request.args.get('msg', '')
    tail = request.args.get('tail', 'SU-RSA')
    
    # Get aircraft data
    aircraft = next((a for a in aircraft_data if a['tail_number'] == tail), aircraft_data[0])
    
    # Get tasks for this aircraft
    tasks = tasks_data.get(tail, [])
    
    # Calculate forecasts
    today = datetime.date.today()
    forecasts = []
    overdue = 0
    warning = 0
    
    for task in tasks:
        due_date = datetime.datetime.strptime(task['due_date'], '%Y-%m-%d').date()
        days_left = (due_date - today).days
        
        if days_left < 0:
            status = 'Overdue'
            overdue += 1
        elif days_left <= 5:
            status = 'Warning'
            warning += 1
        else:
            status = 'Normal'
        
        forecasts.append({
            'task_id': task['task_id'],
            'description': task['description'],
            'due_date': task['due_date'],
            'status': status
        })
    
    return render_template_string(MAIN_TEMPLATE,
        aircraft=aircraft,
        all_aircraft=aircraft_data,
        forecasts=forecasts,
        msg=msg,
        overdue=overdue,
        warning=warning)

@app.route('/update', methods=['POST'])
def update():
    tail = request.form.get('tail')
    current_fh = float(request.form.get('current_fh', 0))
    current_fc = int(float(request.form.get('current_fc', 0)))
    
    # Update aircraft data
    for ac in aircraft_data:
        if ac['tail_number'] == tail:
            ac['current_fh'] = current_fh
            ac['current_fc'] = current_fc
            break
    
    return redirect(url_for('index', tail=tail, msg=f'Aircraft {tail} updated successfully!'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
