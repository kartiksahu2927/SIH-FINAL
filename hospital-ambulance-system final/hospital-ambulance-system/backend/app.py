"""
app.py — Flask application with Socket.IO Real-Time Engine & 2-Device Demo
========================================================================
Serves:
  - DEVICE 1 (Ambulance Dashboard): http://<HOST>:5000/ or http://<HOST>:5000/ambulance
  - DEVICE 2 (JS Hospital Dashboard): http://<HOST>:5000/hospital
Provides:
  - WebSocket/Socket.IO Real-Time Emergency Dispatch
  - REST API routes for hospital eligibility and fleet telemetry
"""

import os
import sys
from flask import Flask, send_from_directory, jsonify, make_response
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

# Ensure safe UTF-8 output on Windows console
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ---- Load environment variables ----
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, '..', '.env'))

# ---- Locate the frontend folder ----
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', 'frontend'))

# ---- Create the Flask app & SocketIO ----
app = Flask(__name__, static_folder=None)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'hospital-ambulance-emergency-2026-key')

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
    ping_timeout=10,
    ping_interval=5,
    logger=False,
    engineio_logger=False
)

# ---- Register API blueprint & Wire Socket Emitter ----
from routes import api_bp, set_socketio_emitter, create_emergency_request, respond_emergency_request, emergency_requests, active_emergency_request_id
app.register_blueprint(api_bp)

# Connect routes to Socket.IO broadcast emitter
set_socketio_emitter(lambda event, data: socketio.emit(event, data))


# ---- Add CORS headers for all responses ----
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Goog-Api-Key, X-Goog-FieldMask'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


# ============================================================
# SOCKET.IO REAL-TIME EVENT HANDLERS
# ============================================================
@socketio.on('connect')
def handle_connect():
    print('[Socket.IO] Client connected to real-time emergency stream')
    # If there is an active pending/accepted request, send sync payload
    from routes import emergency_requests, active_emergency_request_id
    if active_emergency_request_id and active_emergency_request_id in emergency_requests:
        emit('sync_active_emergency', emergency_requests[active_emergency_request_id])


@socketio.on('disconnect')
def handle_disconnect():
    print('[Socket.IO] Client disconnected')


@socketio.on('emergency_request')
def handle_emergency_request_event(data):
    """Ambulance user pressed 'Emergency Patient Onboard' via Socket.IO"""
    print('[Socket.IO] 🚨 Incoming Emergency Request from Ambulance:', data)
    # Broadcast to JS Hospital (Device 2)
    socketio.emit('new_emergency_request', data)


@socketio.on('hospital_response')
def handle_hospital_response_event(data):
    """JS Hospital clicked ACCEPT or REJECT via Socket.IO"""
    status = str(data.get('status', 'accepted')).upper()
    print(f'[Socket.IO] 🏥 Hospital Decision: {status}', data)
    event_name = 'emergency_request_accepted' if status in ('ACCEPT', 'ACCEPTED') else 'emergency_request_rejected'
    socketio.emit(event_name, data)
    socketio.emit('emergency_status_update', data)


@socketio.on('ambulance_location')
def handle_ambulance_location_event(data):
    """Ambulance live GPS stream update"""
    socketio.emit('ambulance_position_changed', data)


# ============================================================
# SERVE FRONTEND PAGES & ROUTES
# ============================================================
@app.route('/')
@app.route('/ambulance')
@app.route('/ambulance/')
def serve_ambulance_dashboard():
    """DEVICE 1: Ambulance Dashboard with interactive map and emergency dispatch."""
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/hospital')
@app.route('/hospital/')
def serve_hospital_dashboard():
    """DEVICE 2: JS Hospital Emergency Admission Dashboard."""
    return send_from_directory(FRONTEND_DIR, 'hospital.html')


@app.route('/<path:filename>')
def serve_frontend(filename):
    """Serve static files (css, js, assets) from frontend directory."""
    clean_filename = filename
    for prefix in ('hospital/', 'ambulance/'):
        if clean_filename.startswith(prefix):
            clean_filename = clean_filename[len(prefix):]

    file_path = os.path.join(FRONTEND_DIR, clean_filename)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return send_from_directory(FRONTEND_DIR, clean_filename)

    file_path_raw = os.path.join(FRONTEND_DIR, filename)
    if os.path.exists(file_path_raw) and os.path.isfile(file_path_raw):
        return send_from_directory(FRONTEND_DIR, filename)

    return send_from_directory(FRONTEND_DIR, 'index.html')


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print("=" * 65)
    print("  🏥 Hospital & Ambulance Real-Time Coordination Platform")
    print(f"  [+] Device 1 (Ambulance Dashboard): http://localhost:{port}/ambulance")
    print(f"  [+] Device 2 (JS Hospital Reception): http://localhost:{port}/hospital")
    print(f"  [+] API Health:                      http://localhost:{port}/api/health")
    print(f"  [+] Real-Time WebSockets:            ONLINE (Flask-SocketIO)")
    print("=" * 65)
    socketio.run(app, debug=True, port=port, host='0.0.0.0', allow_unsafe_werkzeug=True)

