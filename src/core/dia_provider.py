from abc import ABC, abstractmethod
from typing import Optional

import httpx


class VoiceProvider(ABC):
    @abstractmethod
    async def generate_audio(
        self,
        text: str,
        voice_id: str,
        speed: float = 1.0,
        style: Optional[str] = None,
        reference_audio_path: Optional[str] = None,
    ) -> bytes:
        pass


class DiaProvider(VoiceProvider):
    AVAILABLE_VOICES = {
        "dia:default": "Dia 1.6B - Expressive multi-speaker"
    }

    def __init__(self, modal_url: str):
        self.modal_url = modal_url

    @classmethod
    def get_available_voices(cls):
        return cls.AVAILABLE_VOICES.copy()

    def _style_to_hyperparams(self, style: Optional[str], speed: float = 1.0) -> dict:
        """
        Map ABML style to Dia hyperparameters for expressive control.

        Dia parameters:
        - cfg_scale: Classifier-free guidance scale (1.0-5.0, higher = more guided)
        - temperature: Sampling randomness (0.5-1.5, higher = more varied)
        - top_p: Nucleus sampling threshold (0.8-0.99)
        - cfg_filter_top_k: CFG filter top-k (10-50)
        - speed_factor: Speed multiplier (0.5-1.5, higher = faster speech)

        Returns: dict of hyperparameters
        """
        # Base defaults
        # NOTE: Dia has internal limit of audio_length=3072, so max_tokens must stay <=3000
        defaults = {
            "max_new_tokens": 2800,  # Safe limit to stay within Dia's audio_length=3072 constraint
            "cfg_scale": 3.0,
            "temperature": 1.0,
            "top_p": 0.95,
            "cfg_filter_top_k": 30,
            "speed_factor": 0.94 * speed,  # Apply speed multiplier
        }

        if not style:
            return defaults

        style_lower = style.lower()

        # Excited/Happy - higher temperature for expressiveness
        if any(word in style_lower for word in ['excited', 'happy', 'cheerful', 'joyful']):
            return {
                **defaults,
                "cfg_scale": 3.5,
                "temperature": 1.2,
                "top_p": 0.96,
                "speed_factor": 1.1 * speed,  # Faster
            }

        # Angry/Shouting - max guidance for intensity
        elif any(word in style_lower for word in ['angry', 'furious', 'shout', 'yell']):
            return {
                **defaults,
                "cfg_scale": 4.5,
                "temperature": 1.3,
                "top_p": 0.97,
                "cfg_filter_top_k": 40,
                "speed_factor": 1.2 * speed,  # Much faster
            }

        # Sad/Melancholy - lower temperature, slower
        elif any(word in style_lower for word in ['sad', 'melancholy', 'tired', 'weary', 'somber']):
            return {
                **defaults,
                "cfg_scale": 2.5,
                "temperature": 0.8,
                "top_p": 0.93,
                "speed_factor": 0.75 * speed,  # Slower
            }

        # Whisper/Quiet - low guidance, very controlled
        elif any(word in style_lower for word in ['whisper', 'quiet', 'soft', 'calm', 'peaceful']):
            return {
                **defaults,
                "cfg_scale": 2.0,
                "temperature": 0.7,
                "top_p": 0.92,
                "cfg_filter_top_k": 20,
                "speed_factor": 0.7 * speed,  # Much slower
            }

        # Surprised/Shocked - high variety
        elif any(word in style_lower for word in ['surprised', 'shocked', 'astonished', 'amazed']):
            return {
                **defaults,
                "cfg_scale": 3.8,
                "temperature": 1.25,
                "top_p": 0.96,
                "speed_factor": 1.05 * speed,
            }

        # Urgent/Rushed - faster with high guidance
        elif any(word in style_lower for word in ['urgent', 'rushed', 'hurried']):
            return {
                **defaults,
                "cfg_scale": 3.5,
                "temperature": 1.1,
                "speed_factor": 1.25 * speed,  # Very fast
            }

        # Neutral/default
        return defaults

    async def generate_audio(
        self,
        text: str,
        voice_id: str,
        speed: float = 1.0,
        style: Optional[str] = None,
        reference_audio_path: Optional[str] = None,
    ) -> bytes:
        import base64

        # Map style to hyperparameters
        hyperparams = self._style_to_hyperparams(style, speed)

        payload = {
            "text": text,
            "hyperparameters": hyperparams
        }

        if reference_audio_path:
            with open(reference_audio_path, "rb") as f:
                payload["voice_sample_bytes"] = base64.b64encode(f.read()).decode("ascii")

        if style:
            print(f"[Dia] Generating with style '{style}': cfg_scale={hyperparams['cfg_scale']}, "
                  f"temp={hyperparams['temperature']}, speed={hyperparams['speed_factor']:.2f}")
        else:
            print(f"[Dia] Generating neutral speech")

        async with httpx.AsyncClient(timeout=240.0, follow_redirects=True) as client:
            response = await client.post(self.modal_url, json=payload)
            print(f"[Dia] Response Status: {response.status_code}")
            response.raise_for_status()
            content = response.content
            if len(content) < 100:
                print(f"[Dia] Response too small: {len(content)} bytes")
                raise ValueError("Dia endpoint returned too little data")
            if not content.startswith(b"RIFF"):
                print(f"[Dia] WARNING: Response doesn't look like a WAV file")
                raise ValueError("Dia endpoint did not return a WAV file")
            print(f"[Dia] Received {len(content)} bytes")
            return content
