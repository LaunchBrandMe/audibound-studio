#!/usr/bin/env python3
"""
Test script for expression/emotion control in Kokoro TTS
"""
import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from core.voice_engine import KokoroProvider

async def test_expression():
    """Test emotion tags and prosody parameters"""
    
    modal_url = os.getenv("MODAL_URL")
    if not modal_url:
        print("ERROR: MODAL_URL not set")
        return
    
    provider = KokoroProvider(modal_url)
    
    # Test cases
    test_cases = [
        {
            "text": "Hello! How wonderful to see you!",
            "style": "cheerful",
            "description": "Cheerful (should add laugh tags)"
        },
        {
            "text": "I can't believe this is happening.",
            "style": "angry",
            "description": "Angry (should be faster)"
        },
        {
            "text": "It's all my fault...",
            "style": "sad",
            "description": "Sad (should add sigh, slower)"
        },
        {
            "text": "Don't tell anyone about this.",
            "style": "whispering",
            "description": "Whispering (should be slower)"
        },
        {
            "text": "This is just a normal sentence.",
            "style": None,
            "description": "Neutral (no style)"
        }
    ]
    
    print("Testing Expression Control in Kokoro TTS\n")
    print("=" * 60)
    
    for i, test in enumerate(test_cases, 1):
        print(f"\nTest {i}: {test['description']}")
        print(f"Text: \"{test['text']}\"")
        print(f"Style: {test['style']}")
        
        # Process text with emotion tags
        text_with_emotion = provider._add_emotion_tags(test['text'], test['style'])
        prosody = provider._get_prosody_params(test['style'])
        
        print(f"Processed text: \"{text_with_emotion}\"")
        print(f"Prosody: speed={prosody['speed']}")
        
        # Try to generate (but don't fail if Modal is down)
        try:
            audio_bytes = await provider.generate_audio(
                text=test['text'],
                voice_id="af_sarah",
                speed=1.0,
                style=test['style']
            )
            
            # Save to file
            filename = f"/tmp/test_expression_{i}_{test['style'] or 'neutral'}.wav"
            with open(filename, 'wb') as f:
                f.write(audio_bytes)
            
            print(f"✅ Generated: {filename} ({len(audio_bytes)} bytes)")
            
        except Exception as e:
            print(f"⚠️  Generation failed (Modal might be cold): {e}")
    
    print("\n" + "=" * 60)
    print("\nTest complete! Check /tmp/test_expression_*.wav files")

if __name__ == "__main__":
    asyncio.run(test_expression())
