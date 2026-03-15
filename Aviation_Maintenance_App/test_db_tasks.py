from app import app, EngineTask, Aircraft, forecast_tasks
with app.app_context():
    aircraft = Aircraft.query.filter_by(tail_number='SU-RSA').first()
    if aircraft:
        print(f"--- Data for {aircraft.tail_number} ---")
        tasks = EngineTask.query.filter_by(aircraft_id=aircraft.id).limit(3).all()
        for t in tasks:
            print(f"Task ID: {t.task_id} | Package: {t.task_type}")
            print(f"Intervals: FH: {t.interval_fh}, FC: {t.interval_fc}, Days: {t.interval_days}")
            print(f"Last Done: FH: {t.last_done_fh}, FC: {t.last_done_fc}, Date: {t.last_done_date}")
            print("-" * 30)
            
        print("\nForecasts:")
        forecasts = forecast_tasks(aircraft)
        for f in forecasts[:3]:
            print(f"Task: {f['task_id']} | Date: {f['due_date']} | Status: {f['status']}")
            print(f"Reason: {f['reasoning']}")
            print("-" * 30)
