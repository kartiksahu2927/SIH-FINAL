/* ============================================================
   dashboard.js — Main Dashboard UI Orchestrator & Fleet Controller
   ============================================================
   Coordinates:
   - Multi-ambulance prototype fleet selector & active dispatch
   - Real-time GPS acquisition & 12+ hospital network discovery
   - Dynamic hospital listing and eligibility card status
   - Turn-by-turn road network route calculations
   ============================================================ */

const Dashboard = {
  emergencyActive: false,
  selectedHospital: null,
  searchRadiusKm: 30,

  init() {
    this._bindEvents();
    this._bindFleetEvents();
    this._registerHospitalServiceCallbacks();
    this._initSocketIO();
    this._checkUrlParams();
    console.log('[Dashboard] Initialized with Multi-Ambulance Fleet & Real-Time Socket.IO support.');

    // Auto-discover nearby emergency hospitals on startup
    setTimeout(() => {
      this.recalculateDistancesAndRoute();
    }, 600);
  },

  _initSocketIO() {
    try {
      if (typeof io !== 'undefined') {
        this.socket = io();
        console.log('[Socket.IO] Connecting to Real-Time Emergency Gateway...');

        this.socket.on('connect', () => {
          console.log('[Socket.IO] ✅ Connected to Real-Time Telemetry Hub');
          const apiText = document.getElementById('api-status-text');
          if (apiText) {
            apiText.innerHTML = '🟢 <strong>REAL-TIME EMERGENCY GATEWAY</strong> • Live Fleet Sync Active';
          }
        });

        // 1. Dedicated Accepted Event
        this.socket.on('emergency_request_accepted', (data) => {
          console.log('[Socket.IO] ✅ Hospital ACCEPTED request:', data);
          this._stopEmergencyStatusWatcher();
          this._onHospitalAccepted(data);
        });

        // 2. Dedicated Rejected Event
        this.socket.on('emergency_request_rejected', (data) => {
          console.log('[Socket.IO] ❌ Hospital REJECTED request:', data);
          this._stopEmergencyStatusWatcher();
          this._onHospitalRejected(data);
        });

        // 3. Generic Hospital Decision Event
        this.socket.on('hospital_response', (data) => {
          console.log('[Socket.IO] 🏥 Hospital Response Event:', data);
          this._stopEmergencyStatusWatcher();
          const status = String(data?.status || '').toUpperCase();
          if (status === 'ACCEPTED' || status === 'ACCEPT') {
            this._onHospitalAccepted(data);
          } else if (status === 'REJECTED' || status === 'REJECT') {
            this._onHospitalRejected(data);
          }
        });

        // 4. Status Update Event
        this.socket.on('emergency_status_update', (data) => {
          console.log('[Socket.IO] 🔄 Emergency Status Update:', data);
          const status = String(data?.status || '').toUpperCase();
          if (status === 'ACCEPTED') {
            this._stopEmergencyStatusWatcher();
            this._onHospitalAccepted(data);
          } else if (status === 'REJECTED') {
            this._stopEmergencyStatusWatcher();
            this._onHospitalRejected(data);
          }
        });

        // 5. Active emergency sync on connect
        this.socket.on('sync_active_emergency', (data) => {
          if (data && data.status === 'ACCEPTED') {
            this._onHospitalAccepted(data, true);
          }
        });
      }
    } catch (e) {
      console.warn('[Socket.IO] Client init fallback to HTTP REST:', e);
    }
  },

  _startEmergencyStatusWatcher() {
    this._stopEmergencyStatusWatcher();
    console.log('[Dashboard] Starting high-frequency status watcher (400ms interval)...');
    this._statusPollInterval = setInterval(async () => {
      try {
        const res = await fetch('/api/emergency/active-request');
        if (!res.ok) return;
        const json = await res.json();
        if (json.has_active && json.data) {
          const status = String(json.data.status || '').toUpperCase();
          if (status === 'ACCEPTED') {
            console.log('[StatusWatcher] ✅ Detected ACCEPTED status from server!');
            this._stopEmergencyStatusWatcher();
            this._onHospitalAccepted(json.data);
          } else if (status === 'REJECTED') {
            console.log('[StatusWatcher] ❌ Detected REJECTED status from server!');
            this._stopEmergencyStatusWatcher();
            this._onHospitalRejected(json.data);
          }
        }
      } catch (err) {}
    }, 400);
  },

  _stopEmergencyStatusWatcher() {
    if (this._statusPollInterval) {
      clearInterval(this._statusPollInterval);
      this._statusPollInterval = null;
    }
  },

  _onHospitalAccepted(data, isSync = false) {
    this._stopEmergencyStatusWatcher();
    const hospName = data.hospital_name || 'JS Hospital - Demo Hospital';
    const hospId = data.hospital_id || 'JS001';

    if (!isSync) {
      this._playAudioAlert(true);
      this._showToast(`✅ ${hospName} ACCEPTED the emergency request! Road route locked.`, 'success');
    }

    const emergBtn = document.getElementById('emergency-btn');
    if (emergBtn) {
      emergBtn.disabled = false;
      emergBtn.innerHTML = `✅ <span>${hospName} ACCEPTED • EN ROUTE</span>`;
      emergBtn.style.background = 'linear-gradient(135deg, #059669 0%, #047857 100%)';
    }

    this._setEmergencyStatus('accepted');

    // Auto-select JS Hospital on map and calculate real road route
    this.selectHospital(hospId);
  },

  _onHospitalRejected(data) {
    this._stopEmergencyStatusWatcher();
    const hospName = data.hospital_name || 'JS Hospital - Demo Hospital';
    const reason = data.rejection_reason || 'Department at maximum capacity';

    this._playAudioAlert(false);
    this._showToast(`❌ ${hospName} rejected the emergency request (${reason}). Please select an alternate facility.`, 'error');

    this._resetEmergencyButton();
    this._setEmergencyStatus('rejected');
  },

  _playAudioAlert(isSuccess) {
    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) return;
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);

      if (isSuccess) {
        // High-low-high success chime
        osc.frequency.setValueAtTime(587.33, ctx.currentTime); // D5
        osc.frequency.setValueAtTime(880.00, ctx.currentTime + 0.15); // A5
        gain.gain.setValueAtTime(0.3, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.45);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.45);
      } else {
        // Warning low beep
        osc.frequency.setValueAtTime(300, ctx.currentTime);
        osc.frequency.setValueAtTime(220, ctx.currentTime + 0.2);
        gain.gain.setValueAtTime(0.35, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.4);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.4);
      }
    } catch (e) {}
  },

  _checkUrlParams() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('autoEmergency') === 'true' || params.get('emergency') === 'true' || params.get('dispatch') === 'true') {
      setTimeout(() => {
        console.log('[Dashboard] Auto-dispatching emergency patient onboard flow...');
        this.onEmergencyClick();
      }, 800);
    }
  },

  _registerHospitalServiceCallbacks() {
    if (!window.HospitalService) return;

    window.HospitalService.onDataChange = (updated, prev) => {
      this._updateHospitalCardEligibility(updated.place_id, prev);
      if (this.selectedHospital?.place_id === updated.place_id) {
        Object.assign(this.selectedHospital, updated);
        if (typeof window.showHospitalDetails === 'function') {
          window.showHospitalDetails(this.selectedHospital);
        }
      }
    };

    window.HospitalService.onFilterChange = (enabled) => {
      const hospitals = window.HospitalSearch?.getLastResults() || [];
      this._renderHospitalList(hospitals);
    };

    window.HospitalService.onTelemetryStream = (streamData) => {
      const hospitals = window.HospitalSearch?.getLastResults() || [];
      if (hospitals.length > 0) {
        this._renderHospitalList(hospitals);
        if (this.selectedHospital) {
          const fresh = hospitals.find(h => h.place_id === this.selectedHospital.place_id);
          if (fresh) {
            Object.assign(this.selectedHospital, fresh);
            if (typeof window.showHospitalDetails === 'function') {
              window.showHospitalDetails(this.selectedHospital);
            }
          }
        }
      }
    };
  },

  _bindFleetEvents() {
    // Fleet switcher pills
    document.querySelectorAll('.amb-unit-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const unitId = btn.dataset.unitId;
        if (unitId && window.LocationTracker) {
          window.LocationTracker.switchAmbulance(unitId);
        }
      });
    });
  },

  onAmbulanceSwitched(unit) {
    // 1. Update fleet buttons active class
    document.querySelectorAll('.amb-unit-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.unitId === unit.id);
    });

    // 2. Re-calculate hospitals & route from new ambulance position
    if (this.emergencyActive) {
      this.recalculateDistancesAndRoute();
    }
  },

  async recalculateDistancesAndRoute() {
    const active = window.LocationTracker?.getActiveUnit() || { lat: 27.646870, lng: 77.551921 };
    const radiusMeters = this.searchRadiusKm * 1000;
    
    const hospitals = await window.HospitalSearch.searchNearbyHospitals(
      active.lat, active.lng, radiusMeters
    );

    this._renderHospitalList(hospitals);

    if (typeof window.showHospitalMarkers === 'function') {
      window.showHospitalMarkers(hospitals);
    }

    if (this.selectedHospital) {
      // Find matching hospital in new results
      const matching = hospitals.find(h => h.place_id === this.selectedHospital.place_id) || hospitals[0];
      if (matching) {
        this.selectHospital(matching.place_id);
      }
    }
  },

  _bindEvents() {
    // 1. Emergency Button
    const emergBtn = document.getElementById('emergency-btn');
    if (emergBtn) {
      emergBtn.addEventListener('click', () => this.onEmergencyClick());
    }

    // 2. Search Radius Slider
    const radiusInput = document.getElementById('search-radius');
    const radiusVal = document.getElementById('radius-value');
    if (radiusInput) {
      radiusInput.addEventListener('input', (e) => {
        this.searchRadiusKm = parseInt(e.target.value, 10);
        if (radiusVal) radiusVal.textContent = this.searchRadiusKm + ' km';
        if (window.HospitalSearch) {
          window.HospitalSearch.setRadius(this.searchRadiusKm * 1000);
        }
        if (this.emergencyActive) {
          this.recalculateDistancesAndRoute();
        }
      });
    }

    // 3. Eligible Only Checkbox
    const filterCheckbox = document.getElementById('eligible-only-filter');
    if (filterCheckbox) {
      filterCheckbox.addEventListener('change', (e) => {
        if (window.HospitalService) {
          window.HospitalService.setEligibleOnly(e.target.checked);
        }
      });
    }

    // 4. Map Control Buttons
    const recenterBtn = document.getElementById('recenter-map-btn');
    if (recenterBtn) {
      recenterBtn.addEventListener('click', () => {
        if (typeof recenterMap === 'function') recenterMap();
      });
    }

    const fitBtn = document.getElementById('fit-bounds-btn');
    if (fitBtn) {
      fitBtn.addEventListener('click', () => {
        if (typeof fitMapToMarkers === 'function') fitMapToMarkers();
      });
    }

    // 5. Quick Simulation Bar Buttons
    const simGpsBtn = document.getElementById('sim-gps-btn');
    if (simGpsBtn) {
      simGpsBtn.addEventListener('click', () => {
        if (window.LocationTracker) {
          window.LocationTracker.simulateStep();
          if (this.selectedHospital) {
            this.calculateRouteToSelected();
          }
        }
      });
    }

    const simStreamBtn = document.getElementById('sim-stream-btn');
    if (simStreamBtn) {
      simStreamBtn.addEventListener('click', () => {
        if (window.HospitalService) {
          const active = window.HospitalService.toggleLiveStream();
          simStreamBtn.innerHTML = active ? '⏸️ Pause Stream' : '▶️ Resume Stream';
        }
      });
    }

    const simAdmissionBtn = document.getElementById('sim-admission-btn');
    if (simAdmissionBtn) {
      simAdmissionBtn.addEventListener('click', () => {
        if (window.HospitalService) {
          window.HospitalService.simulateAdmissionForSelected();
        }
      });
    }

    const simRespBtn = document.getElementById('sim-response-btn');
    if (simRespBtn) {
      simRespBtn.addEventListener('click', () => {
        if (window.HospitalService) {
          window.HospitalService.simulateRandomResponse();
        }
      });
    }

    const simBedBtn = document.getElementById('sim-bed-btn');
    if (simBedBtn) {
      simBedBtn.addEventListener('click', () => {
        if (window.HospitalService) {
          window.HospitalService.toggleRandomAvailability();
        }
      });
    }

    const simTrafficBtn = document.getElementById('sim-traffic-btn');
    if (simTrafficBtn) {
      simTrafficBtn.addEventListener('click', () => {
        if (window.DynamicRoutes) {
          window.DynamicRoutes.simulateTrafficIncident();
        }
      });
    }
  },

  async onEmergencyClick() {
    const btn = document.getElementById('emergency-btn');
    if (!btn) return;

    let position;
    try {
      btn.disabled = true;
      btn.innerHTML = '📡 <span>Acquiring GPS Telemetry...</span>';
      this._setEmergencyStatus('acquiring');

      position = await window.LocationTracker.getCurrentPosition();
    } catch (err) {
      position = { lat: 27.646870, lng: 77.551921 };
    }

    try {
      btn.innerHTML = '🔍 <span>Scanning Emergency Hospitals & Dispatching...</span>';
      this._setEmergencyStatus('searching');

      const radiusMeters = this.searchRadiusKm * 1000;
      const hospitals = await window.HospitalSearch.searchNearbyHospitals(
        position.lat, position.lng, radiusMeters
      );

      if (!hospitals || hospitals.length === 0) {
        this._showToast(`No hospitals found within ${this.searchRadiusKm} km`, 'warning');
        this._resetEmergencyButton();
        return;
      }

      this._renderHospitalList(hospitals);
      this.emergencyActive = true;

      // Update Map Markers
      if (typeof window.showHospitalMarkers === 'function') {
        window.showHospitalMarkers(hospitals);
      }

      // Priority Target: JS Hospital - Demo Hospital (SIH 2-Device Demo)
      const jsHospital = hospitals.find(h => h.place_id === 'JS001' || h.hospital_id === 'JS001') || hospitals[0];
      const activeUnit = window.LocationTracker?.getActiveUnit() || { id: 'AMB-101', callsign: 'Unit 101 (ALS)' };

      // Calculate real route & ETA to JS Hospital
      let distanceKm = 1.45;
      let etaMin = 4;
      try {
        const routeData = await window.RouteCalculator.calculateRoute(
          { lat: position.lat, lng: position.lng },
          { lat: jsHospital.latitude, lng: jsHospital.longitude }
        );
        if (routeData) {
          distanceKm = routeData.distance_km;
          etaMin = routeData.duration_min;
        }
      } catch (e) {}

      // Real-Time Dispatch Payload
      const requestPayload = {
        ambulance_id: activeUnit.id || 'AMB-101',
        ambulance_callsign: activeUnit.callsign || 'Unit 101 (ALS)',
        hospital_id: jsHospital.place_id || 'JS001',
        hospital_name: jsHospital.name || 'JS Hospital - Demo Hospital',
        patient_name: 'Emergency Patient (Critical Trauma)',
        patient_condition: 'Severe Trauma / Immediate Resuscitation Required',
        distance_km: distanceKm,
        eta_min: etaMin,
        lat: position.lat,
        lng: position.lng
      };

      console.log('[Dashboard] 🚨 Dispatching Emergency Request to JS Hospital (Device 2):', requestPayload);

      // 1. Emit over WebSockets / Socket.IO
      if (this.socket && this.socket.connected) {
        this.socket.emit('emergency_request', requestPayload);
      }

      // 2. Dispatch via REST API
      const res = await fetch('/api/emergency/request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestPayload)
      });
      const resData = await res.json();

      btn.disabled = true;
      btn.innerHTML = '⏳ <span>Waiting for JS Hospital Acceptance...</span>';
      btn.style.background = 'linear-gradient(135deg, #d97706 0%, #b45309 100%)';

      this._setEmergencyStatus('dispatched');
      this._startEmergencyStatusWatcher();
      this._showToast(`🚨 Emergency request sent to JS Hospital (Device 2)! Modal alert popped up on hospital dashboard.`, 'success');

    } catch (err) {
      console.error('[Dashboard] Emergency dispatch exception:', err);
      this._showToast('Hospital dispatch error: ' + err.message, 'error');
      this._resetEmergencyButton();
    }
  },

  _resetEmergencyButton() {
    const btn = document.getElementById('emergency-btn');
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '🚨 <span>Emergency Patient Onboard</span>';
    }
  },

  _setEmergencyStatus(state) {
    const dot = document.getElementById('emergency-dot');
    const text = document.getElementById('emergency-status-text');
    if (!dot || !text) return;

    dot.className = 'status-dot';
    switch (state) {
      case 'acquiring':
        dot.classList.add('unavailable');
        text.textContent = 'Locking Fleet GPS Coordinates...';
        break;
      case 'searching':
        dot.classList.add('unavailable');
        text.textContent = 'Streaming Hospital Telemetry & Bed Capacity...';
        break;
      case 'dispatched':
        dot.classList.add('unavailable');
        text.textContent = '📡 Dispatched to JS Hospital (Device 2) • Waiting for Triage...';
        break;
      case 'accepted':
        dot.classList.add('tracking');
        text.textContent = '✅ JS Hospital ACCEPTED • Navigation & Road Route Locked';
        break;
      case 'rejected':
        dot.classList.add('offline');
        text.textContent = '❌ Request Rejected • Please Select Alternate Facility';
        break;
      case 'results':
        dot.classList.add('tracking');
        text.textContent = '12+ Hospitals Live Sync • Real-Time Route Active';
        break;
      case 'selected':
        dot.classList.add('tracking');
        text.textContent = 'Road Route Locked • Live Telemetry Stream On';
        break;
      default:
        dot.classList.add('offline');
        text.textContent = 'Ready for Emergency Dispatch';
    }
  },

  _renderHospitalList(hospitals) {
    const container = document.getElementById('hospital-list');
    const badge = document.getElementById('hospital-count-badge');
    if (!container) return;

    let filtered = hospitals;
    if (window.HospitalService) {
      filtered = window.HospitalService.filterEligible(hospitals);
    }

    if (badge) {
      badge.textContent = `${filtered.length} Live Sync`;
    }

    const sorted = [...filtered].sort((a, b) =>
      (a.distance_km || 99) - (b.distance_km || 99)
    );

    if (sorted.length === 0) {
      container.innerHTML = `
        <div class="hospital-empty">
          <div class="empty-icon">⚠️</div>
          <p>No hospitals match your current filter or radius settings.</p>
        </div>
      `;
      return;
    }

    let html = '';
    sorted.forEach((h, index) => {
      const isSelected = this.selectedHospital?.place_id === h.place_id;
      const eligibility = window.HospitalService?.getEligibilityStatus(h);
      const isEligible = eligibility?.eligible !== false;
      const divert = h.divert_status || 'OPEN';

      // Occupancy Progress Meter calculation
      const erAvail = h.emergency_beds_available ?? (h.emergency_bed_available ? 5 : 0);
      const erTot = h.emergency_beds_total ?? 20;
      const icuAvail = h.icu_beds_available ?? (h.icu_available ? 3 : 0);
      const icuTot = h.icu_beds_total ?? 8;
      const occupancy = h.occupancy_pct ?? Math.round(((erTot - erAvail) / erTot) * 100);
      
      const occColor = occupancy >= 90 ? 'var(--emergency)' : occupancy >= 75 ? 'var(--warning)' : 'var(--success)';
      const divertTag = divert === 'FULL_DIVERT' ? '<span class="status-tag-divert">FULL DIVERT</span>'
        : divert === 'HEAVY_LOAD' ? '<span class="status-tag-busy">HEAVY LOAD</span>'
        : '<span class="status-tag-open">READY / OPEN</span>';

      html += `
        <div class="hospital-item ${isSelected ? 'selected' : ''} ${!isEligible ? 'ineligible' : ''}"
             data-place-id="${h.place_id}" onclick="window.Dashboard.selectHospital('${h.place_id}')">
          
          <div class="hospital-header">
            <span class="hospital-rank">${index + 1}</span>
            <div class="hospital-name-box">
              <span class="hospital-name" title="${this._escapeHtml(h.name)}">${this._escapeHtml(h.name)}</span>
              <span class="live-stream-badge"><span class="live-pulse-dot"></span> LIVE</span>
            </div>
            ${divertTag}
          </div>
          
          <div class="hospital-meta">
            <span class="hospital-distance">📍 ${h.distance_km ? h.distance_km.toFixed(1) : '--'} km</span>
            <span class="hospital-eta">⏱ ${h.duration_text || '--'}</span>
            <span class="hospital-wait">⏳ Triage: ${h.er_wait_time_minutes ?? 0}m</span>
            <span class="hospital-rating">⭐ ${h.rating || '4.5'}</span>
          </div>

          <!-- Live Bed Capacity Bar -->
          <div class="live-capacity-container">
            <div class="live-capacity-header">
              <span class="capacity-label">Trauma Ward Occupancy</span>
              <span class="capacity-val" style="color:${occColor}; font-weight:700;">${occupancy}% (${erAvail} Beds Free)</span>
            </div>
            <div class="capacity-progress-track">
              <div class="capacity-progress-fill" style="width: ${occupancy}%; background: ${occColor};"></div>
            </div>
          </div>

          <!-- Live Telemetry Badges Grid -->
          <div class="hospital-live-chips">
            <div class="live-chip ${erAvail > 0 ? 'chip-green' : 'chip-red'}">
              <span class="chip-icon">🛏️</span>
              <span class="chip-label">ER:</span>
              <strong>${erAvail}/${erTot}</strong>
            </div>
            <div class="live-chip ${icuAvail > 0 ? 'chip-green' : 'chip-red'}">
              <span class="chip-icon">🫁</span>
              <span class="chip-label">ICU:</span>
              <strong>${icuAvail}/${icuTot}</strong>
            </div>
            <div class="live-chip ${h.doctor_available ? 'chip-green' : 'chip-red'}">
              <span class="chip-icon">👨‍⚕️</span>
              <span class="chip-label">Trauma Dr:</span>
              <strong>${h.doctor_available ? 'On Duty' : 'Off'}</strong>
            </div>
            <div class="live-chip ${h.oxygen_level_pct >= 90 ? 'chip-green' : 'chip-amber'}">
              <span class="chip-icon">💨</span>
              <span class="chip-label">O2:</span>
              <strong>${h.oxygen_level_pct ?? 95}%</strong>
            </div>
          </div>

          <div class="hospital-address">${this._escapeHtml(h.address || 'Address on record')}</div>

          <div class="hospital-actions">
            <button class="btn-select ${isSelected ? 'selected-btn' : ''}" ${!isEligible ? 'disabled' : ''}>
              ${isSelected ? '✓ Selected Destination' : isEligible ? 'Select Hospital' : 'Ineligible'}
            </button>
          </div>
        </div>
      `;
    });

    container.innerHTML = html;
  },

  _updateHospitalCardEligibility(placeId, prev) {
    const hospitals = window.HospitalSearch?.getLastResults() || [];
    this._renderHospitalList(hospitals);

    // If a specific card updated, add flash highlight animation
    const card = document.querySelector(`.hospital-item[data-place-id="${placeId}"]`);
    if (card) {
      card.classList.add('telemetry-flash');
      setTimeout(() => card.classList.remove('telemetry-flash'), 1200);
    }
  },

  selectHospital(placeId) {
    const hospitals = window.HospitalSearch?.getLastResults() || [];
    let hospital = hospitals.find(h => h.place_id === placeId || h.hospital_id === placeId);
    if (!hospital && window.HospitalService) {
      hospital = window.HospitalService.getEligibility(placeId);
    }
    if (!hospital && placeId === 'JS001') {
      hospital = {
        place_id: "JS001",
        hospital_id: "JS001",
        name: "JS Hospital - Demo Hospital",
        type: "Demo Hospital",
        latitude: 27.652000,
        longitude: 77.558000,
        address: "Emergency Expressway Sector 1, SIH Demo Zone",
        phone: "+91 1800 108 0001",
        rating: 5.0,
        emergency_beds_available: 15,
        icu_beds_available: 6,
        doctor_available: true,
        emergency_bed_available: true,
        icu_available: true,
        required_facility_available: true,
        divert_status: "OPEN"
      };
      if (window.HospitalSearch?.lastResults) {
        window.HospitalSearch.lastResults.unshift(hospital);
        this._renderHospitalList(window.HospitalSearch.lastResults);
      }
    }
    if (!hospital && placeId === 'HOSP_KD_MEDICAL') {
      hospital = {
        place_id: "HOSP_KD_MEDICAL",
        name: "K.D. Medical College Hospital & Research Center (Apex Trauma Center)",
        latitude: 27.646870,
        longitude: 77.551921,
        address: "National Highway 44, PO-Chhatikara, Akbarpur, Mathura, Uttar Pradesh 281406",
        phone: "+91 5662 281 100",
        rating: 4.8,
        emergency_beds_available: 14,
        icu_beds_available: 5,
        doctor_available: true,
        emergency_bed_available: true,
        icu_available: true,
        required_facility_available: true,
        divert_status: "OPEN"
      };
      if (window.HospitalSearch?.lastResults) {
        window.HospitalSearch.lastResults.unshift(hospital);
        this._renderHospitalList(window.HospitalSearch.lastResults);
      }
    }
    if (!hospital) {
      console.warn('[Dashboard] Hospital not found with place_id:', placeId);
      return;
    }

    this.selectedHospital = hospital;

    // Update list card UI
    document.querySelectorAll('.hospital-item').forEach(card => {
      const isCardSelected = card.dataset.placeId === placeId;
      card.classList.toggle('selected', isCardSelected);
      const btn = card.querySelector('.btn-select');
      if (btn && this.selectedHospital?.place_id !== placeId) {
        btn.textContent = 'Select Hospital';
        btn.classList.remove('selected-btn');
      } else if (btn && isCardSelected) {
        btn.textContent = '✓ Selected Destination';
        btn.classList.add('selected-btn');
      }
    });

    // Highlight marker on map
    if (typeof window.highlightHospital === 'function') {
      window.highlightHospital(hospital);
    }

    // Show details in right panel immediately
    if (typeof window.showHospitalDetails === 'function') {
      window.showHospitalDetails(hospital);
    }

    // Calculate real street route and trace road path
    this.calculateRouteToSelected();
    this._setEmergencyStatus('selected');
  },

  async calculateRouteToSelected() {
    if (!this.selectedHospital) return;

    const active = window.LocationTracker?.getActiveUnit();
    const origin = (active && active.lat && active.lng)
      ? { lat: Number(active.lat), lng: Number(active.lng) }
      : (window.ambulancePosition || { lat: 27.646870, lng: 77.551921 });

    const destination = {
      lat: Number(this.selectedHospital.latitude),
      lng: Number(this.selectedHospital.longitude)
    };

    console.log('[Dashboard] Calculating route from', origin, 'to destination', destination);

    try {
      const route = await window.RouteCalculator.calculateRoute(origin, destination);
      if (route) {
        this.selectedHospital.distance_km = route.distance_km;
        this.selectedHospital.duration_text = route.duration_text;

        if (typeof window.drawRouteOnMap === 'function') {
          window.drawRouteOnMap(route);
        }

        if (typeof window.showHospitalDetails === 'function') {
          window.showHospitalDetails(this.selectedHospital);
        }
      }
    } catch (e) {
      console.warn('[Dashboard] Route drawing fallback:', e);
    }
  },

  _showToast(message, type = 'info') {
    let container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    const icons = { info: 'ℹ️', success: '✅', warning: '⚠️', error: '❌' };
    toast.innerHTML = `<span>${icons[type] || 'ℹ️'}</span> <div>${message}</div>`;
    
    container.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('toast-fadeout');
      setTimeout(() => toast.remove(), 400);
    }, 4000);
  },

  _escapeHtml(str) {
    if (!str) return '';
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
  }
};

function showToast(msg, type) {
  Dashboard._showToast(msg, type);
}

window.Dashboard = Dashboard;
window.showToast = showToast;

window.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => Dashboard.init(), 400);
});