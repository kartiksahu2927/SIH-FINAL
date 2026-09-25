/* ============================================================
   map.js — Universal Map Controller (Multi-Fleet & Real Streets)
   ============================================================
   Provides:
   - Simultaneous multi-ambulance fleet marker visualization
   - Active ambulance glowing telemetry pin
   - Interactive hospital pins with eligibility status
   - Real turn-by-turn road network route polyline rendering
   ============================================================ */

let map = null;
let mapType = 'leaflet';
let fleetMarkers = {};             // Map of unit_id -> Marker
let hospitalMarkers = [];
let routePolyline = null;
let routeShadowPolyline = null;
let selectedHospitalMarker = null;
let firstLocationUpdate = true;

const DEFAULT_CENTER = { lat: 28.6139, lng: 77.2090 };

/* ===========================================================
   GOOGLE MAPS INITIALIZER
   =========================================================== */
function initMap() {
  const mapEl = document.getElementById('map');
  const leafletEl = document.getElementById('leaflet-map');
  if (!mapEl) return;

  try {
    mapType = 'google';
    mapEl.style.display = 'block';
    if (leafletEl) leafletEl.style.display = 'none';

    map = new google.maps.Map(mapEl, {
      center: DEFAULT_CENTER,
      zoom: 13,
      minZoom: 4,
      maxZoom: 19,
      mapTypeControl: true,
      streetViewControl: false,
      fullscreenControl: true,
      zoomControl: true,
      styles: [
        { featureType: 'poi.medical', elementType: 'geometry', stylers: [{ color: '#f8d7da' }] },
        { featureType: 'poi.business', stylers: [{ visibility: 'off' }] }
      ]
    });

    map.infoWindow = new google.maps.InfoWindow();
    window.map = map;

    const badge = document.getElementById('engine-badge-text');
    if (badge) badge.textContent = 'Google Maps Engine (Active)';
    const badgeBox = document.getElementById('engine-badge');
    if (badgeBox) {
      badgeBox.classList.remove('simulation');
      badgeBox.classList.add('google');
    }

    console.log('[Map] Google Maps initialized successfully.');

    if (window.ambulanceFleet) {
      updateFleetMarkers(window.ambulanceFleet, window.LocationTracker?.activeUnitId || "AMB-101");
    }
  } catch (err) {
    console.warn('[Map] Google Maps init error, falling back to Leaflet:', err);
    initLeafletMap();
  }
}


/* ===========================================================
   LEAFLET / OPENSTREETMAP INITIALIZER (Zero-Config Fallback)
   =========================================================== */
function initLeafletMap() {
  if (map && mapType === 'leaflet') return;

  const mapEl = document.getElementById('map');
  const leafletEl = document.getElementById('leaflet-map');
  if (!leafletEl) return;

  mapType = 'leaflet';
  if (mapEl) mapEl.style.display = 'none';
  leafletEl.style.display = 'block';

  try {
    if (typeof L === 'undefined') {
      console.error('[Map] Leaflet library not loaded.');
      return;
    }

    map = L.map('leaflet-map', {
      center: [DEFAULT_CENTER.lat, DEFAULT_CENTER.lng],
      zoom: 13,
      zoomControl: true
    });

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors | Multi-Ambulance Emergency Nav',
      maxZoom: 19
    }).addTo(map);

    window.map = map;

    const badge = document.getElementById('engine-badge-text');
    if (badge) badge.textContent = 'OpenStreetMap & Real Road Engine (Active)';

    console.log('[Map] Leaflet/OpenStreetMap engine initialized.');

    setTimeout(() => {
      if (map && typeof map.invalidateSize === 'function') {
        map.invalidateSize();
      }
    }, 250);

    if (window.ambulanceFleet) {
      updateFleetMarkers(window.ambulanceFleet, window.LocationTracker?.activeUnitId || "AMB-101");
    }
  } catch (e) {
    console.error('[Map] Leaflet initialization failed:', e);
  }
}

window.addEventListener('resize', () => {
  if (map && mapType === 'leaflet' && typeof map.invalidateSize === 'function') {
    map.invalidateSize();
  }
});


/* ===========================================================
   MULTI-AMBULANCE FLEET MARKERS RENDERING
   =========================================================== */
function updateFleetMarkers(fleet, activeUnitId) {
  if (!map || !Array.isArray(fleet)) return;

  fleet.forEach(unit => {
    const lat = Number(unit.lat);
    const lng = Number(unit.lng);
    const isActive = unit.id === activeUnitId;
    const unitShort = unit.id.replace("AMB-", "#");

    const popupHtml = `
      <div style="font-family:sans-serif; min-width:200px; padding:4px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
          <strong style="color:${isActive ? '#dc2626' : '#2563eb'}; font-size:14px;">🚑 ${unit.callsign}</strong>
          <span style="font-size:10px; font-weight:700; background:#f1f5f9; padding:2px 6px; border-radius:4px;">${unit.plate}</span>
        </div>
        <div style="font-size:11px; color:#555; margin-bottom:4px;"><strong>Type:</strong> ${unit.type}</div>
        <div style="font-size:11px; color:#555; margin-bottom:4px;"><strong>Crew:</strong> ${unit.crew}</div>
        <div style="font-size:11px; color:#555; margin-bottom:6px;"><strong>Location:</strong> ${unit.location_name}</div>
        ${!isActive ? `
          <button onclick="window.LocationTracker.switchAmbulance('${unit.id}')" 
                  style="width:100%; padding:6px; background:#2563eb; color:#fff; border:none; border-radius:4px; font-size:11px; font-weight:600; cursor:pointer;">
            Switch to This Ambulance
          </button>
        ` : `<div style="text-align:center; font-size:11px; font-weight:700; color:#16a34a;">🟢 Currently Selected Vehicle</div>`}
      </div>
    `;

    if (mapType === 'google') {
      if (!fleetMarkers[unit.id]) {
        const marker = new google.maps.Marker({
          position: { lat, lng },
          map: map,
          title: unit.callsign,
          zIndex: isActive ? 9999 : 5000,
        });

        marker.addListener('click', () => {
          if (map.infoWindow) {
            map.infoWindow.setContent(popupHtml);
            map.infoWindow.open(map, marker);
          }
        });

        fleetMarkers[unit.id] = marker;
      }

      const marker = fleetMarkers[unit.id];
      marker.setPosition({ lat, lng });
      marker.setZIndex(isActive ? 9999 : 5000);
      marker.setIcon({
        path: google.maps.SymbolPath.CIRCLE,
        scale: isActive ? 12 : 9,
        fillColor: isActive ? '#dc2626' : '#2563eb',
        fillOpacity: 1.0,
        strokeColor: '#ffffff',
        strokeWeight: isActive ? 3 : 2,
      });

    } else {
      // Leaflet Implementation
      const markerHtml = `
        <div class="fleet-pin ${isActive ? 'active-unit-pulse' : 'standby-unit'}">
          <span class="fleet-icon">🚑</span>
          <span class="fleet-badge">${unitShort}</span>
        </div>
      `;

      const customIcon = L.divIcon({
        className: 'fleet-leaflet-marker',
        html: markerHtml,
        iconSize: [44, 44],
        iconAnchor: [22, 22]
      });

      if (!fleetMarkers[unit.id]) {
        const marker = L.marker([lat, lng], { icon: customIcon, zIndexOffset: isActive ? 5000 : 1000 }).addTo(map);
        marker.bindPopup(popupHtml);
        marker.on('click', () => {
          if (unit.id !== LocationTracker.activeUnitId) {
            LocationTracker.switchAmbulance(unit.id);
          }
        });
        fleetMarkers[unit.id] = marker;
      } else {
        const marker = fleetMarkers[unit.id];
        marker.setLatLng([lat, lng]);
        marker.setIcon(customIcon);
        marker.setZIndexOffset(isActive ? 5000 : 1000);
        marker.setPopupContent(popupHtml);
      }
    }
  });

  // Pan to active unit on initial fix
  if (firstLocationUpdate && activeUnitId) {
    const active = fleet.find(u => u.id === activeUnitId);
    if (active) {
      if (mapType === 'google') {
        map.setCenter({ lat: active.lat, lng: active.lng });
        map.setZoom(13);
      } else {
        map.setView([active.lat, active.lng], 13);
      }
    }
    firstLocationUpdate = false;
  }
}

function updateAmbulanceLocation(lat, lng) {
  if (window.ambulanceFleet) {
    updateFleetMarkers(window.ambulanceFleet, window.LocationTracker?.activeUnitId || "AMB-101");
  }
}


/* ===========================================================
   HOSPITAL MARKERS RENDERING
   =========================================================== */
function showHospitalMarkers(hospitals) {
  if (!map) return;
  clearHospitalMarkers();

  hospitals.forEach((hospital, index) => {
    const lat = Number(hospital.latitude);
    const lng = Number(hospital.longitude);
    const rank = index + 1;
    const erAvail = hospital.emergency_beds_available ?? (hospital.emergency_bed_available ? 5 : 0);
    const erTot = hospital.emergency_beds_total ?? 20;
    const icuAvail = hospital.icu_beds_available ?? (hospital.icu_available ? 3 : 0);
    const icuTot = hospital.icu_beds_total ?? 8;
    const divert = hospital.divert_status || 'OPEN';
    const divertColor = divert === 'FULL_DIVERT' ? '#dc2626' : divert === 'HEAVY_LOAD' ? '#d97706' : '#16a34a';

    const popupHtml = `
      <div style="font-family:sans-serif; min-width:240px; padding:4px;">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:4px;">
          <strong style="color:#1976d2; font-size:14px;">🏥 ${escapeHtml(hospital.name)}</strong>
          <span style="font-size:10px; font-weight:700; background:${divertColor}20; color:${divertColor}; padding:2px 6px; border-radius:4px;">${divert}</span>
        </div>
        <div style="font-size:11px; color:#555; margin-bottom:6px;">${escapeHtml(hospital.address || '')}</div>
        
        <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:6px; margin-bottom:6px; font-size:11px;">
          <div style="display:flex; justify-content:space-between; margin-bottom:2px;">
            <span>🛏️ <strong>ER Beds:</strong></span>
            <strong style="color:${erAvail > 0 ? '#16a34a' : '#dc2626'}">${erAvail} / ${erTot} Free</strong>
          </div>
          <div style="display:flex; justify-content:space-between; margin-bottom:2px;">
            <span>🫁 <strong>ICU Beds:</strong></span>
            <strong style="color:${icuAvail > 0 ? '#16a34a' : '#dc2626'}">${icuAvail} / ${icuTot} Free</strong>
          </div>
          <div style="display:flex; justify-content:space-between;">
            <span>⏳ <strong>Triage Wait:</strong></span>
            <strong style="color:#2563eb">${hospital.er_wait_time_minutes ?? 0} min</strong>
          </div>
        </div>

        <div style="display:flex; justify-content:space-between; font-size:12px; font-weight:600; margin-bottom:6px;">
          <span style="color:#1565c0;">📍 ${hospital.distance_km ? hospital.distance_km.toFixed(1) : '--'} km</span>
          <span style="color:#ef6c00;">⏱ ${hospital.duration_text || '--'}</span>
        </div>
        <button onclick="if(window.Dashboard) window.Dashboard.selectHospital('${hospital.place_id}')" 
                style="width:100%; padding:6px; background:#1976d2; color:#fff; border:none; border-radius:4px; font-size:12px; cursor:pointer; font-weight:600;">
          Select Hospital Destination
        </button>
      </div>
    `;

    if (mapType === 'google') {
      const marker = new google.maps.Marker({
        position: { lat, lng },
        map: map,
        title: hospital.name,
        icon: {
          url: 'data:image/svg+xml;charset=UTF-8,' + encodeURIComponent(`
            <svg xmlns="http://www.w3.org/2000/svg" width="38" height="48" viewBox="0 0 38 48">
              <path fill="${divert === 'FULL_DIVERT' ? '#dc2626' : '#1976d2'}" stroke="#ffffff" stroke-width="2" d="M19 0C8.5 0 0 8.5 0 19c0 10.5 7.5 22 13.5 28 3 3 8 3 11 0C30.5 41 38 29.5 38 19 38 8.5 29.5 0 19 0z"/>
              <text x="19" y="26" font-family="Arial, sans-serif" font-size="16" font-weight="bold" fill="white" text-anchor="middle">${rank}</text>
            </svg>
          `),
          scaledSize: new google.maps.Size(38, 48),
          anchor: new google.maps.Point(19, 48),
        },
        zIndex: 200 + index,
      });

      marker.hospitalData = hospital;
      marker.addListener('click', () => {
        if (map.infoWindow) {
          map.infoWindow.setContent(popupHtml);
          map.infoWindow.open(map, marker);
        }
        if (window.Dashboard) {
          window.Dashboard.selectHospital(hospital.place_id);
        }
      });

      hospitalMarkers.push(marker);
    } else {
      // Leaflet marker
      const hospitalIcon = L.divIcon({
        className: 'hospital-leaflet-marker',
        html: `<div class="hospital-pin ${divert === 'FULL_DIVERT' ? 'pin-divert' : ''}"><span class="pin-rank">${rank}</span></div>`,
        iconSize: [36, 44],
        iconAnchor: [18, 44]
      });

      const marker = L.marker([lat, lng], { icon: hospitalIcon }).addTo(map);
      marker.hospitalData = hospital;
      marker.bindPopup(popupHtml);

      marker.on('click', () => {
        if (window.Dashboard) {
          window.Dashboard.selectHospital(hospital.place_id);
        }
      });

      hospitalMarkers.push(marker);
    }
  });

  fitMapToMarkers();
}

function clearHospitalMarkers() {
  if (mapType === 'google') {
    hospitalMarkers.forEach(m => m.setMap(null));
  } else if (map) {
    hospitalMarkers.forEach(m => map.removeLayer(m));
  }
  hospitalMarkers = [];
  selectedHospitalMarker = null;
}


/* ===========================================================
   HIGHLIGHT SELECTED HOSPITAL
   =========================================================== */
function highlightHospital(hospital) {
  if (!map || !hospital) return;

  const lat = Number(hospital.latitude);
  const lng = Number(hospital.longitude);
  const marker = hospitalMarkers.find(m => m.hospitalData?.place_id === hospital.place_id);

  if (mapType === 'google') {
    if (marker) {
      if (selectedHospitalMarker && selectedHospitalMarker !== marker) {
        selectedHospitalMarker.setAnimation(null);
      }
      marker.setAnimation(google.maps.Animation.BOUNCE);
      setTimeout(() => marker.setAnimation(null), 1500);
      map.panTo(marker.getPosition());
      selectedHospitalMarker = marker;
    } else {
      map.panTo({ lat, lng });
    }
  } else {
    if (marker) {
      map.panTo([lat, lng], { animate: true });
      marker.openPopup();
      selectedHospitalMarker = marker;
    } else {
      map.panTo([lat, lng], { animate: true });
    }
  }
}

function clearHospitalHighlight() {
  if (selectedHospitalMarker && mapType === 'google') {
    selectedHospitalMarker.setAnimation(null);
  }
  selectedHospitalMarker = null;
}


/* ===========================================================
   REAL STREET-LEVEL ROUTE POLYLINE RENDERING
   =========================================================== */
function drawRouteOnMap(routeData, options = {}) {
  if (!map || !routeData) return;
  clearRouteFromMap();

  const strokeColor = options.color || '#2563eb';
  const rawPoints = routeData.points || [];

  if (mapType === 'google') {
    let path = [];
    if (typeof routeData === 'string') {
      if (google.maps.geometry && google.maps.geometry.encoding) {
        path = google.maps.geometry.encoding.decodePath(routeData);
      }
    } else if (rawPoints.length > 0) {
      path = rawPoints.map(p => new google.maps.LatLng(p.lat, p.lng));
    }

    if (path.length > 0) {
      routePolyline = new google.maps.Polyline({
        path: path,
        geodesic: true,
        strokeColor: strokeColor,
        strokeOpacity: 0.9,
        strokeWeight: 6,
        map: map,
        zIndex: 500,
      });

      const bounds = new google.maps.LatLngBounds();
      path.forEach(pt => bounds.extend(pt));
      map.fitBounds(bounds, { top: 70, right: 70, bottom: 70, left: 70 });
    }
  } else {
    // Leaflet Polyline along actual streets
    let latlngs = [];
    if (rawPoints.length > 0) {
      latlngs = rawPoints.map(p => [p.lat, p.lng]);
    } else if (routeData.origin && routeData.destination) {
      latlngs = [
        [routeData.origin.lat, routeData.origin.lng],
        [routeData.destination.lat, routeData.destination.lng]
      ];
    }

    if (latlngs.length > 0) {
      routeShadowPolyline = L.polyline(latlngs, {
        color: '#1e3a8a',
        weight: 9,
        opacity: 0.45,
        smoothFactor: 1
      }).addTo(map);

      routePolyline = L.polyline(latlngs, {
        color: strokeColor,
        weight: 5,
        opacity: 0.95,
        smoothFactor: 1
      }).addTo(map);

      map.fitBounds(routePolyline.getBounds(), { padding: [50, 50] });
    }
  }
}

function clearRouteFromMap() {
  if (routeShadowPolyline) {
    if (map) map.removeLayer(routeShadowPolyline);
    routeShadowPolyline = null;
  }
  if (routePolyline) {
    if (mapType === 'google') {
      routePolyline.setMap(null);
    } else if (map) {
      map.removeLayer(routePolyline);
    }
    routePolyline = null;
  }
}


/* ===========================================================
   VIEWPORT HELPERS
   =========================================================== */
function fitMapToMarkers() {
  if (!map) return;

  if (mapType === 'google') {
    const bounds = new google.maps.LatLngBounds();
    Object.values(fleetMarkers).forEach(m => bounds.extend(m.getPosition()));
    hospitalMarkers.forEach(m => bounds.extend(m.getPosition()));
    if (!bounds.isEmpty()) {
      map.fitBounds(bounds, { top: 60, right: 60, bottom: 60, left: 60 });
    }
  } else {
    const coords = [];
    Object.values(fleetMarkers).forEach(m => coords.push(m.getLatLng()));
    hospitalMarkers.forEach(m => coords.push(m.getLatLng()));
    if (coords.length > 0) {
      map.fitBounds(L.latLngBounds(coords), { padding: [50, 50] });
    }
  }
}

function recenterMap() {
  if (!window.ambulancePosition || !map) return;
  recenterMapTo(window.ambulancePosition.lat, window.ambulancePosition.lng, 14);
}

function recenterMapTo(lat, lng, zoom = 14) {
  if (!map) return;
  const numLat = Number(lat);
  const numLng = Number(lng);
  if (isNaN(numLat) || isNaN(numLng)) return;

  if (mapType === 'google') {
    map.panTo({ lat: numLat, lng: numLng });
    if (zoom) map.setZoom(zoom);
  } else {
    map.setView([numLat, numLng], zoom, { animate: true });
  }
}


/* ===========================================================
   RIGHT PANEL DETAILS VIEW — RICH LIVE OPERATIONAL TELEMETRY
   =========================================================== */
function showHospitalDetails(hospital) {
  const container = document.getElementById('hospital-detail-content');
  if (!container || !hospital) return;

  const erAvail = hospital.emergency_beds_available ?? (hospital.emergency_bed_available ? 5 : 0);
  const erTot = hospital.emergency_beds_total ?? 20;
  const icuAvail = hospital.icu_beds_available ?? (hospital.icu_available ? 3 : 0);
  const icuTot = hospital.icu_beds_total ?? 8;
  const vAvail = hospital.ventilators_available ?? 3;
  const vTot = hospital.ventilators_total ?? 8;
  const o2Pct = hospital.oxygen_level_pct ?? 96;
  const o2Hours = hospital.oxygen_hours_remaining ?? 60;
  const occupancy = hospital.occupancy_pct ?? Math.round(((erTot - erAvail) / erTot) * 100);
  const divert = hospital.divert_status || 'OPEN';

  const specs = hospital.specialists_on_duty || {
    trauma_surgeon: true,
    cardiologist: true,
    neurologist: true,
    anesthesiologist: true,
    orthopedic_surgeon: true
  };

  const blood = hospital.blood_bank || { O_neg: 4, O_pos: 12, A_pos: 8, B_pos: 10, AB_pos: 5 };
  const facilities = hospital.critical_facilities || {
    cath_lab_active: true,
    ct_scanner_ready: true,
    trauma_bay_ready: true,
    burn_unit_ready: false
  };

  const occColor = occupancy >= 90 ? 'var(--emergency)' : occupancy >= 75 ? 'var(--warning)' : 'var(--success)';
  const divertTag = divert === 'FULL_DIVERT' ? '<span class="detail-divert-badge full">🚨 FULL DIVERSION</span>'
    : divert === 'HEAVY_LOAD' ? '<span class="detail-divert-badge heavy">⚠️ HEAVY EMERGENCY LOAD</span>'
    : '<span class="detail-divert-badge open">🟢 READY &amp; ACCEPTING PATIENTS</span>';

  container.innerHTML = `
    <div class="selected-hospital-card">
      
      <!-- Hospital Header -->
      <div class="hospital-hero">
        <div class="hero-top-row">
          <h3>${escapeHtml(hospital.name)}</h3>
          ${divertTag}
        </div>
        <div class="hospital-address-text">📍 ${escapeHtml(hospital.address || 'Address on record')}</div>
        <div class="telemetry-live-heartbeat">
          <span class="pulse-indicator"></span>
          <span>Live Feed: ${escapeHtml(hospital.telemetry_source || 'HL7 FHIR Live Gateway')}</span>
        </div>
      </div>

      <!-- Distance & ETA Metrics -->
      <div class="metrics-grid">
        <div class="metric-box">
          <span class="metric-label">Street Distance</span>
          <span class="metric-val" style="color:#1565c0;">${hospital.distance_km ? hospital.distance_km.toFixed(1) : '--'} km</span>
        </div>
        <div class="metric-box">
          <span class="metric-label">Road Travel Time</span>
          <span class="metric-val" style="color:#ef6c00;">${hospital.duration_text || '--'}</span>
        </div>
        <div class="metric-box">
          <span class="metric-label">Triage Red Queue</span>
          <span class="metric-val" style="color:#16a34a;">${hospital.er_wait_time_minutes ?? 0} min wait</span>
        </div>
        <div class="metric-box">
          <span class="metric-label">Hospital Rating</span>
          <span class="metric-val" style="color:#ca8a04;">⭐ ${hospital.rating || '4.5'}</span>
        </div>
      </div>

      <!-- Live Capacity Breakdown -->
      <div class="detail-section">
        <div class="section-title-row">
          <h4>🛏️ Live Capacity Telemetry</h4>
          <span class="occupancy-pill" style="background:${occColor}20; color:${occColor}; font-weight:700;">
            ${occupancy}% Ward Load
          </span>
        </div>

        <div class="telemetry-meters-grid">
          <!-- ER Beds Meter -->
          <div class="meter-card">
            <div class="meter-label-row">
              <span>Emergency Beds</span>
              <strong style="color:${erAvail > 0 ? '#16a34a' : '#dc2626'}">${erAvail} / ${erTot} Free</strong>
            </div>
            <div class="capacity-progress-track">
              <div class="capacity-progress-fill" style="width:${Math.round(((erTot - erAvail)/erTot)*100)}%; background:${occColor};"></div>
            </div>
          </div>

          <!-- ICU Beds Meter -->
          <div class="meter-card">
            <div class="meter-label-row">
              <span>ICU &amp; Critical Beds</span>
              <strong style="color:${icuAvail > 0 ? '#16a34a' : '#dc2626'}">${icuAvail} / ${icuTot} Free</strong>
            </div>
            <div class="capacity-progress-track">
              <div class="capacity-progress-fill" style="width:${Math.round(((icuTot - icuAvail)/icuTot)*100)}%; background:${icuAvail > 0 ? '#2563eb' : '#dc2626'};"></div>
            </div>
          </div>

          <!-- Ventilators Meter -->
          <div class="meter-card">
            <div class="meter-label-row">
              <span>Ventilators</span>
              <strong style="color:${vAvail > 0 ? '#16a34a' : '#dc2626'}">${vAvail} / ${vTot} Active</strong>
            </div>
            <div class="capacity-progress-track">
              <div class="capacity-progress-fill" style="width:${Math.round(((vTot - vAvail)/vTot)*100)}%; background:#8b5cf6;"></div>
            </div>
          </div>

          <!-- Oxygen Reserves -->
          <div class="meter-card">
            <div class="meter-label-row">
              <span>Oxygen Reserves</span>
              <strong style="color:#16a34a;">${o2Pct}% (${o2Hours}h)</strong>
            </div>
            <div class="capacity-progress-track">
              <div class="capacity-progress-fill" style="width:${o2Pct}%; background:#10b981;"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- Medical Team On Shift -->
      <div class="detail-section">
        <h4>👨‍⚕️ Emergency Specialists &amp; Crew on Duty</h4>
        <div class="specialists-grid">
          <div class="spec-item ${specs.trauma_surgeon ? 'spec-on' : 'spec-off'}">
            <span>${specs.trauma_surgeon ? '✅' : '❌'} Trauma Surgeon</span>
            <small>${specs.trauma_surgeon ? 'In Resuscitation Bay' : 'Off-Duty'}</small>
          </div>
          <div class="spec-item ${specs.cardiologist ? 'spec-on' : 'spec-off'}">
            <span>${specs.cardiologist ? '✅' : '❌'} Cardiologist</span>
            <small>${specs.cardiologist ? 'On Call (Cath Lab)' : 'Unavailable'}</small>
          </div>
          <div class="spec-item ${specs.neurologist ? 'spec-on' : 'spec-off'}">
            <span>${specs.neurologist ? '✅' : '❌'} Neurosurgeon</span>
            <small>${specs.neurologist ? 'Active on Floor' : 'Unavailable'}</small>
          </div>
          <div class="spec-item ${specs.anesthesiologist ? 'spec-on' : 'spec-off'}">
            <span>${specs.anesthesiologist ? '✅' : '❌'} Anesthesiologist</span>
            <small>${specs.anesthesiologist ? 'On Duty' : 'Unavailable'}</small>
          </div>
        </div>
        <div class="staff-count-row">
          <span>👩‍⚕️ ER Doctors on Shift: <strong>${hospital.er_doctors_count ?? 6}</strong></span>
          <span>🩺 Nursing Staff: <strong>${hospital.nurse_staff_count ?? 18}</strong></span>
        </div>
      </div>

      <!-- Blood Bank & Critical Facilities -->
      <div class="detail-section">
        <h4>🩸 Blood Bank &amp; Critical Care Facilities</h4>
        <div class="blood-bank-pills">
          <span class="blood-pill">O- Universal: <strong>${blood.O_neg ?? 4} units</strong></span>
          <span class="blood-pill">O+: <strong>${blood.O_pos ?? 12} units</strong></span>
          <span class="blood-pill">A+: <strong>${blood.A_pos ?? 8} units</strong></span>
          <span class="blood-pill">B+: <strong>${blood.B_pos ?? 10} units</strong></span>
        </div>

        <div class="facilities-tags">
          <span class="fac-tag ${facilities.cath_lab_active ? 'fac-ready' : 'fac-down'}">
            ${facilities.cath_lab_active ? '✅' : '⚠️'} Cath Lab Ready
          </span>
          <span class="fac-tag ${facilities.ct_scanner_ready ? 'fac-ready' : 'fac-down'}">
            ${facilities.ct_scanner_ready ? '✅' : '⚠️'} CT Scan Active
          </span>
          <span class="fac-tag ${facilities.trauma_bay_ready ? 'fac-ready' : 'fac-down'}">
            ${facilities.trauma_bay_ready ? '✅' : '⚠️'} Trauma Bay 1 &amp; 2
          </span>
        </div>
      </div>

      ${hospital.phone ? `
      <div class="detail-section">
        <h4>Direct Emergency Hotline</h4>
        <a href="tel:${escapeHtml(hospital.phone)}" class="contact-link">📞 ${escapeHtml(hospital.phone)}</a>
      </div>` : ''}

      <!-- Interactive Actions -->
      <div class="detail-interactive-actions">
        <button class="btn-action-sm" onclick="window.HospitalService.simulateAdmissionForSelected()">
          🚨 Simulate Patient Check-in (-1 ER Bed)
        </button>
        <button class="btn-action-sm" onclick="window.HospitalService.toggleHospitalDivertStatus('${hospital.place_id}')">
          🔄 Toggle Divert Status
        </button>
      </div>

      <div class="navigation-actions">
        <button id="start-nav-btn" class="btn-navigate" onclick="startNavigationFlow()">
          🧭 Start Turn-by-Turn Navigation
        </button>
      </div>
    </div>
  `;
}

function startNavigationFlow() {
  const activeUnit = window.LocationTracker?.getActiveUnit();
  const unitName = activeUnit ? activeUnit.callsign : "Ambulance Unit";
  const hospName = window.Dashboard?.selectedHospital ? window.Dashboard.selectedHospital.name : "Destination Hospital";
  if (typeof showToast === 'function') {
    showToast(`🧭 Priority Navigation Active for ${unitName} -> ${hospName}! Turn-by-turn road route locked.`, 'success');
  }
}

function escapeHtml(str) {
  if (!str) return '';
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

// Expose globally
window.initMap = initMap;
window.initLeafletMap = initLeafletMap;
window.updateFleetMarkers = updateFleetMarkers;
window.updateAmbulanceLocation = updateAmbulanceLocation;
window.showHospitalMarkers = showHospitalMarkers;
window.clearHospitalMarkers = clearHospitalMarkers;
window.highlightHospital = highlightHospital;
window.clearHospitalHighlight = clearHospitalHighlight;
window.drawRouteOnMap = drawRouteOnMap;
window.clearRouteFromMap = clearRouteFromMap;
window.showHospitalDetails = showHospitalDetails;
window.fitMapToMarkers = fitMapToMarkers;
window.recenterMap = recenterMap;
window.recenterMapTo = recenterMapTo;