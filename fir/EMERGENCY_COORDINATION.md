# Hospital acceptance and ambulance destination coordination

## Architecture and integration findings

The existing FastAPI application, SQLAlchemy database, hospital identities and
JWT roles remain authoritative. Notifications use the existing
`/api/ws/notifications/{user_id}` connection and `notifications` table. JS Hospital
keeps its `JS001` map identity, database ID, existing account and records. KS Hospital
has a distinct `KS001` identity and `ks-hospital@mediroute.demo` hospital account.

The integration audit found:

* The old create endpoint selected one hospital before acceptance and notified
  only that facility. There was no per-hospital response/deadline persistence.
* The map iframe used a hardcoded Render origin while the proxy used localhost.
  Map links/proxy now use `MAP_BRIDGE_URL`.
* The driver form mapped address keywords to hardcoded coordinates, and patient
  GPS errors silently substituted Mathura coordinates. Coordinated views now
  use browser GPS with permission/freshness checks, or explicitly labelled demo
  positions on demo emergencies only.
* The Flask prototype synthesizes nearby facilities, telemetry, and geometric
  routing fallbacks. It cannot identify a genuine road estimate in its API
  response. Coordination therefore uses the same OSRM provider directly, with
  no geometric/time fallback and no claim of live traffic.
* The separate service has **no versioned destination-update API**, and its
  hospital dashboard hardcodes JS001 and emits acceptance before persisting it.
  Those prototype decisions cannot be authoritative for this workflow.

No source file in `hospital-ambulance-system final` is modified. The old demo
remains available independently. Use the authenticated MediRoute dashboards for
this coordinated workflow; decisions on the legacy standalone hospital page do
not update this database.

## Persistence and deployment

Startup uses the existing `create_all` convention plus an additive, idempotent
compatibility migration in `mediroute/db.py`. Run one application startup to
apply schema changes before starting additional workers.

* `hospitals`: `demo_facility`, `capabilities_source`, `location_source`, and
  `availability_checked_at`.
* `emergency_requests`: `state_version`, an optimistic concurrency revision.
  Existing `details` JSON records pickup, dispatch origin, dispatch/deadline,
  care requirement, clinical lock, notified IDs, assignment version, current
  route, latest comparison, escalation, and lifecycle timeline.
* `locations`: `observed_at`, `source` (`GPS`, `DEMO`, or migrated `LEGACY`).
  Legacy coordinates do not count as a fresh GPS fix.
* `emergency_hospital_responses`: one row per emergency/hospital, enforced by
  a unique constraint; notification time, deadline, original geographic distance,
  eligibility snapshot, response, server response timestamp and authenticated user.
* `emergency_route_decisions`: previous/new destination, assignment version,
  same-position road estimates, reason and timestamp. Releasing an unavailable
  destination also creates a history row.

Existing clinical/patient relationships and IDs are preserved. Hospital seed
coordinates are only applied on initial creation, never moved with GPS or
silently overwritten on restart. Back up the database before deployment using
the project's normal database backup process.

All response, GPS, lifecycle and assignment writes claim the emergency's current
`state_version` using a conditional SQL update in a transaction. A competing
write returns 409 (reload/retry); route computations that lose their revision
are discarded. Network requests happen before the decision write transaction.
The same CAS works with SQLite and PostgreSQL; only SQLite is exercised by the
automated suite.

A server maintenance task sweeps stored deadlines every 0.5 seconds. The backend
also checks deadlines on reads/responses, so browser timers and reconnects are
not authoritative. Restarting does not reset the 60-second deadline. Committed
emergency notices are fanned out to each worker's existing WebSocket connections;
the dashboard also refreshes state every four seconds for recovery.

## API contracts

All coordination APIs require the existing bearer JWT.

| Endpoint | Behaviour |
| --- | --- |
| `GET /api/coordination/config` | Safe UI settings (no keys) |
| `POST /api/emergencies` | Create unique DB emergency ID, dispatch immediately from explicit pickup coordinates, notify eligible hospitals |
| `GET /api/emergencies/{id}/coordination` | Authorized persisted state, server clock, GPS freshness, responses and route history |
| `GET /api/emergencies/{id}/nearby?origin=AMBULANCE` | Search from latest fresh assigned ambulance GPS; `PICKUP` explicitly selects recorded pickup |
| `POST /api/emergencies/{id}/dispatch` | Driver/admin coordination from explicit `origin`; existing dispatch is idempotent, retaining deadline and notification origin |
| `PATCH /api/emergencies/{id}/hospital-response` | Hospital's own `ACCEPTED`, `DECLINED`, or `WITHDRAWN` response; no client hospital ID accepted |
| `POST /api/emergencies/{id}/location` | Assigned driver's GPS, optional original `observed_at`, and `source`; rejects stale/future/out-of-order positions |
| `POST /api/emergencies/{id}/reassess` | Authorized driver/admin requests current route comparison |
| `PATCH /api/emergencies/{id}/status` | Validated lifecycle; existing hospital ACCEPTED/REJECTED calls delegate to hospital responses |
| `GET /api/hospitals/me/emergencies` | Own incoming requests plus existing assigned emergencies, with patient identity stripped |
| `PATCH /api/hospitals/{id}/capacity` | Existing capacity update; timestamp and reassess accepted active emergencies |

Emergency creation supports `demo: true` only when enabled in backend config.
`clinical_destination_locked: true` requires a hospital account and `hospital_id`.
Driver dispatch cannot weaken a recorded care requirement. Driver transitions
do not grant hospital acceptance. Hospital response identity comes from its JWT.

The driver's initial emergency form uses current browser GPS as the **pickup**
and says so explicitly. For an existing emergency, `nearby?origin=AMBULANCE`
uses actual telemetry; no seeded ambulance coordinate is accepted as a fresh fix.
Once dispatched, redispatch does not silently change the recorded origin or
reset the response window. New searches are previews from the explicitly chosen
origin, not replacements for the dispatched request.

WebSocket events reuse the existing notification envelope (`title`, `body`,
`kind`, `reference`), with `kind=EMERGENCY` and event names
`emergency_coordination_updated` / `emergency_escalation`. The reference includes
emergency and version identifiers. On receipt/reconnect, the UI fetches current
authorized state rather than applying an untrusted destination from a message.

## Decision rules

1. Initial response deadline is exactly 60 seconds from server dispatch.
2. A first eligible acceptance is provisionally assigned in its transaction,
   before waiting for road estimates or the remaining response window.
3. Timed-out initial responses are not silently accepted. With
   `EMERGENCY_ALLOW_LATE_ACCEPTANCE=true`, a timed-out hospital may submit a
   **new, explicitly late acceptance** only while status is `IN_TRANSIT`, a
   destination already exists, and GPS is fresh. Declines/withdrawals cannot be
   reversed via a duplicate acceptance.
4. Exclude inactive, location-unverified, clinically unsuitable, declined,
   withdrawn or currently known-unavailable facilities. Explicit acceptance
   confirms capacity for this request. Unknown/stale aggregate availability is
   displayed as such, rather than interpreted as live telemetry.
5. Compare all accepted candidates from one fresh GPS snapshot using OSRM road
   distance/time. Persist the origin and provider timestamps. Do not compare a
   pickup distance with a remaining ambulance route.
6. Keep current destination unless time saved, **after switch cost**, meets
   `REROUTE_MIN_TIME_SAVING_MINUTES`. Distance is a tie-breaker; the distance
   threshold applies when the configured time threshold and net time saving are
   both zero. Initial candidates are ranked by time then road distance.
7. Retain the current destination if its remaining time is at/below
   `REROUTE_NEAR_DESTINATION_MINUTES`, estimates are unavailable/too close, or a
   recent reroute is within the cooldown. Clinical locks cannot be overridden.
8. If acceptance is withdrawn or declared capacity becomes unavailable, select
   a suitable accepted replacement only with fresh GPS/road data; otherwise
   release the provisional destination and escalate to dispatcher/admin.
9. No rerouting after arrival, treatment, completion or cancellation. No
   automatically inferred arrival is used; the driver marks arrival explicitly.

The existing `Escalation` table requires a complaint FK, so emergency escalation
uses persisted emergency timeline/status, audit logs and dispatcher/admin
notifications instead of creating a fictitious complaint. It directs staff to
the configured emergency phone number. This application does not call a public
dispatch centre or reserve a bed in an external HIS.

## Map integration

`coordination-map.html` loads the **existing** `frontend/js/map.js` read-only
through `/coordination-map/library.js` (local repository when present, otherwise
the configured map service). It uses its `initLeafletMap`, `drawRouteOnMap` and
`clearRouteFromMap` functions. No alternate routing/map implementation is added.

The authenticated parent sends `mediroute:assignment` with authoritative route
geometry via same-origin `postMessage`. The adapter checks origin, parent window
and `state_version`; stale events cannot restore an old route. It updates the
destination label, ambulance marker and existing road polyline. It clears stale
routes rather than drawing an invented line. No JWT is sent to the iframe or
third-party map service. The map uses the provider's road ETA, **not live traffic**.

**Standalone external dashboard limitation:** the existing deployment has no
destination update contract. The smallest compatible addition is a backend-only,
authenticated `POST /api/emergency/destination`, accepting:

```json
{
  "request_id": "MR-123",
  "emergency_id": 123,
  "hospital_id": "KS001",
  "hospital_name": "KS Hospital - Emergency Center (Demo)",
  "latitude": 27.6182,
  "longitude": 77.6014,
  "assignment_version": 2,
  "state_version": 7,
  "route": {"available": true, "distance_km": 1.2, "duration_min": 2.5, "points": []}
}
```

The values above only illustrate the contract, not a routing result. That
adapter must authenticate `MAP_BRIDGE_TOKEN`, persist versions per emergency,
ignore older/equal `(assignment_version, state_version)` updates, upsert the
provided destination without a JS fallback, and emit an existing Socket.IO
status event that applies the supplied route geometry without recalculation or
client acceptance. It must handle `hospital_id: null` / closed status for
cancellation in a full external deployment. Until this interface is implemented
and deployed, leave `MAP_DESTINATION_UPDATE_ENABLED=false`. MediRoute's embedded
renderer updates now; the standalone remote page does not synchronize assignments.

## Repeatable JS / KS demo

1. In `fir/.env`, set `MEDIROUTE_DEMO_COORDINATION=true`. Keep the threshold
   defaults from `.env.example`. Start with:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn mediroute.app:app --env-file .env --port 8000
   ```

2. Default **demo fixtures**, not verified real facilities:
   * JS001: `(27.652, 77.558)`; existing `hospital@mediroute.demo`.
   * KS001: `(27.6182, 77.6014)`; `ks-hospital@mediroute.demo`.
   * Both declare demo trauma/emergency capability and simulated capacity.
   * New installation coordinates can be configured with
     `DEMO_JS_HOSPITAL_LAT/LNG` and `DEMO_KS_HOSPITAL_LAT/LNG`; invalid values
     fail validation. Existing coordinates/IDs are preserved on startup. To
     change an existing demo location, explicitly update that database record
     in your local demo setup, never based on the user's current position.
   * Credentials use the existing development demo password convention,
     configurable through `MEDIROUTE_DEMO_PASSWORD` on initial seed. Existing
     accounts/passwords are not reset by startup. Custom passwords must be
     entered normally instead of the legacy one-click demo password shortcut.
3. Use separate browser profiles/devices for driver, JS hospital and KS hospital
   sessions (the existing frontend token is stored in localStorage).
4. On Driver dashboard, select **Explicit controlled demo**, enter pickup
   `(27.6100, 77.6000)`, category `Controlled transport demo`, care `trauma`, and
   create. Both hospitals should receive the request within the configured radius.
5. Submit the same **labelled demo ambulance position** in that emergency's
   demo-position form. For real driving, use **Start live GPS**, not this form.
6. JS hospital: Emergency → **Accept & confirm capacity**. Driver sees a
   provisional JS destination immediately and the provider route when available.
7. Driver: **Patient onboard / En route**. Submit another explicitly chosen demo
   position to represent progress (or use actual GPS for a non-demo request).
8. KS hospital: accept later. Both road estimates use the latest identical
   origin. The destination changes only if measured savings meet thresholds.
   The map and assignment history update; JS receives a release notice.
9. Mark **Arrived**, then **Complete emergency**. Further acceptance/rerouting is
   refused. New demo emergencies have new IDs and independent response windows.

Alternatively, the following runs the same APIs with **live OSRM estimates**:

```powershell
.\.venv\Scripts\python.exe demo_coordination.py
# Optional deterministic cleanup after observing the result:
.\.venv\Scripts\python.exe demo_coordination.py --complete
```

The script uses chosen demo positions and explicitly labels them. It does not
manufacture route distances or force a reroute; provider changes/outages may
cause the current destination to be retained. Use **Reassess route** or submit a
fresh demo position when estimates expire. Demo locations do not follow the user.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
node --test tests/test_coordination_map.cjs
node --check mediroute/static/app.js
node --check mediroute/static/coordination.js
node --check mediroute/static/coordination-map.js
```

The Python feature suite uses an isolated SQLite database and clearly marked
test-only route fixtures for controlled concurrency/timing/threshold scenarios.
It covers nearby search, JS/KS inclusion/notification, acceptance/decline,
server timeout, late response, simultaneous acceptance, same-origin comparison,
meaningful/insignificant rerouting, care/clinical lock, terminal states, GPS and
provider failures, withdrawal/capacity loss, permissions, stale decisions,
cooldown, seed identity and the existing WebSocket. The Node test executes the
map adapter against renderer stubs and checks route updates, origin/parent
validation and stale message rejection. It is not a visual browser test.
