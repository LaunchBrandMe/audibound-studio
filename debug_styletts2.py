import asyncio
from src.core.styletts2_provider import StyleTTS2Provider

async def test():
    print("Testing StyleTTS2...")
    provider = StyleTTS2Provider(
        modal_url="https://launchbrand-me--audibound-styletts2-generate-speech.modal.run"
    )
    
    print("Generating happy speech...")
    try:
        audio = await provider.generate_audio(
            text="Wow! This is amazing! StyleTTS2 is finally working!",
            voice_id="default",
            style="happy"
        )
        
        with open("styletts2_test_final.wav", "wb") as f:
            f.write(audio)
        
        print(f"✅ Success! Generated {len(audio)} bytes")
        print(f"   Saved to: styletts2_test_final.wav")
    except Exception as e:
        print(f"❌ Failed: {e}")

asyncio.run(test())
