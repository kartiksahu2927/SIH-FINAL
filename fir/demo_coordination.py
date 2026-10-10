"""Repeatable labelled demo using real HTTP APIs and real provider road routes.

Run from fir after enabling MEDIROUTE_DEMO_COORDINATION on the backend.
No fixture routes or fabricated traffic are used by this script.
"""
import argparse
import os
import time
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv


def main():
    load_dotenv('.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--latitude', type=float, default=27.6100)
    parser.add_argument('--longitude', type=float, default=77.6000)
    parser.add_argument('--delay', type=float, default=2)
    parser.add_argument('--complete', action='store_true')
    args = parser.parse_args()
    password = os.getenv('MEDIROUTE_DEMO_PASSWORD', 'MediRoute!2026')
    with httpx.Client(base_url=args.url, timeout=120) as client:
        headers = {}
        for account in ('driver','hospital','ks-hospital'):
            response = client.post('/api/auth/login', json={'email':f'{account}@mediroute.demo','password':password})
            response.raise_for_status()
            headers[account] = {'Authorization':f"Bearer {response.json()['access_token']}"}

        def request(method, path, role='driver', **kwargs):
            response = client.request(method, path, headers=headers[role], **kwargs)
            if response.is_error:
                raise RuntimeError(f'{response.status_code}: {response.text}')
            return response.json()

        print('CONTROLLED DEMO: chosen pickup/ambulance positions and simulated facility capacity; real OSRM routes, no live traffic.')
        emergency = request('POST','/api/emergencies',json={'condition':'Controlled emergency transport demonstration','required_service':'trauma','latitude':args.latitude,'longitude':args.longitude,'demo':True})
        eid = emergency['id']
        print('Emergency ID:',eid,'Notified:',[r['hospital_map_place_id'] for r in emergency['responses']])
        def broadcast():
            request('POST',f'/api/emergencies/{eid}/location',json={'latitude':args.latitude,'longitude':args.longitude,'observed_at':datetime.now(timezone.utc).isoformat(),'source':'DEMO'})
        broadcast()
        request('PATCH',f'/api/emergencies/{eid}/hospital-response','hospital',json={'status':'ACCEPTED'})
        request('PATCH',f'/api/emergencies/{eid}/status',json={'status':'IN_TRANSIT'})
        print('JS accepted; provisional journey started. Waiting for KS response…')
        time.sleep(max(0,args.delay))
        broadcast()
        request('PATCH',f'/api/emergencies/{eid}/hospital-response','ks-hospital',json={'status':'ACCEPTED'})
        result = request('GET',f'/api/emergencies/{eid}/coordination')
        comparison = result['details'].get('last_route_comparison',{})
        for candidate in comparison.get('candidates',[]):
            route = candidate['route']
            print(candidate['hospital_name'], 'road km:',route.get('distance_km'),'minutes:',route.get('duration_min'),'provider:',route.get('provider'))
        print('Decision:',comparison.get('reason',comparison.get('limitation')))
        print('Assignment version:',result['details']['assignment_version'])
        print('Open the Driver dashboard to see the existing map renderer display the current destination.')
        if args.complete:
            request('PATCH',f'/api/emergencies/{eid}/status',json={'status':'ARRIVED'})
            request('PATCH',f'/api/emergencies/{eid}/status',json={'status':'COMPLETED'})
            print('Demo marked arrived and completed.')


if __name__ == '__main__':
    main()
