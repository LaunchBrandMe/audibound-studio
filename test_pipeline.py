import asyncio
import os
import shutil
from src.core.director import ScriptDirector
from src.core.abml import ScriptManifest
from src.main import run_production_pipeline, projects
from unittest.mock import MagicMock, AsyncMock

# Mock the Voice Provider to avoid needing real Modal/API
async def mock_generate_audio(text, voice_id, speed=1.0):
    # Return 1 second of silence as "audio"
    # In reality, this should be a valid WAV header to satisfy ffmpeg
    # But for simple file existence checks, bytes are enough.
    # However, ffmpeg will fail if it's not a real media file.
    # So we will just copy a dummy wav file if we had one, 
    # or we rely on the fact that our assembler might just check file existence.
    # Let's try to return a very minimal valid WAV header if possible, 
    # or just rely on the assembler to handle it.
    
    # Actually, let's just return bytes. 
    # If FFmpeg fails, we know we need real WAVs.
    return b"RIFF....WAVEfmt ...." 

async def run_test():
    print("--- Starting Integration Test ---")
    
    # 1. Setup Mock Project
    project_id = "test_project_001"
    projects[project_id] = {
        "id": project_id,
        "title": "The Haunted Test",
        "raw_text": "The door creaked open. 'Hello?', he whispered.",
        "status": "created"
    }
    
    # 2. Run Director (Real Gemini call - requires API Key)
    # If no API key, we mock the director output
    if not os.getenv("GOOGLE_API_KEY"):
        print("No Google API Key found. Mocking Director output.")
        # Manually construct a manifest
        from src.core.abml import SeriesBible, Scene, AudioBlock, VoiceLayer
        bible = SeriesBible(project_title="Test", characters=[])
        scene = Scene(
            scene_id="1", 
            setting="Dark room", 
            ambience_description="Quiet",
            blocks=[
                AudioBlock(
                    id="b1", 
                    narration=VoiceLayer(speaker="Hero", text="Hello?", voice_id="af")
                )
            ]
        )
        manifest = ScriptManifest(project_id=project_id, title="Test", bible=bible, scenes=[scene])
        projects[project_id]["manifest"] = manifest
    else:
        # Call the real director logic (synchronously for test)
        # We'll skip this for now to keep the test fast and focused on Pipeline
        pass

    # Ensure we have a manifest
    if "manifest" not in projects[project_id]:
         # Fallback mock if we didn't run real director
        from src.core.abml import SeriesBible, Scene, AudioBlock, VoiceLayer
        bible = SeriesBible(project_title="Test", characters=[])
        scene = Scene(
            scene_id="1", 
            setting="Dark room", 
            ambience_description="Quiet",
            blocks=[
                AudioBlock(
                    id="b1", 
                    narration=VoiceLayer(speaker="Hero", text="Hello?", voice_id="af")
                )
            ]
        )
        manifest = ScriptManifest(project_id=project_id, title="Test", bible=bible, scenes=[scene])
        projects[project_id]["manifest"] = manifest

    # 3. Mock Voice Provider in main.py
    # We need to patch `get_voice_provider` or the provider instance itself.
    # Since `run_production_pipeline` calls `get_voice_provider` inside, 
    # we should mock the return value of `get_voice_provider`.
    
    # For this simple script, we will just monkeypatch the class in the module
    import src.core.voice_engine
    
    class MockProvider:
        async def generate_audio(self, text, voice_id, speed=1.0):
            print(f"  [Mock] Generating audio for: {text}")
            # We need a real WAV file for FFmpeg to work.
            # Let's create a tiny silence wav using ffmpeg itself if possible,
            # or just write a dummy file and hope ffmpeg ignores errors if we use -f lavfi
            return b"MOCK_WAV_BYTES" 

    # Patching
    src.core.voice_engine.get_voice_provider = lambda *args, **kwargs: MockProvider()

    # 4. Run Production Pipeline
    try:
        await run_production_pipeline(project_id)
        print("--- Test Complete: Success ---")
        print(f"Output: {projects[project_id].get('output_path')}")
    except Exception as e:
        print(f"--- Test Failed: {e} ---")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_test())
