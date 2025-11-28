# Voice Library API Endpoints - Add to main.py after existing endpoints

from fastapi import Form
from typing import Optional

# ==================== VOICE LIBRARY ENDPOINTS ====================

@app.post("/voices/upload")
async def upload_voice(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),  # Comma-separated tags
    engine: str = Form("styletts2")
):
    """Upload a new reference audio file for voice cloning"""
    try:
        # Read file bytes  
        audio_bytes = await file.read()
        
        # Validate file size (max 10MB)
        if len(audio_bytes) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large. Max 10MB.")
        
        # Validate file type
        allowed_extensions = {'.wav', '.mp3', '.m4a'}
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in allowed_extensions:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid file type. Allowed: {', '.join(allowed_extensions)}"
            )
        
        # Parse tags
        tag_list = [t.strip() for t in tags.split(',')] if tags else []
        
        # Use filename as name if not provided
        if not name:
            name = os.path.splitext(file.filename)[0]
        
        # Add to library
        voice_lib = get_voice_library()
        voice_entry = voice_lib.add_voice(
            name=name,
            audio_bytes=audio_bytes,
            filename=file.filename,
            engine=engine,
            tags=tag_list
        )
        
        return {
            "success": True,
            "voice": voice_entry
        }
    
    except Exception as e:
        print(f"Error uploading voice: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/voices")
async def list_voices(query: str = None, tags: str = None):
    """List all voices in library with optional filtering"""
    try:
        voice_lib = get_voice_library()
        
        # Parse tags if provided
        tag_list = [t.strip() for t in tags.split(',')] if tags else None
        
        # Search or get all
        if query or tag_list:
            voices = voice_lib.search_voices(query=query or "", tags=tag_list)
        else:
            voices = voice_lib.get_all_voices()
        
        return {
            "voices": voices,
            "count": len(voices)
        }
    
    except Exception as e:
        print(f"Error listing voices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/voices/{voice_id}")
async def get_voice(voice_id: str):
    """Get details for a specific voice"""
    try:
        voice_lib = get_voice_library()
        voice = voice_lib.get_voice(voice_id)
        
        if not voice:
            raise HTTPException(status_code=404, detail="Voice not found")
        
        return {"voice": voice}
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error getting voice: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/voices/{voice_id}")
async def delete_voice(voice_id: str):
    """Delete a voice from the library"""
    try:
        voice_lib = get_voice_library()
        success = voice_lib.delete_voice(voice_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Voice not found")
        
        return {"success": True, "message": f"Voice {voice_id} deleted"}
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting voice: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/voices/{voice_id}")
async def update_voice(voice_id: str, updates: Dict):
    """Update voice metadata (name, tags, etc.)"""
    try:
        voice_lib = get_voice_library()
        voice = voice_lib.update_voice(voice_id, updates)
        
        if not voice:
            raise HTTPException(status_code=404, detail="Voice not found")
        
        return {"success": True, "voice": voice}
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error updating voice: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/voices/{voice_id}/test")
async def test_voice(voice_id: str, request: Dict):
    """Generate test audio with a cloned voice"""
    try:
        text = request.get("text", "This is a test of the voice cloning system.")
        style = request.get("style")
        
        # Get voice from library
        voice_lib = get_voice_library()
        voice = voice_lib.get_voice(voice_id)
        
        if not voice:
            raise HTTPException(status_code=404, detail="Voice not found")
        
        # Get the appropriate provider
        engine = voice.get("engine", "styletts2")
        reference_path = voice.get("reference_file")
        
        if not os.path.exists(reference_path):
            raise HTTPException(status_code=404, detail="Reference audio file not found")
        
        # Generate audio with voice cloning
        from src.core.voice_engine import get_voice_provider
        
        provider = get_voice_provider(engine, modal_url=os.getenv(f"{engine.upper()}_MODAL_URL"))
        
        audio_bytes = await provider.generate_audio(
            text=text,
            voice_id="default",
            style=style,
            reference_audio_path=reference_path
        )
        
        # Save test audio
        test_dir = os.path.join("outputs", "voice_tests")
        os.makedirs(test_dir, exist_ok=True)
        test_filename = f"test_{voice_id}_{int(datetime.now().timestamp())}.wav"
        test_path = os.path.join(test_dir, test_filename)
        
        with open(test_path, 'wb') as f:
            f.write(audio_bytes)
        
        return {
            "success": True,
            "audio_url": f"/outputs/voice_tests/{test_filename}",
            "text": text
        }
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error testing voice: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
