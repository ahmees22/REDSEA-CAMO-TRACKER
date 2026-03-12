from app import app, EngineTask, Aircraft, forecast_tasks

with app.app_context():
    aircraft = Aircraft.query.filter_by(tail_number='SU-RSA').first()
    if aircraft:
        print("====== Verification Data for SU-RSA ======\n")
        
        # Find tasks that actually have intervals to show the math
        tasks = EngineTask.query.filter(
            EngineTask.aircraft_id == aircraft.id,
            (EngineTask.interval_fh != None) | (EngineTask.interval_fc != None) | (EngineTask.interval_days != None)
        ).limit(3).all()
        
        if not tasks:
            print("No tasks with defined intervals were found. The intervals map from MAIN sheet might not have matched the Task IDs in the 1C/OOP sheets, or the upload wasn't actually completed.")
        else:
            print("--- 1. Raw Task Data (From Database) ---")
            for t in tasks:
                print(f"Task ID: {t.task_id}")
                print(f"  Package/Type: {t.task_type}")
                print(f"  Description: {t.description[:80]}...")
                print(f"  Parsed Intervals: FH: {t.interval_fh}, FC: {t.interval_fc}, Days: {t.interval_days}")
                print(f"  Last Done Status: FH: {t.last_done_fh}, FC: {t.last_done_fc}, Date: {t.last_done_date.strftime('%Y-%m-%d') if t.last_done_date else 'None'}\n")
            
            print("\n--- 2. Live Forecasting Engine Output (Algorithm Output) ---")
            forecasts = forecast_tasks(aircraft)
            # Find the forecasts for our specific subset of tasks
            task_ids = [t.task_id for t in tasks]
            rel_forecasts = [f for f in forecasts if f['task_id'] in task_ids]
            
            for f in rel_forecasts:
                print(f">> Forecast for Task {f['task_id']}:")
                print(f"   Calculated Due Date: {f['due_date']}")
                print(f"   Status Alert: {f['status']}")
                print(f"   Mathematical Reasoning: {f['reasoning']}\n")
    else:
        print("Aircraft not found.")
