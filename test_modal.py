#!/usr/bin/env python3
"""Quick test to verify Modal endpoint is working"""

import sys
import os
import requests

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

MODAL_URL = os.getenv("MODAL_URL")

if not MODAL_URL:
    print("ERROR: MODAL_URL not set in .env")
    sys.exit(1)

print(f"Testing Modal endpoint: {MODAL_URL}")

payload = {
    "text": "Hello world, this is a test.",
    "voice": "af",
    "speed": 1.0
}

print(f"Sending request with payload: {payload}")

try:
    response = requests.post(MODAL_URL, json=payload, timeout=60)
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        audio_data = response.content
        print(f"✅ SUCCESS! Received {len(audio_data)} bytes of audio")
        
        # Save to test file
        with open("test_output.wav", "wb") as f:
            f.write(audio_data)
        print("✅ Saved to test_output.wav - try playing it!")
        
    else:
        print(f"❌ ERROR: {response.status_code}")
        print(f"Response: {response.text}")
        
except Exception as e:
    print(f"❌ EXCEPTION: {e}")
