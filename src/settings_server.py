import json
import logging
import os
from aiohttp import web
from valveSettings import valveSettings

logger = logging.getLogger("SETTINGS")
logging.basicConfig(level=logging.INFO)

async def index(request):
    """Serve the settings.html page."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(base_dir, '../web/settings.html')
    return web.FileResponse(file_path)

async def get_settings(request):
    """Return the current valve settings."""
    settings_data = {
        "controllerMac": valveSettings.controllerMac,
        "valveUnits": valveSettings.valveUnits,
        "schedule": valveSettings.schedule
    }
    return web.json_response(settings_data)

async def post_settings(request):
    """Update valve settings from JSON payload."""
    try:
        new_settings = await request.json()

        # Validate that the necessary keys exist
        if 'controllerMac' not in new_settings or 'valveUnits' not in new_settings or 'schedule' not in new_settings:
            return web.json_response({"error": "Missing required keys in payload."}, status=400)

        # Update in-memory settings
        valveSettings.controllerMac = new_settings['controllerMac']
        valveSettings.valveUnits = new_settings['valveUnits']
        valveSettings.schedule = new_settings['schedule']

        # Save to valveSettings.json
        base_dir = os.path.dirname(os.path.abspath(__file__))
        valve_settings_path = os.path.join(base_dir, 'valveSettings.json')
        schedule_settings_path = os.path.join(base_dir, 'scheduleSettings.json')

        valve_data = {"data": []}
        for mac in valveSettings.controllerMac:
            units = valveSettings.valveUnits.get(mac, [])
            valve_data["data"].append({
                "controllerMac": mac,
                "valveUnits": units
            })

        with open(valve_settings_path, 'w') as f:
            json.dump(valve_data, f, indent=2)

        # Save to scheduleSettings.json
        schedule_data = {"data": []}
        for vUnit, days in valveSettings.schedule.items():
            unit_data = {
                "valveUnit": vUnit,
                "schedule": []
            }
            # Iterate through the dictionary of days (0-6)
            for day, valves in days.items():
                day_data = {"day": int(day)}
                for i in range(len(valves)):
                    day_data[f"valve{i+1}"] = valves[i]
                unit_data["schedule"].append(day_data)
            schedule_data["data"].append(unit_data)

        with open(schedule_settings_path, 'w') as f:
            json.dump(schedule_data, f, indent=2)

        return web.json_response({"status": "Settings updated successfully."})
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON payload."}, status=400)
    except Exception as e:
        logger.error(f"Error saving settings: {e}")
        return web.json_response({"error": str(e)}, status=500)

def setup_routes(app):
    app.router.add_get('/', index)
    app.router.add_get('/api/settings', get_settings)
    app.router.add_post('/api/settings', post_settings)

async def init_app():
    app = web.Application()
    setup_routes(app)
    return app

def start_web_server(port=8080):
    app = init_app()
    logger.info(f"Settings web server starting on port {port}")
    web.run_app(app, port=port)

if __name__ == "__main__":
    start_web_server()
