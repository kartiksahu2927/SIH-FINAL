/* Existing notification WebSocket triggers refresh; polling handles reconnects
   and deadline/GPS freshness changes. All decisions remain server-side. */
window.Coordination = (() => {
  let rows = [], refreshTimer = null, countdownTimer = null, generation = 0;
  let watchId = null, gpsEmergencyId = null, gpsBusy = false, lastGpsSent = 0, refreshing = false;
  const closed = new Set(['ARRIVED', 'UNDER_TREATMENT', 'COMPLETED', 'CANCELLED']);
  const esc = value => escapeHtml(String(value ?? '—'));
  const stamp = value => value && !/[Zz]|[+-]\d\d:\d\d$/.test(value) ? `${value}Z` : value;
  let clockOffset = 0;
  let routeMaxAgeMs = 30000;
  function clock() { return Date.now() + clockOffset; }
  function timer() {
    document.querySelectorAll('[data-response-deadline]').forEach(el => {
      const seconds = Math.max(0, Math.ceil((Date.parse(stamp(el.dataset.responseDeadline)) - clock()) / 1000));
      el.textContent = seconds ? `${seconds}s remaining` : 'Initial response window ended';
    });
  }
  function routeText(route, fresh) {
    const recent = route?.route_data_timestamp && clock() - Date.parse(stamp(route.route_data_timestamp)) <= routeMaxAgeMs;
    return route?.available && fresh && recent ? `${Number(route.distance_km).toFixed(2)} road km · ${Number(route.duration_min).toFixed(1)} min (no live traffic)` : 'Fresh road-route distance / ETA unavailable';
  }
  function renderRows() {
    const hospital = state.user.role === 'HOSPITAL';
    return rows.map(data => {
      const e = data.emergency, d = e.details || {}, active = !closed.has(e.status);
      const current = data.responses.find(r => r.hospital_id === e.hospital_id);
      const gps = data.current_location;
      const controls = hospital ? '' : `<div class="coord-actions">
        ${active ? `<button data-coord-action="gps" data-id="${e.id}">Start live GPS</button><button data-coord-action="stop-gps">Stop GPS</button>
        <button data-coord-action="nearby" data-id="${e.id}">Nearby from current GPS</button><button data-coord-action="dispatch" data-id="${e.id}">Coordinate from current GPS</button>
        <button data-coord-action="reassess" data-id="${e.id}">Reassess route</button>` : ''}
        ${active && e.hospital_id && ['ACCEPTED','DRIVER_ASSIGNED'].includes(e.status) ? `<button data-coord-status="IN_TRANSIT" data-id="${e.id}">Patient onboard / En route</button>` : ''}
        ${e.status === 'IN_TRANSIT' ? `<button data-coord-status="ARRIVED" data-id="${e.id}">Mark arrived</button>` : ''}
        ${['ARRIVED','UNDER_TREATMENT'].includes(e.status) ? `<button data-coord-status="COMPLETED" data-id="${e.id}">Complete emergency</button>` : ''}
        ${active ? `<button class="quiet" data-coord-status="CANCELLED" data-id="${e.id}">Cancel emergency</button>` : ''}
      </div>`;
      return `<article class="card coordination-card" data-emergency="${e.id}">
        <h3>Emergency #${e.id} ${d.demo ? '<span class="pill">DEMO — simulated facility data</span>' : ''}</h3>
        ${!hospital || e.hospital_id === state.user.hospital_id ? `<button type="button" class="quiet" onclick="openStaffFirstAidModal(${e.id})">First-Aid Log</button>` : ''}
        <p><strong>${esc(e.status)}</strong> · <span data-response-deadline="${esc(d.response_deadline || '')}"></span> · Assignment v${d.assignment_version || 0}</p>
        <p>${esc(d.condition)} · Required care: ${esc(d.required_service || 'Not specified')}</p>
        <p>Pickup: ${esc(d.latitude)}, ${esc(d.longitude)} · Dispatched: ${esc(d.dispatch_at)}</p>
        <p>Ambulance: ${gps ? `${esc(gps.latitude)}, ${esc(gps.longitude)} (${esc(gps.source)}, ${esc(gps.at)})` : 'GPS unavailable or stale — enable location permission and broadcast a fresh fix.'}</p>
        <p><strong>Destination: ${esc(current?.hospital_name || (e.hospital_id ? `Hospital #${e.hospital_id}` : 'Awaiting confirmation'))}</strong><br>${esc(routeText(d.current_route, !!gps))}</p>
        ${d.clinical_destination_locked ? '<p class="notice">Clinical destination locked. Automated alternatives cannot override it.</p>' : ''}
        ${d.last_assignment_reason ? `<p>${esc(d.last_assignment_reason)}</p>` : ''}
        ${d.escalation ? `<p class="error">${esc(d.escalation)} Contact dispatcher / <a href="tel:112">112</a>.</p>` : ''}
        <div class="table-wrap"><table><thead><tr><th>Notified hospital</th><th>Origin distance</th><th>Declared care / availability</th><th>Response / assignment</th><th>Action</th></tr></thead><tbody>${data.responses.map(r => {
          const own = r.hospital_id === state.user.hospital_id;
          const late = Date.parse(stamp(r.response_deadline)) <= clock();
          const accept = active && (r.status === 'PENDING' && !late || ['PENDING','TIMED_OUT'].includes(r.status) && late && e.status === 'IN_TRANSIT' && gps);
          const assignment = e.hospital_id === r.hospital_id ? 'Selected destination' : r.status === 'ACCEPTED' ? 'Accepted alternative; no active reservation assigned' : 'Not selected';
          return `<tr><td>${esc(r.hospital_name)}${r.demo_facility ? ' (Demo)' : ''}<br><small>Notified ${esc(r.notified_at)}</small></td><td>${Number(r.distance_km).toFixed(2)} geographic km</td><td>${esc(r.hospital_services.join(', '))}<br>${esc(r.eligibility?.demo_facility ? 'Demo capacity; confirmation required' : 'Hospital acceptance confirms capacity')}</td><td>${esc(r.status)}<br>${assignment}${r.eligibility?.late_acceptance ? ' · late response' : ''}</td><td>${hospital && own && accept ? `<button data-coord-response="ACCEPTED" data-id="${e.id}">${late ? 'Accept late' : 'Accept & confirm capacity'}</button>` : ''}${hospital && own && active && r.status === 'PENDING' && !late ? `<button class="quiet" data-coord-response="DECLINED" data-id="${e.id}">Decline</button>` : ''}${hospital && own && active && r.status === 'ACCEPTED' ? `<button class="quiet" data-coord-response="WITHDRAWN" data-id="${e.id}">Withdraw acceptance</button>` : ''}</td></tr>`;
        }).join('')}</tbody></table></div>
        ${controls}
        <details><summary>Reported emergency availability</summary>${data.responses.map(r => `<p>${esc(r.hospital_name)}: ${r.availability_source === 'UNKNOWN' ? 'Unavailable' : `${esc(r.emergency_available)} declared emergency spaces`} (${esc(r.availability_source)}${r.availability_checked_at ? `; reported ${esc(r.availability_checked_at)}` : ''}). Acceptance confirms capacity for this request.</p>`).join('')}</details>
        ${!hospital && d.last_route_comparison ? `<details><summary>Latest same-position road comparison</summary><p>${esc(d.last_route_comparison.reason || d.last_route_comparison.limitation)}</p>${(d.last_route_comparison.candidates || []).map(c => `<p>${esc(c.hospital_name)}: ${esc(routeText(c.route, !!gps))}</p>`).join('')}</details>` : ''}
        ${!hospital && d.demo && active ? `<form class="coord-demo-location" data-id="${e.id}"><h4>Explicit simulated ambulance position (demo only)</h4><label>Latitude<input name="latitude" type="number" step="any" min="-90" max="90" required></label><label>Longitude<input name="longitude" type="number" step="any" min="-180" max="180" required></label><button>Submit labelled demo position</button></form>` : ''}
      </article>`;
    }).join('') || '<article class="card">No emergency requests are currently assigned to this account.</article>';
  }
  function pushMap() {
    const frame = document.getElementById('coordination-map');
    const active = rows.find(r => !closed.has(r.emergency.status)) || rows[0];
    if (frame?.contentWindow && active) frame.contentWindow.postMessage({type:'mediroute:assignment', payload:active}, location.origin);
  }
  async function refresh() {
    const el = document.getElementById('coordination-list');
    if (!el || !state.user || refreshing) return;
    refreshing = true;
    const run = generation;
    try {
      let fresh;
      if (state.user.role === 'HOSPITAL') fresh = await api('/hospitals/me/emergencies');
      else {
        const list = await api('/driver/assignments');
        fresh = await Promise.all(list.slice(0, 20).map(e => api(`/emergencies/${e.id}/coordination`)));
      }
      if (run !== generation || !el.isConnected) return;
      if (fresh[0]?.server_time) clockOffset = Date.parse(stamp(fresh[0].server_time)) - Date.now();
      rows = fresh.map(data => {
        const previous = rows.find(r => r.emergency.id === data.emergency.id);
        return previous && previous.emergency.state_version > data.emergency.state_version ? previous : data;
      });
      if (gpsEmergencyId && rows.some(data => data.emergency.id === gpsEmergencyId && closed.has(data.emergency.status))) stopGps();
      // Preserve input focus while staff enter notes or explicit demo GPS.
      if (!el.contains(document.activeElement) || !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)) el.innerHTML = renderRows();
      timer(); pushMap();
    } catch (error) { setStatus(error.message, true); }
    finally { refreshing = false; }
  }
  function stopGps() { if (watchId !== null) navigator.geolocation.clearWatch(watchId); watchId = null; gpsEmergencyId = null; }
  function stop() { generation++; clearInterval(refreshTimer); clearInterval(countdownTimer); stopGps(); }
  function startGps(id) {
    stopGps();
    gpsEmergencyId = id;
    if (!navigator.geolocation) throw new Error('Geolocation is unavailable in this browser. No position was substituted.');
    watchId = navigator.geolocation.watchPosition(async p => {
      if (!state.user || gpsBusy || Date.now() - lastGpsSent < 12000) return;
      gpsBusy = true; lastGpsSent = Date.now();
      try {
        await api(`/emergencies/${id}/location`, {method:'POST', body:JSON.stringify({latitude:p.coords.latitude, longitude:p.coords.longitude, speed_kmh:p.coords.speed == null ? null : p.coords.speed * 3.6, observed_at:new Date(p.timestamp).toISOString(), source:'GPS'})});
        await refresh();
      } catch (error) { setStatus(error.message, true); }
      finally { gpsBusy = false; }
    }, error => { stopGps(); setStatus(`GPS unavailable: ${error.message}. Allow location access; no fallback coordinates were used.`, true); }, {enableHighAccuracy:true, maximumAge:0, timeout:15000});
  }
  function bind() {
    if (!document.getElementById('coordination-list')) return;
    clearInterval(refreshTimer); clearInterval(countdownTimer);
    refreshTimer = setInterval(refresh, 4000); countdownTimer = setInterval(timer, 250);
    refresh();
  }
  function renderNearby(results) {
    const target = document.getElementById('nearby-search-results');
    if (!target) return;
    target.innerHTML = results.hospitals?.length ? `<div class="table-wrap"><table><thead><tr><th>Hospital</th><th>Distance</th><th>Location</th><th>Published information</th></tr></thead><tbody>${results.hospitals.map(h => `<tr><td>${esc(h.name)}${h.demo_facility ? ' (Controlled demo)' : ''}</td><td>${Number(h.distance_km).toFixed(2)} km</td><td>${esc(h.address || `${h.latitude}, ${h.longitude}`)}</td><td>${esc(h.services?.join(', ') || 'No services published')}<br><small>${esc(h.availability_status || 'Availability not published')}</small></td></tr>`).join('')}</tbody></table></div>` : `<p class="muted">${esc(results.message || 'No genuine hospitals were found for these coordinates and radius.')}</p>`;
  }
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-coord-action],[data-coord-response],[data-coord-status]');
    if (!button) return;
    button.disabled = true;
    const id = Number(button.dataset.id);
    try {
      if (button.dataset.coordResponse) await api(`/emergencies/${id}/hospital-response`, {method:'PATCH', body:JSON.stringify({status:button.dataset.coordResponse})});
      else if (button.dataset.coordStatus) await api(`/emergencies/${id}/status`, {method:'PATCH', body:JSON.stringify({status:button.dataset.coordStatus})});
      else if (button.dataset.coordAction === 'gps') startGps(id);
      else if (button.dataset.coordAction === 'stop-gps') stopGps();
      else if (button.dataset.coordAction === 'reassess') await api(`/emergencies/${id}/reassess`, {method:'POST'});
      else if (button.dataset.coordAction === 'dispatch') {
        const result = await api(`/emergencies/${id}/dispatch`, {method:'POST', body:JSON.stringify({origin:'AMBULANCE'})});
        setStatus(result.idempotent ? 'Already dispatched from the recorded origin. The initial deadline and notifications were preserved; use Nearby to inspect current-GPS candidates.' : 'Hospitals notified from the current ambulance position.');
      }
      else if (button.dataset.coordAction === 'nearby') {
        const data = await api(`/emergencies/${id}/nearby?origin=AMBULANCE`);
        document.getElementById('coordination-nearby').textContent = `From current GPS (${data.origin.latitude}, ${data.origin.longitude}), radius ${data.radius_km} km: ${data.matches.map(h => `${h.name}${h.demo_facility ? ' (Demo)' : ''} — ${h.distance_km} geographic km; ${h.services.join(', ')}`).join(' | ') || 'No eligible hospitals'}`;
      }
      await refresh();
    } catch (error) { setStatus(error.message, true); await refresh(); }
    finally { button.disabled = false; }
  });
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (form.id === 'nearby-search-form') {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(form));
      try {
        const result = await api(`/hospitals/nearby?lat=${encodeURIComponent(Number(data.latitude))}&lng=${encodeURIComponent(Number(data.longitude))}&radius_km=${encodeURIComponent(Number(data.radius_km))}`);
        renderNearby(result);
      } catch (error) { setStatus(error.message, true); }
      return;
    }
    if (!form.matches('.coord-demo-location,#coordination-create')) return;
    event.preventDefault();
    const d = Object.fromEntries(new FormData(form));
    try {
      if (form.matches('.coord-demo-location')) await api(`/emergencies/${form.dataset.id}/location`, {method:'POST', body:JSON.stringify({latitude:Number(d.latitude), longitude:Number(d.longitude), observed_at:new Date().toISOString(), source:'DEMO'})});
      else {
        const demo = d.demo === 'on';
        let lat = Number(d.latitude), lng = Number(d.longitude);
        if (!demo) {
          const p = await new Promise((resolve,reject) => navigator.geolocation ? navigator.geolocation.getCurrentPosition(resolve,reject,{enableHighAccuracy:true,maximumAge:0,timeout:15000}) : reject(new Error('Geolocation unavailable')));
          lat = p.coords.latitude; lng = p.coords.longitude;
        }
        await api('/emergencies', {method:'POST', body:JSON.stringify({condition:d.condition, required_service:d.required_service || null, latitude:lat, longitude:lng, demo, origin:'PICKUP'})});
      }
      await refresh();
    } catch (error) { setStatus(error.message, true); }
  });
  window.addEventListener('message', event => {
    if (event.origin === location.origin && event.source === document.getElementById('coordination-map')?.contentWindow && event.data?.type === 'mediroute:map-ready') pushMap();
  });
  async function view(hospital = false) {
    const config = await api('/coordination/config');
    routeMaxAgeMs = config.route_max_age_seconds * 1000;
    return `${hospital ? '' : `<article class="card"><h3>Hospital destination map</h3><iframe id="coordination-map" class="coord-map" src="/coordination-map.html" title="Authoritative ambulance destination and road route"></iframe></article>
      <article class="card"><h3>Nearby hospital discovery</h3><p>Enter an explicit selected location. GPS is not silently replaced. Results are limited to OSM records tagged as hospitals; capacity and ETA are not inferred.</p><form id="nearby-search-form"><label>Selected latitude<input name="latitude" type="number" step="any" min="-90" max="90" required></label><label>Selected longitude<input name="longitude" type="number" step="any" min="-180" max="180" required></label><label>Radius (km)<input name="radius_km" type="number" min="1" max="100" value="10" required></label><button type="button" id="kcc-location-button" class="quiet">Use selected KCC College test point</button><button>Search genuine hospitals</button></form><div id="nearby-search-results" role="status"></div></article>
      <article class="card"><h3>Create emergency at pickup</h3><form id="coordination-create"><label>Emergency category / care summary<input name="condition" required maxlength="1000"></label><label>Required service<input name="required_service" placeholder="e.g. trauma"></label>${config.demo_enabled ? '<label><input name="demo" type="checkbox">Explicit controlled demo (simulated facility capacity and chosen pickup)</label><label>Demo pickup latitude<input name="latitude" type="number" step="any" min="-90" max="90" value="27.6100"></label><label>Demo pickup longitude<input name="longitude" type="number" step="any" min="-180" max="180" value="77.6000"></label>' : ''}<p>For a real request, the browser’s current GPS is the pickup location. Location permission is required.</p><button>Create and notify eligible hospitals</button></form></article>`}
      <p id="coordination-nearby" role="status"></p><section id="coordination-list">Loading hospital coordination…</section>`;
  }
  document.addEventListener('click', event => {
    if (event.target.id !== 'kcc-location-button') return;
    const form = document.getElementById('nearby-search-form');
    if (!form) return;
    form.latitude.value = '28.4595';
    form.longitude.value = '77.5000';
    setStatus('Selected reproducible test point near KCC College, Greater Noida: 28.4595, 77.5000. This is an explicit selection, not a GPS claim.');
  });
  return {view, bind, refresh, stop};
})();
