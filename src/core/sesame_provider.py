import httpx
import os
from typing import Optional


class SesameProvider:
    """Client for the Modal Sesame CSM endpoint."""
    
    # Available Sesame voices (single model, neutral voice)
    AVAILABLE_VOICES = {
        "sesame:default": "Sesame CSM - Expressive neutral voice"
    }

    def __init__(self, modal_url: str):
        self.modal_url = modal_url
    
    @classmethod
    def get_available_voices(cls):
        """Return dictionary of available Sesame voices."""
        return cls.AVAILABLE_VOICES.copy()

    async def generate_audio(
        self,
        text: str,
        voice_id: str = "default",
        speed: float = 1.0,
        style: Optional[str] = None,
        reference_audio_path: Optional[str] = None  # NEW: Voice cloning
    ) -> bytes:
        import base64
        
        payload = {
            "text": text
        }
        
        # Add reference audio for voice cloning
        if reference_audio_path and os.path.exists(reference_audio_path):
            print(f"[SesameProvider] Loading reference audio: {reference_audio_path}")
            with open(reference_audio_path, 'rb') as f:
                audio_bytes = f.read()
            # Encode as base64 for JSON payload
            payload["voice_sample_bytes"] = base64.b64encode(audio_bytes).decode()
            print(f"[SesameProvider] Reference audio encoded ({len(audio_bytes)} bytes)")
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(self.modal_url, json=payload)
            response.raise_for_status()
            content = response.content
            
            # Validate response
            if len(content) < 100:
                raise ValueError("Sesame endpoint returned too little data")
            if not content.startswith(b"RIFF"):
                raise ValueError("Sesame endpoint did not return valid WAV audio")
            
            return content
