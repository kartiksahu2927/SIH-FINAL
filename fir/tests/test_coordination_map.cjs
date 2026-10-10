const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('existing map renderer receives assignments and ignores stale/untrusted messages', () => {
  let listener, drawn = [], cleared = 0;
  const summary = {}, parent = {postMessage() {}};
  const context = {
    location: {origin:'http://localhost:8000'}, parent,
    document: {getElementById() { return summary; }, createElement() { return {}; }},
    L: {circleMarker() { return {addTo() { return this; }, bindPopup() { return this; }, remove() {}}; }},
    window: {initLeafletMap() {}, L: {}, map:{}, drawRouteOnMap(route) { drawn.push(route); }, clearRouteFromMap() { cleared++; }, addEventListener(_, fn) { listener = fn; }},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../mediroute/static/coordination-map.js'), 'utf8'), context);
  const packet = (version, id, name) => ({type:'mediroute:assignment', payload:{emergency:{id:12,state_version:version,hospital_id:id,details:{assignment_version:version,current_route:{available:true,distance_km:5,duration_min:8,route_data_timestamp:String(version),points:[{lat:1,lng:2},{lat:3,lng:4}]}}}, responses:[{hospital_id:id,hospital_name:name,hospital_latitude:3,hospital_longitude:4,status:'ACCEPTED'}],current_location:{latitude:1,longitude:2,source:'GPS'}}});
  listener({origin:context.location.origin,source:parent,data:packet(1,1,'JS Hospital')});
  assert.match(summary.textContent, /JS Hospital/);
  listener({origin:context.location.origin,source:parent,data:packet(2,3,'KS Hospital')});
  assert.match(summary.textContent, /KS Hospital/);
  assert.equal(drawn.length,2);
  listener({origin:context.location.origin,source:parent,data:packet(1,1,'JS Hospital')});
  listener({origin:'http://untrusted.example',source:parent,data:packet(3,1,'Untrusted')});
  assert.match(summary.textContent, /KS Hospital/);
  assert.equal(drawn.length,2);
  const missingGPS = packet(3,3,'KS Hospital'); missingGPS.payload.current_location = null;
  listener({origin:context.location.origin,source:parent,data:missingGPS});
  assert.match(summary.textContent, /unavailable/);
  assert.equal(cleared,1);
});
