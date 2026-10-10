# Emergency coordination

MediRoute's FastAPI coordination workflow remains available for the existing
JWT hospital role and ambulance driver. JS Hospital's existing map dashboard is
unchanged. KS Hospital is **not** a second FastAPI/JWT role or account.

## JS / KS map reception dashboards

The Flask map module serves the same `hospital.html`, `hospital.css`, and
`hospital_dashboard.js` implementation at:

* `http://127.0.0.1:5000/hospital` — JS Hospital (`JS001`)
* `http://127.0.0.1:5000/ks-hospital` — KS Hospital (`KS001`)

The dashboard identifies its hospital from the URL and sends its own hospital
identifier with every response. The ambulance request is broadcast to both
configured targets at the same time. Each reception dashboard has its own
popup, Accept button, Reject button, queue state, and server response row.

The response window is 60 seconds from the server-created request deadline. The
popup countdown is a display only; the Flask `/api/emergency/respond` endpoint
rejects responses after the deadline, marks pending targets `TIMED_OUT`, and
broadcasts `emergency_response_window_expired`. Responses are independent: JS
can accept while KS rejects, or both can respond separately. The first accepted
hospital becomes the accepted destination in the ambulance UI; later response
events remain visible by hospital ID.

The legacy Socket.IO `hospital_response` event is retained for compatibility,
but REST `/api/emergency/respond` is authoritative. The request store uses a
lock for simultaneous responses and a unique response status per hospital.

## Demo procedure

1. Start the map module from its `backend` directory.
2. Open the ambulance dashboard at `http://127.0.0.1:5000/ambulance`.
3. Open both reception pages in separate browser tabs/windows using the URLs
   above.
4. Trigger **Emergency Patient Onboard** on the ambulance page.
5. Both JS and KS popups should appear with the same request ID and a 60-second
   countdown.
6. Accept or reject independently from either popup before the deadline.

The map service's controlled demo configuration is the source of the JS001 and
KS001 identifiers. They are clearly labelled demo reception endpoints; their
displayed capacity data is the existing demo telemetry, not a claim about a
verified real-world facility.

## FastAPI role cleanup

The previous `ks-hospital@mediroute.demo` FastAPI account and `/ks-hospital`
FastAPI role alias were removed. Existing databases remove the old account on
startup; the map dashboard is the only KS Hospital dashboard now.
