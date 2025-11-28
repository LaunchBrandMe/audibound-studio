"""
Comprehensive test for voice cloning across all TTS engines.
Tests both basic synthesis and voice cloning for Sesame, StyleTTS2, and IndexTTS2.
"""

import asyncio
import httpx
import base64
import os
from pathlib import Path

# Test configuration
TEST_TEXT = "This is a test of voice cloning technology."
OUTPUT_DIR = Path("outputs/voice_cloning_tests")
REFERENCE_DIR = Path("references")

# Modal endpoints
SESAME_URL = "https://launchbrand-me--audibound-sesame-generate-speech.modal.run"
STYLETTS2_URL = "https://launchbrand-me--audibound-styletts2-generate-speech.modal.run"
INDEXTTS2_URL = "https://launchbrand-me--audibound-indextts2-generate-speech.modal.run"


async def test_engine(engine_name: str, url: str, payload: dict, output_file: str):
    """Test a single TTS engine."""
    print(f"\n{'='*60}")
    print(f"Testing {engine_name}")
    print(f"{'='*60}")
    print(f"Payload keys: {list(payload.keys())}")
    
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            print(f"Sending request to {engine_name}...")
            response = await client.post(url, json=payload)
            print(f"Response status: {response.status_code}")
            
            if response.status_code == 200:
                audio_data = response.content
                print(f"Received {len(audio_data)} bytes")
                
                # Save output
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                output_path = OUTPUT_DIR / output_file
                with open(output_path, 'wb') as f:
                    f.write(audio_data)
                print(f"✅ Saved to: {output_path}")
                return True
            else:
                print(f"❌ Error: {response.text}")
                return False
                
    except Exception as e:
        print(f"❌ Exception: {e}")
        import traceback
        traceback.print_exc()
        return False


async def load_reference_audio(filename: str) -> str:
    """Load reference audio and encode as base64."""
    ref_path = REFERENCE_DIR / filename
    if not ref_path.exists():
        print(f"⚠️  Reference file not found: {ref_path}")
        return None
    
    with open(ref_path, 'rb') as f:
        audio_bytes = f.read()
    
    b64_encoded = base64.b64encode(audio_bytes).decode('utf-8')
    print(f"Loaded reference audio: {filename} ({len(audio_bytes)} bytes)")
    return b64_encoded


async def main():
    print("\n" + "="*60)
    print("VOICE CLONING TEST SUITE")
    print("="*60)
    
    # Find a reference audio file
    if REFERENCE_DIR.exists():
        ref_files = list(REFERENCE_DIR.glob("*.wav")) + list(REFERENCE_DIR.glob("*.mp3"))
        if ref_files:
            ref_audio_b64 = await load_reference_audio(ref_files[0].name)
        else:
            print("⚠️  No reference audio files found in references/")
            ref_audio_b64 = None
    else:
        print("⚠️  No references directory found")
        ref_audio_b64 = None
    
    results = {}
    
    # Test 1: Sesame - Basic
    print("\n\n🎯 TEST 1: Sesame - Basic Synthesis")
    results['sesame_basic'] = await test_engine(
        "Sesame Basic",
        SESAME_URL,
        {"text": TEST_TEXT},
        "sesame_basic.wav"
    )
    
    # Test 2: Sesame - Voice Cloning
    if ref_audio_b64:
        print("\n\n🎯 TEST 2: Sesame - Voice Cloning")
        results['sesame_cloning'] = await test_engine(
            "Sesame Voice Cloning",
            SESAME_URL,
            {
                "text": TEST_TEXT,
                "voice_sample_bytes": ref_audio_b64
            },
            "sesame_cloned.wav"
        )
    
    # Test 3: StyleTTS2 - Basic
    print("\n\n🎯 TEST 3: StyleTTS2 - Basic Synthesis")
    results['styletts2_basic'] = await test_engine(
        "StyleTTS2 Basic",
        STYLETTS2_URL,
        {
            "text": TEST_TEXT,
            "alpha": 0.3,
            "beta": 0.7
        },
        "styletts2_basic.wav"
    )
    
    # Test 4: StyleTTS2 - Voice Cloning
    if ref_audio_b64:
        print("\n\n🎯 TEST 4: StyleTTS2 - Voice Cloning")
        results['styletts2_cloning'] = await test_engine(
            "StyleTTS2 Voice Cloning",
            STYLETTS2_URL,
            {
                "text": TEST_TEXT,
                "alpha": 0.3,
                "beta": 0.7,
                "voice_sample_bytes": ref_audio_b64
            },
            "styletts2_cloned.wav"
        )
    
    # Test 5: IndexTTS2 - Basic
    print("\n\n🎯 TEST 5: IndexTTS2 - Basic Synthesis")
    results['indextts2_basic'] = await test_engine(
        "IndexTTS2 Basic",
        INDEXTTS2_URL,
        {
            "text": TEST_TEXT,
            "emo_vector": [0.2, 0, 0, 0, 0, 0, 0, 0.6],
            "emo_alpha": 0.7
        },
        "indextts2_basic.wav"
    )
    
    # Test 6: IndexTTS2 - Voice Cloning
    if ref_audio_b64:
        print("\n\n🎯 TEST 6: IndexTTS2 - Voice Cloning")
        results['indextts2_cloning'] = await test_engine(
            "IndexTTS2 Voice Cloning",
            INDEXTTS2_URL,
            {
                "text": TEST_TEXT,
                "emo_vector": [0.2, 0, 0, 0, 0, 0, 0, 0.6],
                "emo_alpha": 0.7,
                "voice_sample_b64": ref_audio_b64  # Note: different param name!
            },
            "indextts2_cloned.wav"
        )
    
    # Summary
    print("\n\n" + "="*60)
    print("TEST RESULTS SUMMARY")
    print("="*60)
    for test_name, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{test_name:30s} {status}")
    
    total = len(results)
    passed = sum(results.values())
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if ref_audio_b64:
        print("\n⚠️  MANUAL VERIFICATION REQUIRED:")
        print("   Listen to the *_cloned.wav files and verify they sound")
        print("   different from *_basic.wav and similar to the reference audio.")
    
    print(f"\nOutput directory: {OUTPUT_DIR.absolute()}")


if __name__ == "__main__":
    asyncio.run(main())
