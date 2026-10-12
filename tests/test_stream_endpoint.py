import urllib.request
import json
import time

def test():
    req = urllib.request.Request('http://127.0.0.1:8000/rca/incidents')
    with urllib.request.urlopen(req, timeout=5) as resp:
        incidents = json.loads(resp.read().decode('utf-8'))
        print(f"Fetched {len(incidents)} incidents from API")
        if not incidents:
            print("No incidents found")
            return
        
        inc = incidents[0]
        inc_id = inc.get("incident_id")
        print(f"Testing stream for incident #{inc_id}")
        
        stream_payload = json.dumps({'incident_data': inc, 'force_refresh': True}).encode('utf-8')
        stream_req = urllib.request.Request(
            'http://127.0.0.1:8000/rca/analyze/stream',
            data=stream_payload,
            headers={'Content-Type': 'application/json'}
        )
        
        start = time.time()
        with urllib.request.urlopen(stream_req, timeout=15) as sresp:
            print(f"Response status: {sresp.status}, Content-Type: {sresp.headers.get('Content-Type')}")
            full_text = []
            chunk_count = 0
            while True:
                chunk = sresp.read(64)
                if not chunk:
                    break
                chunk_count += 1
                text_part = chunk.decode('utf-8', errors='ignore')
                full_text.append(text_part)
                if chunk_count <= 5:
                    print(f"Chunk #{chunk_count} ({len(chunk)} bytes): {repr(text_part)}")
            
            elapsed = time.time() - start
            combined = "".join(full_text)
            print(f"\nStream completed in {elapsed:.2f}s across {chunk_count} chunks!")
            print(f"Total length: {len(combined)} chars")
            print("First 250 characters of streamed report:")
            print(combined[:250])

if __name__ == "__main__":
    test()
