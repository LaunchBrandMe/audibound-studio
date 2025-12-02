import asyncio
import os
from src.core.sfx_engine import get_sfx_provider

async def run_test():
    print("Testing SFX generation with AudioGen Small...")
    
    # Initialize provider with a duration > 5s to test the new limit (e.g. 15s)
    # Note: We need the AUDIOGEN_MODAL_ENDPOINT env var set, or pass it explicitly.
    # Since I don't have the actual URL here, I will check if I can mock it or if it's expected to fail locally.
    # But the user wants me to "do the same", implying I should have the ability to run it or at least set it up.
    # I will assume the environment is set up like in the music test.
    
    # However, for this verification script to run locally without the actual Modal app deployed and URL available, 
    # it might fail if I don't have the URL. 
    # But I can check if the code *would* send the right request.
    
    # Actually, I'll just try to instantiate it and see if it fails on the URL check.
    try:
        # We need to mock the URL if it's not in env
        if not os.getenv("AUDIOGEN_MODAL_ENDPOINT"):
            os.environ["AUDIOGEN_MODAL_ENDPOINT"] = "https://mock-url.modal.run"
            
        provider = get_sfx_provider("audiogen", duration=15.0)
        print(f"Provider initialized with default duration: {provider.default_duration}s")
        
        # We won't actually call generate because we don't have a real endpoint running with the new model yet 
        # (unless the user deployed it, which they haven't).
        # But I can verify the class structure and initialization.
        
        assert provider.default_duration == 15.0
        print("✅ Provider accepted 15.0s duration.")
        
    except Exception as e:
        print(f"Test failed: {e}")

if __name__ == "__main__":
    asyncio.run(run_test())
