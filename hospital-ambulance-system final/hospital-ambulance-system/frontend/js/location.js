/* ============================================================
   location.js — Multi-Ambulance Fleet Telemetry & Address Geocoding Controller
   ============================================================
   Manages:
   - 4 Active Ambulances with live address & status tracking
   - Driver Address Entry & Live Geocoding Search
   - Advanced Geodetic Matrix (UTM/MGRS, Geohash, DMS, HDOP)
   - Real-time road navigation sync
   ============================================================ */

const LocationTracker = {
  activeUnitId: "AMB-101",
  isTracking: false,
  watchId: null,
  positionSource: null,
  lastPositionAt: 0,
  positionRevision: 0,
  maxAgeMs: 120000,
  manualSelection: false,

  // 4 Active Fleet Ambulances
  FLEET: [
    {
      id: "AMB-101",
      callsign: "Unit 101 (ALS)",
      plate: "DL-01-EM-1081",
      type: "Advanced Life Support (ICU on Wheels)",
      crew: "Vikram Singh & Dr. A. Sharma",
      status: "DISPATCHED",
      status_type: "emergency",
      equipment: ["Ventilator", "Defibrillator", "Multipara Monitor", "Oxygen 100%"],
      oxygen: "98%",
      battery: "94%",
      speed_kmh: 48,
      lat: null,
      lng: null,
      location_name: "Location not selected",
      address: "",
      simIndex: 0,
      routePoints: [
      ]
    },
    {
      id: "AMB-102",
      callsign: "Unit 102 (ICU)",
      plate: "DL-04-TC-2042",
      type: "Mobile Cardiac & ICU Unit",
      crew: "Rajesh Kumar & Nurse Priya",
      status: "AVAILABLE",
      status_type: "success",
      equipment: ["12-Lead ECG", "Syringe Pump", "Suction Unit", "Oxygen 95%"],
      oxygen: "95%",
      battery: "88%",
      speed_kmh: 0,
      lat: null,
      lng: null,
      location_name: "Location not selected",
      address: "",
      simIndex: 0,
      routePoints: [
      ]
    },
    {
      id: "AMB-103",
      callsign: "Unit 103 (BLS)",
      plate: "DL-07-BL-5053",
      type: "Basic Life Support & First Response",
      crew: "Manoj Yadav & EMT Rohit",
      status: "PATROL",
      status_type: "info",
      equipment: ["Automated AED", "Trauma Splints", "First Aid Kit", "Oxygen 88%"],
      oxygen: "88%",
      battery: "92%",
      speed_kmh: 32,
      lat: null,
      lng: null,
      location_name: "Location not selected",
      address: "",
      simIndex: 0,
      routePoints: [
      ]
    },
    {
      id: "AMB-104",
      callsign: "Unit 104 (NICU)",
      plate: "DL-09-CC-8084",
      type: "Critical Care & Pediatric Unit",
      crew: "Suresh Verma & Dr. Sneha",
      status: "STANDBY",
      status_type: "warning",
      equipment: ["Infant Incubator", "Blood Gas Analyzer", "Emergency Ventilator"],
      oxygen: "100%",
      battery: "99%",
      speed_kmh: 0,
      lat: null,
      lng: null,
      location_name: "Location not selected",
      address: "",
      simIndex: 0,
      routePoints: [
      ]
    }
  ],

  start() {
    // Fleet seed coordinates are demo configuration, never a hardware GPS fix.
    this.FLEET.forEach(unit => { unit.lat = null; unit.lng = null; unit.address = ''; unit.location_name = 'Location not selected'; });
    this._bindAddressEvents();
    this.useGPS();
  },

  useGPS() {
    this.manualSelection = false;
    this.positionSource = null;
    this.positionRevision++;
    window.Dashboard?.clearNearbyResults('Waiting for a current GPS fix…');
    if (this.watchId !== null) navigator.geolocation.clearWatch(this.watchId);
    if (navigator.geolocation) {
      this.watchId = navigator.geolocation.watchPosition(
        (pos) => this._onHardwareGPS(pos),
        (err) => {
          if (!this.manualSelection) {
            this.positionSource = null;
            this.positionRevision++;
            window.Dashboard?.clearNearbyResults('GPS unavailable. Allow location access, or explicitly select an address/coordinates.');
            this._locationMessage(`GPS unavailable: ${err.message}. Enter a location below; no Mathura fallback is used.`);
          }
        },
        { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 }
      );
      this.isTracking = true;
    } else {
      this._locationMessage('GPS unavailable in this browser. Select a location manually below.');
    }
  },

  _locationMessage(message) {
    const text = document.getElementById('gps-status-text');
    if (text) text.textContent = message;
  },

  setSelectedPosition(lat, lng, label, source = 'MANUAL', timestamp = Date.now(), accuracy = null) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) throw new Error('Enter valid latitude and longitude.');
    const unit = this.getActiveUnit();
    const previous = this.positionSource ? {lat:unit.lat, lng:unit.lng} : null;
    this.manualSelection = source !== 'GPS';
    this.positionSource = source;
    this.lastPositionAt = timestamp;
    this.positionRevision++;
    Object.assign(unit, {lat, lng, address:label, location_name:label, accuracy});
    this._broadcastFleet();
    this._locationMessage(`${source === 'GPS' ? 'Browser GPS' : 'Manually selected location'}: ${lat.toFixed(6)}, ${lng.toFixed(6)}${accuracy != null ? ` (±${Math.round(accuracy)} m)` : ''}`);
    if (!previous || source !== 'GPS') window.recenterMapTo?.(lat, lng, 14);
    if (!previous || this._distanceKm(previous.lat, previous.lng, lat, lng) > 0.1 || source !== 'GPS') window.Dashboard?.recalculateDistancesAndRoute();
  },

  _bindAddressEvents() {
    document.getElementById('btn-use-gps')?.addEventListener('click', () => this.useGPS());
    document.getElementById('btn-use-coordinates')?.addEventListener('click', () => {
      const lat = document.getElementById('selected-latitude').value.trim();
      const lng = document.getElementById('selected-longitude').value.trim();
      try {
        if (!lat || !lng) throw new Error('Enter both latitude and longitude.');
        this.setSelectedPosition(Number(lat), Number(lng), 'Explicitly selected coordinates');
      } catch (error) { this._locationMessage(error.message); }
    });
    const btn = document.getElementById('btn-update-address');
    const input = document.getElementById('amb-address-input');

    if (btn && input) {
      btn.addEventListener('click', () => {
        this.setAmbulanceAddress(input.value.trim());
      });

      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          this.setAmbulanceAddress(input.value.trim());
        }
      });
    }

    document.querySelectorAll('.addr-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const addr = chip.getAttribute('data-addr');
        if (input) input.value = addr;
        this.setAmbulanceAddress(addr);
      });
    });
  },

  async setAmbulanceAddress(addressStr) {
    if (!addressStr) return;
    const results = document.getElementById('address-results');
    if (!results) return;
    results.replaceChildren();
    this._locationMessage('Looking up address. Choose a returned location to confirm it.');
    try {
      const res = await fetch(`/api/location/search?q=${encodeURIComponent(addressStr)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Address lookup failed');
      if (!data.length) { this._locationMessage('Address not found. Try the full address or enter coordinates.'); return; }
      data.forEach(place => {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'btn-sm';
        button.textContent = `${place.display_name} (${place.lat}, ${place.lon})`;
        button.onclick = () => {
          this.setSelectedPosition(Number(place.lat), Number(place.lon), place.display_name);
          results.replaceChildren();
        };
        results.appendChild(button);
      });
    } catch (error) { this._locationMessage(`${error.message}. You can enter coordinates explicitly.`); }
  },

  getActiveUnit() {
    return this.FLEET.find(a => a.id === this.activeUnitId) || this.FLEET[0];
  },

  getAllUnits() {
    return this.FLEET;
  },

  switchAmbulance(unitId) {
    const unit = this.FLEET.find(a => a.id === unitId);
    if (!unit) return;

    this.activeUnitId = unitId;
    this.positionSource = null;
    this.positionRevision++;
    this._locationMessage('Vehicle changed. Use current GPS or select its actual location.');
    window.Dashboard?.clearNearbyResults('Select this ambulance’s actual location.');
    window.ambulancePosition = Number.isFinite(unit.lat) && Number.isFinite(unit.lng)
      ? { lat: unit.lat, lng: unit.lng }
      : null;

    // Update input address box with active unit address
    const input = document.getElementById('amb-address-input');
    if (input) input.value = unit.address || unit.location_name;

    // Broadcast update
    this._broadcastFleet();

    if (Number.isFinite(unit.lat) && Number.isFinite(unit.lng) && typeof window.recenterMapTo === 'function') {
      window.recenterMapTo(unit.lat, unit.lng, 14);
    } else if (Number.isFinite(unit.lat) && Number.isFinite(unit.lng) && window.leafletMap) {
      window.leafletMap.setView([unit.lat, unit.lng], 14, { animate: true });
    }

    if (window.Dashboard) {
      window.Dashboard.onAmbulanceSwitched(unit);
    }

    if (typeof showToast === 'function') {
      showToast(`Switched active control to ${unit.callsign} (${unit.plate})`, 'info');
    }
  },

  getCurrentPosition() {
    const active = this.getActiveUnit();
    if (!this.positionSource || !Number.isFinite(active.lat) || !Number.isFinite(active.lng) || (this.positionSource === 'GPS' && Date.now() - this.lastPositionAt > this.maxAgeMs)) {
      return Promise.reject(new Error('Fresh GPS is unavailable. Allow location access, or explicitly select an address/coordinates.'));
    }
    return Promise.resolve({ lat: active.lat, lng: active.lng, accuracy: active.accuracy, source: this.positionSource });
  },

  simulateStep() {
    this._locationMessage('Use real GPS or explicitly selected coordinates. Simulated movement is disabled for nearby discovery.');
  },

  _onHardwareGPS(pos) {
    if (this.manualSelection) return;
    if (!Number.isFinite(pos.timestamp) || Date.now() - pos.timestamp > this.maxAgeMs || pos.timestamp > Date.now() + 5000) {
      this._locationMessage('GPS fix is stale; waiting for a current fix.');
      return;
    }
    const lat = pos.coords.latitude;
    const lng = pos.coords.longitude;
    const active = this.getActiveUnit();

    active.speed_kmh = Math.round((pos.coords.speed || 0) * 3.6);
    this.setSelectedPosition(lat, lng, 'Current browser GPS', 'GPS', pos.timestamp, pos.coords.accuracy);
  },

  _realignFleetSector(centerLat, centerLng) {
    const offsets = [
      { dLat: 0.0, dLng: 0.0, name: "Primary Emergency Sector Hub" },
      { dLat: 0.015, dLng: 0.012, name: "North Highway Emergency Outpost" },
      { dLat: -0.018, dLng: -0.010, name: "South Corridor First Response Post" },
      { dLat: 0.006, dLng: -0.016, name: "District Base Standby Station" }
    ];

    this.FLEET.forEach((unit, idx) => {
      const off = offsets[idx % offsets.length];
      unit.lat = Number((centerLat + off.dLat).toFixed(6));
      unit.lng = Number((centerLng + off.dLng).toFixed(6));
      unit.location_name = off.name;
      unit.routePoints = this._generateSectorWaypoints(unit.lat, unit.lng);
    });

    if (typeof window.recenterMapTo === 'function') {
      window.recenterMapTo(centerLat, centerLng, 14);
    }
  },

  _generateSectorWaypoints(lat, lng) {
    return [
      { lat: Number(lat.toFixed(6)), lng: Number(lng.toFixed(6)) },
      { lat: Number((lat + 0.004).toFixed(6)), lng: Number((lng + 0.005).toFixed(6)) },
      { lat: Number((lat + 0.008).toFixed(6)), lng: Number((lng + 0.002).toFixed(6)) },
      { lat: Number((lat + 0.003).toFixed(6)), lng: Number((lng - 0.004).toFixed(6)) },
      { lat: Number(lat.toFixed(6)), lng: Number(lng.toFixed(6)) }
    ];
  },

  _distanceKm(lat1, lon1, lat2, lon2) {
    const R = 6371;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  },

  _toDMS(lat, lng) {
    const toDms = (deg, isLat) => {
      const dir = deg >= 0 ? (isLat ? 'N' : 'E') : (isLat ? 'S' : 'W');
      const abs = Math.abs(deg);
      const d = Math.floor(abs);
      const m = Math.floor((abs - d) * 60);
      const s = (((abs - d) * 60 - m) * 60).toFixed(1);
      return `${d}°${m}'${s}"${dir}`;
    };
    return `${toDms(lat, true)} ${toDms(lng, false)}`;
  },

  _computeGeodetics(lat, lng) {
    const dms = this._toDMS(lat, lng);
    // Approximate UTM Zone & coordinates
    const zone = Math.floor((lng + 180) / 6) + 1;
    const easting = Math.floor(500000 + (lng % 6) * 100000);
    const northing = Math.floor(lat * 111320);
    const utm = `${zone}R FL ${String(easting).slice(-4)} ${String(northing).slice(-4)}`;
    
    // Geohash simulation
    const chars = '0123456789bcdefghjkmnpqrstuvwxyz';
    let geohash = 'tsp0';
    for (let i = 0; i < 3; i++) {
      geohash += chars[Math.floor(Math.abs(lat * 100 + lng * 10 + i) % chars.length)];
    }

    return {
      dms,
      utm,
      geohash,
      dop: `${(0.7 + (Math.abs(lat * 10) % 3) * 0.05).toFixed(2)} (Sub-meter DGPS)`
    };
  },

  _broadcastFleet() {
    if (!this.positionSource) return;
    const active = this.getActiveUnit();
    window.ambulancePosition = { lat: active.lat, lng: active.lng };
    window.ambulanceFleet = this.FLEET.filter(unit => Number.isFinite(unit.lat) && Number.isFinite(unit.lng));

    // Update map markers
    if (typeof updateFleetMarkers === 'function') {
      updateFleetMarkers(window.ambulanceFleet, this.activeUnitId);
    } else if (typeof updateAmbulanceLocation === 'function') {
      updateAmbulanceLocation(active.lat, active.lng);
    }

    // Update UI Elements
    this._updateUI(active);

    // Sync to backend
    fetch(`/api/ambulances/${active.id}/location`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        lat: active.lat, 
        lng: active.lng, 
        speed_kmh: active.speed_kmh, 
        status: active.status,
        address: active.address || active.location_name 
      })
    }).catch(() => {});
  },

  _updateUI(unit) {
    const timeEl = document.getElementById('gps-last-updated');
    const emergBtn = document.getElementById('emergency-btn');

    if (timeEl) timeEl.textContent = new Date().toLocaleTimeString();
    if (emergBtn) emergBtn.disabled = false;

    // Update Complex Geodetic Telemetry Matrix
    const geodetics = this._computeGeodetics(unit.lat, unit.lng);
    const utmEl = document.getElementById('telemetry-utm');
    const geohashEl = document.getElementById('telemetry-geohash');
    const dopEl = document.getElementById('telemetry-dop');
    const coordsEl = document.getElementById('telemetry-coords');

    if (utmEl) utmEl.textContent = 'Not supplied by browser';
    if (geohashEl) geohashEl.textContent = 'WGS84';
    if (dopEl) dopEl.textContent = unit.accuracy != null ? `±${Math.round(unit.accuracy)} m` : 'Manual selection';
    if (coordsEl) coordsEl.textContent = geodetics.dms;

    // Update telemetry card details
    const activeName = document.getElementById('active-amb-name');
    const activePlate = document.getElementById('active-amb-plate');
    const activeType = document.getElementById('active-amb-type');
    const activeStatus = document.getElementById('active-amb-status');
    const activeCrew = document.getElementById('active-amb-crew');
    const activeO2 = document.getElementById('active-amb-o2');
    const activeSpeed = document.getElementById('active-amb-speed');

    if (activeName) activeName.textContent = unit.callsign;
    if (activePlate) activePlate.textContent = unit.plate;
    if (activeType) activeType.textContent = unit.type;
    if (activeCrew) activeCrew.textContent = unit.crew;
    if (activeO2) activeO2.textContent = unit.oxygen;
    if (activeSpeed) activeSpeed.textContent = `${unit.speed_kmh} km/h`;

    if (activeStatus) {
      activeStatus.textContent = unit.status;
      activeStatus.className = `status-pill status-${unit.status_type}`;
    }
  },

  _setStatus(state) {
    const dot = document.getElementById('gps-dot');
    const text = document.getElementById('gps-status-text');
    if (!dot || !text) return;
    dot.className = 'status-dot tracking';
    text.textContent = 'Fleet Telemetry Online (4 Units Active)';
  }
};

window.LocationTracker = LocationTracker;

window.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => LocationTracker.start(), 300);
});
