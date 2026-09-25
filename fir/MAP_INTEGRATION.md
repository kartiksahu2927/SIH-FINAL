# Read-only map integration

MediRoute does not copy, import, rewrite, or modify `hospital-ambulance-system final`. The supplied archive baseline SHA-256 is:

`39EB30E71F8A7A24FC270D3D21F9B75707ED406CCF286F3665D3458FFA73C3B6`

The external adapter in `mediroute/services.py` communicates only through the protected Flask module's public interfaces:

- `POST /api/emergency/request` for hospital selection and dispatch.
- `POST /api/emergency/ambulance-location` for live driver location relay.
- Existing Socket.IO broadcasts remain owned by the map module for its hospital/ambulance screens.

Set `MAP_BRIDGE_URL` to the independently run protected map service (normally `http://127.0.0.1:5000`). If it is unavailable, MediRoute persists the emergency request and tells the user that map relay was unavailable; it never fabricates a successful map dispatch.

The audit found that the map module is a Flask + Flask-SocketIO application serving `/ambulance` and `/hospital`, with Flask APIs for live hospital telemetry, ambulance fleet telemetry, emergency request/response, and route calculations. Its UI uses a high-contrast emergency dashboard with restrained blue/green/red status cues; MediRoute extends this with a calm medical white/teal interface.
