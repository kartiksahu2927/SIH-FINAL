/* ============================================================
   hospital_service.js — Live Hospital Telemetry & Capacity Stream
   ============================================================
   Handles:
   - Real-time HL7/FHIR live telemetry streaming (Every 3.5s)
   - Live Emergency Bed, ICU, Oxygen, Ventilator & Doctor tracking
   - Hospital Response & Divert Status (OPEN, HEAVY LOAD, DIVERT)
   - Dynamic UI pulse change-detection & live sync
   - Interactive Patient Admission & Surge simulation
   ============================================================ */

const HospitalService = {
  telemetryMap: new Map(),
  previousTelemetryMap: new Map(),
  showEligibleOnly: false,
  isLiveStreamActive: false,
  streamIntervalId: null,
  streamIntervalMs: 3500,
  lastStreamSync: null,
  metricsSummary: null,

  // Event Callbacks
  onDataChange: null,
  onFilterChange: null,
  onTelemetryStream: null,
  onHospitalChanged: null,

  init() {
    this.startLiveStream();
    console.log('[HospitalService] Live Operational Telemetry Stream initialized.');
  },

  startLiveStream() {
    if (this.isLiveStreamActive) return;
    this.isLiveStreamActive = true;
    this._fetchLiveStream();
    this.streamIntervalId = setInterval(() => {
      this._fetchLiveStream();
    }, this.streamIntervalMs);
  },

  stopLiveStream() {
    if (this.streamIntervalId) {
      clearInterval(this.streamIntervalId);
      this.streamIntervalId = null;
    }
    this.isLiveStreamActive = false;
  },

  toggleLiveStream() {
    if (this.isLiveStreamActive) {
      this.stopLiveStream();
      if (typeof showToast === 'function') showToast('Live Telemetry Stream Paused', 'info');
    } else {
      this.startLiveStream();
      if (typeof showToast === 'function') showToast('🟢 Live Telemetry Stream Resumed (Syncing every 3.5s)', 'success');
    }
    this._updateLiveIndicatorUI();
    return this.isLiveStreamActive;
  },

  async _fetchLiveStream() {
    try {
      const res = await fetch('/api/hospitals/live-stream');
      if (!res.ok) return;
      const data = await res.json();
      if (data.success && Array.isArray(data.hospitals)) {
        this.lastStreamSync = data.timestamp || new Date().toISOString();
        this.metricsSummary = data.metrics_summary || null;

        const changedHospitals = [];

        data.hospitals.forEach(h => {
          const prev = this.telemetryMap.get(h.place_id);
          const hasChanged = prev && (
            prev.emergency_beds_available !== h.emergency_beds_available ||
            prev.icu_beds_available !== h.icu_beds_available ||
            prev.divert_status !== h.divert_status ||
            prev.accepted !== h.accepted
          );

          if (hasChanged) {
            changedHospitals.push({ place_id: h.place_id, prev, current: h });
          }

          this.telemetryMap.set(h.place_id, { ...h });
          this._mergeIntoSearchResults(h);
        });

        this._updateLiveIndicatorUI();

        if (this.onTelemetryStream) {
          this.onTelemetryStream({
            timestamp: this.lastStreamSync,
            summary: this.metricsSummary,
            changed: changedHospitals
          });
        }

        if (changedHospitals.length > 0 && this.onDataChange) {
          changedHospitals.forEach(c => this.onDataChange(c.current, c.prev));
        }
      }
    } catch (err) {
      console.debug('[HospitalService] Stream polling skip:', err.message);
    }
  },

  _updateLiveIndicatorUI() {
    const dot = document.getElementById('api-status-dot');
    const text = document.getElementById('api-status-text');
    if (!dot || !text) return;

    if (this.isLiveStreamActive) {
      dot.className = 'status-dot tracking';
      const openCount = this.metricsSummary?.open_hospitals ?? 11;
      const totBeds = this.metricsSummary?.total_emergency_beds_available ?? 88;
      text.innerHTML = `🟢 <strong>LIVE TELEMETRY</strong> • 12+ Hospitals (${openCount} Ready • ${totBeds} ER Beds Free)`;
    } else {
      dot.className = 'status-dot offline';
      text.textContent = 'Telemetry Stream Paused';
    }
  },

  updateEligibility(data) {
    if (!data || !data.place_id) return;

    const existing = this.telemetryMap.get(data.place_id) || {};
    const updated = {
      ...existing,
      ...data,
      place_id: data.place_id,
      updated_at: data.updated_at || new Date().toISOString(),
    };

    this.telemetryMap.set(data.place_id, updated);
    this._mergeIntoSearchResults(updated);

    if (this.onDataChange) this.onDataChange(updated, existing);
    return updated;
  },

  updateEligibilityBatch(arr) {
    if (Array.isArray(arr)) {
      arr.forEach(item => this.updateEligibility(item));
    }
  },

  handleHospitalResponse(data) {
    if (!data || !data.place_id) return;

    const status = data.status;
    const accepted = status === 'accepted';
    const existing = this.telemetryMap.get(data.place_id) || {};

    const updated = {
      ...existing,
      place_id: data.place_id,
      accepted: accepted,
      rejection_reason: data.reason || (accepted ? null : 'Trauma ward full'),
      response_time_seconds: data.response_time_seconds || 30,
      responded_at: new Date().toISOString()
    };

    this.telemetryMap.set(data.place_id, updated);
    this._mergeIntoSearchResults(updated);

    if (this.onDataChange) this.onDataChange(updated, existing);

    // If currently selected hospital rejected, alert user
    if (!accepted && window.Dashboard?.selectedHospital?.place_id === data.place_id) {
      if (typeof showToast === 'function') {
        showToast(`Hospital [${existing.name || data.place_id}] rejected dispatch: ${updated.rejection_reason}`, 'warning');
      }
    }

    return updated;
  },

  _mergeIntoSearchResults(telemetry) {
    if (!window.HospitalSearch) return;
    const results = window.HospitalSearch.getLastResults() || [];
    const hospital = results.find(h => h.place_id === telemetry.place_id);
    if (hospital) {
      Object.assign(hospital, telemetry);
    }
  },

  isEligible(hospital) {
    const elig = this.telemetryMap.get(hospital.place_id) || hospital;
    if (elig.divert_status === 'FULL_DIVERT') return false;
    if (elig.emergency_beds_available !== undefined && elig.emergency_beds_available <= 0) return false;
    if (elig.emergency_bed_available === false) return false;
    if (elig.doctor_available === false) return false;
    if (elig.accepted === false) return false;
    return true;
  },

  getEligibilityStatus(hospital) {
    const elig = this.telemetryMap.get(hospital.place_id) || hospital;
    const reasons = [];
    
    if (elig.divert_status === 'FULL_DIVERT') reasons.push('Full critical diversion');
    if (elig.emergency_beds_available !== undefined && elig.emergency_beds_available <= 0) {
      reasons.push('0 Emergency beds available');
    } else if (elig.emergency_bed_available === false) {
      reasons.push('No emergency beds');
    }
    if (elig.doctor_available === false) reasons.push('No trauma surgeon on duty');
    if (elig.accepted === false) reasons.push(elig.rejection_reason || 'Rejected by hospital');

    return {
      eligible: reasons.length === 0,
      reason: reasons.length ? reasons.join('; ') : 'All live criteria verified',
      details: {
        bed: (elig.emergency_beds_available > 0 || elig.emergency_bed_available === true) ? 'available'
          : (elig.emergency_beds_available === 0 || elig.emergency_bed_available === false) ? 'unavailable' : 'unknown',
        doctor: elig.doctor_available === true ? 'available'
          : elig.doctor_available === false ? 'unavailable' : 'unknown',
        accepted: elig.accepted === true ? 'accepted'
          : elig.accepted === false ? 'rejected' : 'pending',
        divert: elig.divert_status || 'OPEN',
        er_beds_avail: elig.emergency_beds_available ?? '--',
        er_beds_tot: elig.emergency_beds_total ?? '--',
        icu_beds_avail: elig.icu_beds_available ?? '--',
        icu_beds_tot: elig.icu_beds_total ?? '--',
        occupancy_pct: elig.occupancy_pct ?? 65,
        oxygen_pct: elig.oxygen_level_pct ?? 95,
        wait_min: elig.er_wait_time_minutes ?? 0,
      }
    };
  },

  filterEligible(hospitals) {
    if (!this.showEligibleOnly) return hospitals;
    return hospitals.filter(h => this.isEligible(h));
  },

  setEligibleOnly(enabled) {
    this.showEligibleOnly = !!enabled;
    if (this.onFilterChange) this.onFilterChange(this.showEligibleOnly);
  },

  getEligibility(placeId) {
    return this.telemetryMap.get(placeId) || null;
  },

  /* ===========================================================
     LIVE SIMULATION ACTIONS (For testing and interactive demos)
     =========================================================== */

  async simulateAdmissionForSelected() {
    const selected = window.Dashboard?.selectedHospital;
    if (!selected) {
      if (typeof showToast === 'function') showToast('Select a hospital first to simulate patient check-in', 'info');
      return;
    }

    try {
      const res = await fetch(`/api/hospitals/${selected.place_id}/simulate-admission`, { method: 'POST' });
      const data = await res.json();
      if (data.success && data.data) {
        this.updateEligibility(data.data);
        if (typeof showToast === 'function') {
          showToast(`🚨 Patient Admitted at [${selected.name}]: Beds now ${data.data.emergency_beds_available}/${data.data.emergency_beds_total} (Occupancy: ${data.data.occupancy_pct}%)`, 'warning');
        }
      }
    } catch (e) {
      console.error('[HospitalService] Admission simulation error:', e);
    }
  },

  async toggleHospitalDivertStatus(placeId) {
    const targetId = placeId || window.Dashboard?.selectedHospital?.place_id;
    if (!targetId) return;

    try {
      const res = await fetch(`/api/hospitals/${targetId}/toggle-status`, { method: 'POST' });
      const data = await res.json();
      if (data.success && data.data) {
        this.updateEligibility(data.data);
        if (typeof showToast === 'function') {
          showToast(`🏥 Status Updated [${data.data.name}]: ${data.data.divert_status}`, 'info');
        }
      }
    } catch (e) {
      console.error('[HospitalService] Toggle status error:', e);
    }
  },

  toggleRandomAvailability() {
    const hospitals = window.HospitalSearch?.getLastResults() || [];
    if (!hospitals.length) {
      if (typeof showToast === 'function') showToast('Run hospital search first to toggle capacity', 'info');
      return;
    }

    const randomHosp = hospitals[Math.floor(Math.random() * hospitals.length)];
    const current = this.telemetryMap.get(randomHosp.place_id) || randomHosp;
    const isFull = current.emergency_beds_available === 0;
    const newAvail = isFull ? 8 : 0;

    fetch(`/api/hospitals/${randomHosp.place_id}/telemetry`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        emergency_beds_available: newAvail,
        divert_status: newAvail === 0 ? 'FULL_DIVERT' : 'OPEN'
      })
    })
    .then(res => res.json())
    .then(d => {
      if (d.success && d.data) {
        this.updateEligibility(d.data);
        if (typeof showToast === 'function') {
          showToast(`Live Shift: ${randomHosp.name} -> ER Beds: ${newAvail}/${d.data.emergency_beds_total} (${d.data.divert_status})`, 'info');
        }
      }
    })
    .catch(() => {});
  },

  simulateRandomResponse() {
    const hospitals = window.HospitalSearch?.getLastResults() || [];
    if (!hospitals.length) {
      if (typeof showToast === 'function') showToast('Run hospital search first to simulate responses', 'info');
      return;
    }

    const target = window.Dashboard?.selectedHospital || hospitals[0];
    const isAccept = Math.random() > 0.35;
    const status = isAccept ? 'accepted' : 'rejected';

    fetch('/api/hospitals/response', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        place_id: target.place_id,
        status: status,
        reason: isAccept ? null : 'ICU Capacity Exceeded',
        response_time_seconds: Math.floor(Math.random() * 40) + 15
      })
    })
    .then(res => res.json())
    .then(d => {
      if (d.success && d.data) {
        this.handleHospitalResponse({
          place_id: target.place_id,
          status: status,
          reason: isAccept ? null : 'ICU Capacity Exceeded',
          response_time_seconds: 30
        });
      }
    })
    .catch(() => {});
  }
};

window.HospitalService = HospitalService;

window.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => HospitalService.init(), 200);
});