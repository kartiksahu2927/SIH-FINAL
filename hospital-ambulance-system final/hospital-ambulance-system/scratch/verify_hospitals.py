import requests

r = requests.get('http://127.0.0.1:5000/api/hospitals/nearby')
data = r.json()
print("Nearby Hospitals Count:", len(data['hospitals']))
for h in data['hospitals'][:8]:
    print(f"Rank: {h['name']} | Dist: {h['distance_km']} km | Beds: {h.get('emergency_beds_available')}")
