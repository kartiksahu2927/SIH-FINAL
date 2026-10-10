/* Adapter to the existing map.js renderer. No GPS simulation or route inference. */
(() => {
  let revision = -1, emergencyId = null, routeKey = '', markers = [];
  if (typeof window.initLeafletMap !== 'function' || !window.L) {
    document.getElementById('summary').textContent = 'Existing map renderer unavailable. The assigned destination is still shown in the dashboard.';
    return;
  }
  window.initLeafletMap();
  window.addEventListener('message', event => {
    if (event.origin !== location.origin || event.source !== parent || event.data?.type !== 'mediroute:assignment') return;
    const data = event.data.payload, e = data?.emergency;
    if (!e || !Number.isInteger(e.state_version)) return;
    if (emergencyId === e.id && e.state_version < revision) return;
    emergencyId = e.id; revision = e.state_version;
    const selected = data.responses.find(r => r.hospital_id === e.hospital_id);
    const route = e.details?.current_route;
    const fresh = !!data.current_location && route?.available;
    const name = selected?.hospital_name || 'Awaiting accepted destination';
    document.getElementById('summary').textContent = `Emergency #${e.id} · ${name} · ${fresh ? `${route.distance_km.toFixed(2)} km / ${route.duration_min.toFixed(1)} min (OSRM, no live traffic)` : 'Fresh route/ETA unavailable'}${e.details?.demo ? ' · DEMO' : ''}`;
    markers.forEach(marker => marker.remove()); markers = [];
    const addMarker = (lat, lng, text, color) => {
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
      const label = document.createElement('span'); label.textContent = text;
      markers.push(L.circleMarker([lat, lng], {radius: 8, color}).addTo(window.map).bindPopup(label));
    };
    data.responses.forEach(r => addMarker(r.hospital_latitude, r.hospital_longitude, `${r.hospital_name}: ${r.status}${r.demo_facility ? ' (Demo)' : ''}`, r.hospital_id === e.hospital_id ? '#087f76' : '#64748b'));
    if (data.current_location) addMarker(data.current_location.latitude, data.current_location.longitude, `Ambulance ${data.current_location.source}`, '#dc2626');
    const key = `${e.id}:${e.details?.assignment_version}:${route?.route_data_timestamp}:${fresh}`;
    if (key !== routeKey) {
      routeKey = key;
      if (fresh && Array.isArray(route.points) && route.points.length > 1) window.drawRouteOnMap({points: route.points});
      else window.clearRouteFromMap();
    }
  });
  parent.postMessage({type: 'mediroute:map-ready'}, location.origin);
})();
