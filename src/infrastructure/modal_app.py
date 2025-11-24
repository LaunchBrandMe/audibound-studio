import modal
import io

# Define the image with Kokoro TTS
image = (
    modal.Image.debian_slim(python_version="3.10")
    .pip_install(
        "kokoro-onnx",  # Kokoro TTS
        "numpy",
        "scipy",
        "fastapi",
        "soundfile",
        "requests"
    )
)

app = modal.App("audibound-kokoro-tts", image=image)

# Use Modal's Volume for model caching
model_volume = modal.Volume.from_name("kokoro-models", create_if_missing=True)

@app.function(
    cpu=2.0,  # Kokoro runs well on CPU
    volumes={"/cache": model_volume},
    timeout=300
)
def generate_audio(text: str, voice: str = "af", speed: float = 1.0):
    """
    Generate audio using Kokoro TTS.
    
    Args:
        text: Text to synthesize
        voice: Voice code (af=American Female, am=American Male, bf=British Female, bm=British Male)
        speed: Speech speed multiplier
    
    Returns:
        bytes: WAV audio data
    """
    print(f"[Kokoro] === Starting generation ===")
    print(f"[Kokoro] Text: {text[:100]}...")
    print(f"[Kokoro] Voice: {voice}, Speed: {speed}")
    
    try:
        from kokoro_onnx import Kokoro
        import numpy as np
        import scipy.io.wavfile
        import os
        
        print("[Kokoro] Initializing TTS model...")
        
        # Model files should be in the cache volume
        model_path = "/cache/kokoro-v1.0.onnx"
        voices_path = "/cache/voices-v1.0.bin"
        
        # Download model files if they don't exist
        if not os.path.exists(model_path) or not os.path.exists(voices_path):
            print("[Kokoro] Downloading model files...")
            import requests
            
            model_url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
            voices_url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
            
            if not os.path.exists(model_path):
                response = requests.get(model_url)
                with open(model_path, 'wb') as f:
                    f.write(response.content)
                print(f"[Kokoro] Downloaded model to {model_path}")
            
            if not os.path.exists(voices_path):
                response = requests.get(voices_url)
                with open(voices_path, 'wb') as f:
                    f.write(response.content)
                print(f"[Kokoro] Downloaded voices to {voices_path}")
        
        # Initialize Kokoro with model files
        kokoro = Kokoro(model_path, voices_path)
        
        print(f"[Kokoro] Model loaded successfully")
        
        # Generate audio
        print(f"[Kokoro] Generating speech with voice: {voice}...")
        
        # Map simple voice codes to full Kokoro voice names
        voice_map = {
            "af": "af_sarah",  # American Female
            "am": "am_adam",   # American Male
            "bf": "bf_emma",   # British Female
            "bm": "bm_george"  # British Male
        }
        kokoro_voice = voice_map.get(voice, "af_sarah")
        
        # Kokoro.create() returns (samples, sample_rate)
        samples, sample_rate = kokoro.create(
            text, 
            voice=kokoro_voice, 
            speed=speed,
            lang="en-us"
        )
        
        # Ensure it's in the right format  (int16 for WAV)
        if samples.dtype != np.int16:
            samples = (samples * 32767).astype(np.int16)
        
        print(f"[Kokoro] Audio generated, shape: {samples.shape}, rate: {sample_rate}Hz")
        
        # Convert to WAV bytes
        print("[Kokoro] Converting to WAV...")
        buffer = io.BytesIO()
        
        scipy.io.wavfile.write(buffer, sample_rate, samples)

        
        audio_bytes = buffer.getvalue()
        
        print(f"[Kokoro] Generated {len(audio_bytes)} bytes")
        print(f"[Kokoro] === Success! ===")
        
        return audio_bytes
        
    except Exception as e:
        print(f"[Kokoro] !!! ERROR: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        raise

@app.function()
@modal.web_endpoint(method="POST")
def generate_speech(item: dict):
    """
    Web endpoint for TTS generation.
    Expects JSON: {"text": "...", "voice": "af", "speed": 1.0}
    """
    from fastapi.responses import Response
    
    print(f"[Endpoint] Received request")
    
    text = item.get("text")
    voice = item.get("voice", "af")  # Default to American Female
    speed = item.get("speed", 1.0)
    
    if not text:
        print("[Endpoint] ERROR: No text provided")
        return {"error": "No text provided"}
    
    try:
        # Call the generate function
        audio_bytes = generate_audio.remote(text, voice, speed)
        print(f"[Endpoint] Success! Returning {len(audio_bytes)} bytes")
        
        # Return as WAV file with proper headers
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={
                "Content-Disposition": "attachment; filename=speech.wav"
            }
        )
    except Exception as e:
        print(f"[Endpoint] Error: {e}")
        import traceback
        traceback.print_exc()
        raise
