# Read-only map integration

## Authoritative hospital coordination extension

The map workflow keeps the existing renderer and adds a second reception URL for
KS Hospital. Both reception URLs use the same implementation and Flask REST/
Socket.IO endpoints; see [EMERGENCY_COORDINATION.md](EMERGENCY_COORDINATION.md).

The protected map module remains a separate deployment boundary. Its existing JS
Hospital page is preserved; the KS page is the same page/controller selected by
the `/ks-hospital` route.

The external adapter in `mediroute/services.py` communicates only through the protected Flask module's public interfaces:

- `POST /api/emergency/request` for simultaneous JS001/KS001 dispatch.
- `POST /api/emergency/respond` for authenticated-by-dashboard hospital response
  status, enforced per hospital and per 60-second response deadline.
- `POST /api/emergency/ambulance-location` for live driver location relay.
- Existing Socket.IO broadcasts remain owned by the map module for its hospital/ambulance screens.

Set `MAP_BRIDGE_URL` to the independently run protected map service (normally `http://127.0.0.1:5000`). If it is unavailable, MediRoute persists the emergency request and tells the user that map relay was unavailable; it never fabricates a successful map dispatch.

The map module is a Flask + Flask-SocketIO application serving `/ambulance`,
`/hospital`, and `/ks-hospital`, with APIs for live hospital telemetry,
ambulance fleet telemetry, simultaneous emergency request/response, and route
calculations. Its UI uses the existing high-contrast emergency dashboard.
