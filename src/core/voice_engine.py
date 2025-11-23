from abc import ABC, abstractmethod
import httpx
import os
from typing import Optional

class VoiceProvider(ABC):
    @abstractmethod
    async def generate_audio(self, text: str, voice_id: str, speed: float = 1.0) -> bytes:
        pass

class KokoroProvider(VoiceProvider):
    def __init__(self, modal_url: str):
        self.modal_url = modal_url

    async def generate_audio(self, text: str, voice_id: str, speed: float = 1.0) -> bytes:
        """
        Calls the Modal.com endpoint to generate audio using Kokoro.
        """
        async with httpx.AsyncClient() as client:
            payload = {
                "text": text,
                "voice": voice_id,
                "speed": speed
            }
            # Modal endpoints often use a simple POST with JSON
            print(f"[VoiceEngine] Requesting audio for voice: {voice_id}...")
            response = await client.post(self.modal_url, json=payload, timeout=60.0)
            print(f"[VoiceEngine] Response Status: {response.status_code}")
            response.raise_for_status()
            content = response.content
            print(f"[VoiceEngine] Received {len(content)} bytes from Modal.")
            return content

class ElevenLabsProvider(VoiceProvider):
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")

    async def generate_audio(self, text: str, voice_id: str, speed: float = 1.0) -> bytes:
        # Placeholder for ElevenLabs implementation
        # Would use their API to get MP3 bytes
        raise NotImplementedError("ElevenLabs provider not yet implemented")

class MockProvider(VoiceProvider):
    """Local mock TTS for testing - generates simple sine wave audio"""
    
    async def generate_audio(self, text: str, voice_id: str, speed: float = 1.0) -> bytes:
        import numpy as np
        import scipy.io.wavfile
        import io
        
        # Generate simple audio based on text length
        # ~150 words per minute = 2.5 words per second = 0.4 seconds per word
        word_count = len(text.split())
        duration_seconds = max(1.0, word_count * 0.4 / speed)
        
        sample_rate = 24000
        
        # Generate a simple sine wave (440 Hz tone)
        t = np.linspace(0, duration_seconds, int(sample_rate * duration_seconds))
        # Mix of frequencies to make it less annoying
        audio = (
            0.3 * np.sin(2 * np.pi * 440 * t) +  # A4
            0.2 * np.sin(2 * np.pi * 554 * t) +  # C#5
            0.1 * np.sin(2 * np.pi * 659 * t)    # E5
        )
        
        # Add envelope (fade in/out)
        fade_samples = int(0.1 * sample_rate)
        audio[:fade_samples] *= np.linspace(0, 1, fade_samples)
        audio[-fade_samples:] *= np.linspace(1, 0, fade_samples)
        
        # Convert to int16
        audio = (audio * 32767).astype(np.int16)
        
        # Write to WAV
        buffer = io.BytesIO()
        scipy.io.wavfile.write(buffer, sample_rate, audio)
        
        return buffer.getvalue()

def get_voice_provider(provider_type: str = "kokoro", **kwargs) -> VoiceProvider:
    if provider_type == "kokoro":
        return KokoroProvider(modal_url=kwargs.get("modal_url"))
    elif provider_type == "elevenlabs":
        return ElevenLabsProvider(api_key=kwargs.get("api_key"))
    elif provider_type == "mock":
        return MockProvider()
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")
