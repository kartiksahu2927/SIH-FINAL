/* ============================================================
   hospital_dashboard.js — JS Hospital Emergency Triage Controller (Device 2)
   ============================================================
   Provides:
   - Real-time Socket.IO communication with backend & Ambulance Fleet
   - Instant emergency alert modal popup with loud dual-tone audio siren
   - Live two-way Accept / Reject dispatch coordination
   - Dynamic incoming ambulance address & live tracking
   ============================================================ */

const HospitalDashboard = {
  socket: null,
  hospitalId: window.location.pathname.toLowerCase().includes('ks-hospital') ? 'KS001' : 'JS001',
  hospitalName: window.location.pathname.toLowerCase().includes('ks-hospital') ? 'KS Hospital' : 'JS Hospital',
  currentRequest: null,
  requestHistory: [],
  audioCtx: null,
  countdownTimer: null,

  init() {
    this._configureHospitalIdentity();
    this._initAudioContext();
    this._bindDOMEvents();
    this._initSocketIO();
    this._fetchActiveRequest();
    console.log(`[${this.hospitalName}] Emergency Triage Reception Gateway initialized.`);
  },

  _configureHospitalIdentity() {
    const brand = document.getElementById('hospital-brand-name');
    const title = document.getElementById('page-title');
    if (brand) brand.textContent = this.hospitalName;
    if (title) title.textContent = `${this.hospitalName} — Emergency Triage Reception Dashboard`;
    const modalHospital = document.getElementById('modal-hospital-name');
    if (modalHospital) modalHospital.textContent = `${this.hospitalName} - Emergency Center`;
  },

  _initAudioContext() {
    // Unlock AudioContext on initial click or keypress
    const unlockAudio = () => {
      try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!this.audioCtx && AudioCtx) {
          this.audioCtx = new AudioCtx();
        }
        if (this.audioCtx && this.audioCtx.state === 'suspended') {
          this.audioCtx.resume();
        }
      } catch (e) {}
    };

    window.addEventListener('click', unlockAudio, { once: false });
    window.addEventListener('keydown', unlockAudio, { once: false });
    window.addEventListener('touchstart', unlockAudio, { once: false });
  },

  _initSocketIO() {
    try {
      if (typeof io !== 'undefined') {
        // Connect to Socket.IO on port 5000 if loaded on a different port or iframe
        const socketUrl = (window.location.port === '5000' || !window.location.port) 
          ? undefined 
          : `${window.location.protocol}//${window.location.hostname}:5000`;
        
        this.socket = socketUrl ? io(socketUrl, { transports: ['websocket', 'polling'] }) : io();

        this.socket.on('connect', () => {
          console.log('[JS Hospital Socket.IO] Connected to Emergency Gateway');
          this._updateConnectionStatus(true);
        });

        this.socket.on('disconnect', () => {
          console.log('[JS Hospital Socket.IO] Disconnected');
          this._updateConnectionStatus(false);
        });

        // 🚨 REAL-TIME INCOMING EMERGENCY DISPATCH EVENT
        this.socket.on('new_emergency_request', (data) => {
          console.log('[JS Hospital Socket.IO] 🚨 NEW EMERGENCY REQUEST RECEIVED:', data);
          this._handleIncomingRequest(data);
        });

        // Live ambulance GPS / Address movement
        this.socket.on('ambulance_position_changed', (data) => {
          this._updateAmbulanceLivePosition(data);
        });

        // Reset
        this.socket.on('emergency_demo_reset', () => {
          this._closeModal();
          this.currentRequest = null;
          this._renderQueue();
        });

        this.socket.on('emergency_response_window_expired', (data) => {
          const response = data?.hospital_responses?.[this.hospitalId];
          if (!response) return;
          this._handleIncomingRequest(data);
          this._closeModal();
        });

        // Sync on reconnect
        this.socket.on('sync_active_emergency', (data) => {
          if (data && data.status === 'PENDING') {
            this._handleIncomingRequest(data);
          }
        });
      }
    } catch (err) {
      console.warn('[JS Hospital] Socket.IO initialization error:', err);
    }
  },

  async _fetchActiveRequest() {
    try {
      const res = await fetch('/api/emergency/active-request');
      const json = await res.json();
      if (json.has_active && json.data) {
        const response = json.data.hospital_responses?.[this.hospitalId];
        if (json.data.status === 'PENDING' || response?.status === 'PENDING') {
          this._handleIncomingRequest(json.data);
        } else {
          const target = (json.data.hospital_targets || []).find(item => item.hospital_id === this.hospitalId);
          const scoped = target ? { ...json.data, ...target, status: response?.status || json.data.status } : json.data;
          this.currentRequest = scoped;
          this._addToHistory(scoped);
          this._renderQueue();
        }
      }
    } catch (e) {}
  },

  _handleIncomingRequest(req) {
    const target = (req.hospital_targets || []).find(item => item.hospital_id === this.hospitalId);
    if (req.hospital_targets && !target) return;
    const response = req.hospital_responses?.[this.hospitalId];
    if (target && response && response.status !== 'PENDING') {
      const scoped = { ...req, ...target, status: response.status };
      this.currentRequest = scoped;
      this._addToHistory(scoped);
      this._renderQueue();
      return;
    } else if (target) {
      req = { ...req, ...target, hospital_id: this.hospitalId, hospital_name: target.hospital_name };
    }
    this.currentRequest = req;
    this._addToHistory(req);
    this._renderQueue();

    // Play Alert Sound
    this._playEmergencyAlertSound();

    // Populate Modal with exact requirements
    const elHosp = document.getElementById('modal-hospital-name');
    if (elHosp) elHosp.textContent = req.hospital_name || 'JS Hospital - Emergency Center';

    const elPatient = document.getElementById('modal-patient-name');
    if (elPatient) elPatient.textContent = req.patient_name || 'Emergency Patient';

    const elCond = document.getElementById('modal-patient-condition');
    if (elCond) elCond.textContent = req.patient_condition || 'Severe Trauma / Immediate Care';

    const elAmbId = document.getElementById('modal-ambulance-id');
    if (elAmbId) elAmbId.textContent = `${req.ambulance_callsign || req.ambulance_id || 'AMB-101'} (${req.ambulance_plate || 'DL-01-EM-1081'})`;

    const elAmbType = document.getElementById('modal-ambulance-type');
    if (elAmbType) elAmbType.textContent = req.ambulance_type || 'Advanced Life Support (ICU on Wheels)';

    const elDist = document.getElementById('modal-distance');
    if (elDist) elDist.textContent = `${req.distance_km ? req.distance_km.toFixed(1) : '1.5'} km`;

    const elEta = document.getElementById('modal-eta');
    if (elEta) elEta.textContent = `${req.eta_min || '4'} minutes (${req.eta_text || '4 min'})`;

    const elBed = document.getElementById('modal-bed-status');
    if (elBed) elBed.textContent = req.emergency_bed_available !== false ? 'Available (15/20 Free)' : 'Full / Limited';

    const elFac = document.getElementById('modal-facility-status');
    if (elFac) elFac.textContent = req.required_facility_available !== false ? 'Available (Trauma Bay & ICU Ready)' : 'Unavailable';

    const loc = req.ambulance_location || {};
    const address = req.ambulance_address || req.location_name || (loc.lat && loc.lng ? `${loc.lat.toFixed(4)}, ${loc.lng.toFixed(4)}` : 'Live Fleet Telemetry Active');
    const elLoc = document.getElementById('modal-location');
    if (elLoc) elLoc.textContent = address;

    // Reset Modal UI
    const banner = document.getElementById('modal-status-banner');
    const actions = document.getElementById('modal-actions');
    if (banner) {
      banner.className = 'modal-status-banner';
      banner.textContent = '';
    }
    if (actions) actions.style.display = 'grid';
    this._startCountdown(req.response_deadline);

    // Open prominent popup modal
    const overlay = document.getElementById('emergency-modal');
    if (overlay) {
      overlay.classList.remove('hidden');
    }
  },

  async respondToRequest(status) {
    if (!this.currentRequest) return;

    const isAccepted = status === 'ACCEPT' || status === 'ACCEPTED';
    const requestId = this.currentRequest.request_id;
    const reqStatus = isAccepted ? 'ACCEPTED' : 'REJECTED';

    console.log(`[JS Hospital] Sending decision for ${requestId}: ${reqStatus}`);

    // Update modal UI immediately
    const banner = document.getElementById('modal-status-banner');
    const actions = document.getElementById('modal-actions');
    if (actions) actions.style.display = 'none';

    if (banner) {
      banner.className = `modal-status-banner show ${isAccepted ? 'accepted' : 'rejected'}`;
      banner.innerHTML = isAccepted 
        ? `✅ <strong>EMERGENCY REQUEST ACCEPTED</strong><br><small>Ambulance notified. Emergency Trauma Bay #1 Locked.</small>`
        : `❌ <strong>EMERGENCY REQUEST REJECTED</strong><br><small>Ambulance notified to redirect.</small>`;
    }

    // The REST endpoint is authoritative and enforces the server deadline.
    try {
      const res = await fetch('/api/emergency/respond', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          request_id: requestId,
          status: reqStatus,
          hospital_id: this.hospitalId,
          reason: isAccepted ? null : 'Trauma ward at 100% capacity'
        })
      });
      const data = await res.json();
      if (data.data) {
        this.currentRequest = data.data;
        this._addToHistory(data.data);
        this._renderQueue();
      }
      if (res.status === 409) {
        if (banner) banner.innerHTML = '⏱️ <strong>RESPONSE WINDOW EXPIRED</strong><br><small>The server closed this request after 60 seconds.</small>';
        if (actions) actions.style.display = 'none';
      }
    } catch (e) {
      console.error('[JS Hospital] Error posting decision:', e);
    }

    // Close modal after 3 seconds so triage staff can view queue
    setTimeout(() => {
      this._closeModal();
    }, 2800);
  },

  _startCountdown(deadline) {
    if (this.countdownTimer) clearInterval(this.countdownTimer);
    const el = document.getElementById('modal-countdown');
    if (!deadline) return;
    const update = () => {
      const remaining = Math.max(0, Math.ceil((Date.parse(deadline) - Date.now()) / 1000));
      if (el) el.textContent = remaining ? `Response window: ${remaining}s` : 'Response window expired';
      if (!remaining) {
        clearInterval(this.countdownTimer);
        const actions = document.getElementById('modal-actions');
        const banner = document.getElementById('modal-status-banner');
        if (actions) actions.style.display = 'none';
        if (banner) {
          banner.className = 'modal-status-banner show rejected';
          banner.innerHTML = '⏱️ <strong>RESPONSE WINDOW EXPIRED</strong><br><small>No further response is accepted by the server.</small>';
        }
      }
    };
    update();
    this.countdownTimer = setInterval(update, 250);
  },

  _updateAmbulanceLivePosition(data) {
    if (!this.currentRequest) return;
    const locEl = document.getElementById('modal-location');
    if (locEl) {
      if (data.address) {
        locEl.textContent = data.address;
      } else if (data.lat && data.lng) {
        locEl.textContent = `${Number(data.lat).toFixed(4)}, ${Number(data.lng).toFixed(4)} (${data.speed_kmh || 45} km/h)`;
      }
    }
  },

  _closeModal() {
    if (this.countdownTimer) clearInterval(this.countdownTimer);
    const overlay = document.getElementById('emergency-modal');
    if (overlay) {
      overlay.classList.add('hidden');
    }
  },

  _addToHistory(req) {
    const idx = this.requestHistory.findIndex(r => r.request_id === req.request_id);
    if (idx >= 0) {
      this.requestHistory[idx] = req;
    } else {
      this.requestHistory.unshift(req);
    }
  },

  _renderQueue() {
    const container = document.getElementById('incoming-queue');
    if (!container) return;

    if (this.requestHistory.length === 0) {
      container.innerHTML = `
        <div class="queue-empty">
          <div class="queue-empty-icon">📡</div>
          <p><strong>Standing by for incoming emergency requests...</strong></p>
          <small style="color:#64748b;">Ambulance dispatch from field units will pop up automatically here.</small>
        </div>
      `;
      return;
    }

    let html = '';
    this.requestHistory.forEach(req => {
      const isPending = req.status === 'PENDING';
      const isAccepted = req.status === 'ACCEPTED';
      const pillClass = isPending ? 'pill-pending' : isAccepted ? 'pill-accepted' : 'pill-rejected';
      const statusIcon = isPending ? '⏳' : isAccepted ? '✅' : '❌';

      html += `
        <div class="queue-item ${req.status.toLowerCase()}">
          <div class="queue-item-header">
            <div>
              <strong style="color:#fff; font-size:0.95rem;">🚑 ${req.ambulance_callsign || req.ambulance_id || 'AMB-101'}</strong>
              <span style="font-size:0.75rem; color:#94a3b8; margin-left:6px;">${req.ambulance_plate || ''}</span>
            </div>
            <span class="queue-status-pill ${pillClass}">${statusIcon} ${req.status}</span>
          </div>
          
          <div style="font-size:0.82rem; color:#cbd5e1; display:flex; justify-content:space-between;">
            <span>👤 ${req.patient_name || 'Emergency Patient'}</span>
            <span>📍 ${req.ambulance_address || (req.distance_km ? req.distance_km.toFixed(1) + ' km' : '--')} (ETA: ${req.eta_min || '--'}m)</span>
          </div>

          ${isPending ? `
            <div style="display:flex; gap:8px; margin-top:6px;">
              <button onclick="window.HospitalDashboard.respondToRequest('ACCEPT')" 
                      style="flex:1; padding:8px; background:#10b981; color:#fff; border:none; border-radius:6px; font-weight:700; cursor:pointer;">
                ✓ Accept
              </button>
              <button onclick="window.HospitalDashboard.respondToRequest('REJECT')" 
                      style="flex:1; padding:8px; background:transparent; color:#f87171; border:1px solid #ef4444; border-radius:6px; font-weight:700; cursor:pointer;">
                ✕ Reject
              </button>
            </div>
          ` : ''}
        </div>
      `;
    });

    container.innerHTML = html;
  },

  _updateConnectionStatus(isOnline) {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    if (dot && text) {
      if (isOnline) {
        dot.style.background = '#10b981';
        text.textContent = '🟢 Hospital Online (Socket.IO Connected)';
      } else {
        dot.style.background = '#f59e0b';
        text.textContent = '🟡 Reconnecting to Gateway...';
      }
    }
  },

  _playEmergencyAlertSound() {
    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) return;
      const ctx = new AudioContext();
      
      // Emergency two-tone siren
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'triangle';
      osc.connect(gain);
      gain.connect(ctx.destination);

      const now = ctx.currentTime;
      osc.frequency.setValueAtTime(880, now);        // A5
      osc.frequency.setValueAtTime(659.25, now + 0.2); // E5
      osc.frequency.setValueAtTime(880, now + 0.4);    // A5
      osc.frequency.setValueAtTime(659.25, now + 0.6); // E5
      osc.frequency.setValueAtTime(987.77, now + 0.8); // B5

      gain.gain.setValueAtTime(0.4, now);
      gain.gain.exponentialRampToValueAtTime(0.01, now + 1.2);

      osc.start(now);
      osc.stop(now + 1.2);
    } catch (e) {
      console.warn('AudioContext alert tone prevented by browser policy until interaction:', e);
    }
  },

  _bindDOMEvents() {
    // Modal Accept Button
    const acceptBtn = document.getElementById('modal-btn-accept');
    if (acceptBtn) {
      acceptBtn.addEventListener('click', () => this.respondToRequest('ACCEPT'));
    }

    // Modal Reject Button
    const rejectBtn = document.getElementById('modal-btn-reject');
    if (rejectBtn) {
      rejectBtn.addEventListener('click', () => this.respondToRequest('REJECT'));
    }

    // Sound Test Button
    const soundBtn = document.getElementById('btn-sound-test');
    if (soundBtn) {
      soundBtn.addEventListener('click', () => {
        this._playEmergencyAlertSound();
        soundBtn.textContent = '🔊 Siren Active!';
        setTimeout(() => { soundBtn.textContent = '🔊 Test Siren Sound'; }, 1800);
      });
    }

    // Reset Button
    const resetBtn = document.getElementById('btn-demo-reset');
    if (resetBtn) {
      resetBtn.addEventListener('click', async () => {
        await fetch('/api/emergency/reset', { method: 'POST' });
        this.requestHistory = [];
        this.currentRequest = null;
        this._closeModal();
        this._renderQueue();
      });
    }
  }
};

window.HospitalDashboard = HospitalDashboard;

window.addEventListener('DOMContentLoaded', () => {
  HospitalDashboard.init();
});
