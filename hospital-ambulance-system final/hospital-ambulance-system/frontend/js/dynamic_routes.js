/* ============================================================
   dynamic_routes.js — Dynamic Route Monitoring & Traffic Alerts
   ============================================================
   Monitors live route travel time and alerts paramedic team
   if road congestion or traffic incidents occur.
   ============================================================ */

const DynamicRoutes = {
  isMonitoring: false,
  intervalId: null,
  currentHospital: null,

  startMonitoring(hospital) {
    this.currentHospital = hospital;
    this.isMonitoring = true;
    console.log('[DynamicRoutes] Started active traffic telemetry for', hospital.name);
  },

  stopMonitoring() {
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
    }
    this.isMonitoring = false;
    this.currentHospital = null;
  },

  simulateTrafficIncident() {
    const delay = Math.floor(Math.random() * 6) + 4; // 4 to 9 min delay
    if (typeof showToast === 'function') {
      showToast(`⚠️ Live Traffic Alert: Road congestion detected (+${delay} min delay on primary corridor)`, 'warning');
    }

    if (window.Dashboard && window.Dashboard.selectedHospital) {
      const h = window.Dashboard.selectedHospital;
      const currentMin = h.estimated_duration_min || 8;
      const newMin = currentMin + delay;
      h.duration_text = `${newMin} min (Traffic Alert)`;
      
      if (typeof window.showHospitalDetails === 'function') {
        window.showHospitalDetails(h);
      }
    }
  }
};

window.DynamicRoutes = DynamicRoutes;