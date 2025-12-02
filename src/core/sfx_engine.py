from abc import ABC, abstractmethod
import os
import httpx
import tempfile

class SfxProvider(ABC):
    @abstractmethod
    async def get_sfx(self, description: str, category: str) -> str:
        """
        Returns the path to the SFX file.
        """
        pass

class StockSfxProvider(SfxProvider):
    def __init__(self, library_path: str):
        self.library_path = library_path

    async def get_sfx(self, description: str, category: str) -> str:
        # For MVP, return a placeholder path based on category
        # e.g., assets/sfx/footsteps.mp3
        
        # sanitize description for filename
        safe_desc = "".join(c for c in description if c.isalnum() or c in (' ', '_')).rstrip()
        filename = f"{safe_desc.replace(' ', '_')}.mp3"
        return os.path.join(self.library_path, filename)

class AudioGenProvider(SfxProvider):
    """
    SFX provider using Meta's AudioGen via Modal endpoint.
    """
    def __init__(self, endpoint_url: str, duration: float = 5.0):
        self.endpoint_url = endpoint_url
        self.default_duration = duration

    async def get_sfx(self, description: str, category: str, max_retries: int = 3) -> str:
        """
        Generate SFX using AudioGen and return path to temp file.

        Args:
            description: Text description of the sound effect
            category: Category hint (not used by AudioGen but kept for interface compatibility)
            max_retries: Maximum number of retry attempts

        Returns:
            Path to generated WAV file
        """
        print(f"[AudioGenProvider] Generating SFX: '{description}'")

        last_error = None
        for attempt in range(max_retries):
            try:
                # Call Modal endpoint with longer timeout
                async with httpx.AsyncClient(timeout=900.0) as client:
                    response = await client.post(
                        self.endpoint_url,
                        json={
                            "description": description,
                            "duration": self.default_duration
                        }
                    )
                    response.raise_for_status()
                    audio_bytes = response.content

                # Validate we got actual audio data
                if not audio_bytes or len(audio_bytes) < 100:
                    raise ValueError(f"Received invalid audio data (size: {len(audio_bytes) if audio_bytes else 0} bytes)")

                # Save to temporary file
                temp_file = tempfile.NamedTemporaryFile(
                    suffix='.wav',
                    delete=False,
                    prefix='sfx_'
                )
                temp_file.write(audio_bytes)
                temp_file.close()

                print(f"[AudioGenProvider] Saved SFX to {temp_file.name} ({len(audio_bytes)} bytes)")
                return temp_file.name

            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                    print(f"[AudioGenProvider] Attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                    import asyncio
                    await asyncio.sleep(wait_time)
                else:
                    print(f"[AudioGenProvider] All {max_retries} attempts failed for '{description}'")

        raise RuntimeError(f"Failed to generate SFX after {max_retries} attempts: {last_error}")

class DiaProvider(SfxProvider):
    """
    SFX provider using Dia text-to-audio via Modal endpoint.
    More reliable than AudioGen for sound effects.
    """
    def __init__(self, endpoint_url: str):
        self.endpoint_url = endpoint_url

    async def get_sfx(self, description: str, category: str, max_retries: int = 3) -> str:
        """
        Generate SFX using Dia and return path to temp file.

        Args:
            description: Text description of the sound effect
            category: Category hint (not used by Dia)
            max_retries: Maximum number of retry attempts

        Returns:
            Path to generated WAV file
        """
        print(f"[DiaProvider] Generating SFX: '{description}'")

        last_error = None
        for attempt in range(max_retries):
            try:
                # Call Modal endpoint with longer timeout
                async with httpx.AsyncClient(timeout=900.0) as client:
                    response = await client.post(
                        self.endpoint_url,
                        json={
                            "text": description,
                        }
                    )
                    response.raise_for_status()
                    audio_bytes = response.content

                # Validate we got actual audio data
                if not audio_bytes or len(audio_bytes) < 100:
                    raise ValueError(f"Received invalid audio data (size: {len(audio_bytes) if audio_bytes else 0} bytes)")

                # Save to temporary file
                temp_file = tempfile.NamedTemporaryFile(
                    suffix='.wav',
                    delete=False,
                    prefix='sfx_dia_'
                )
                temp_file.write(audio_bytes)
                temp_file.close()

                print(f"[DiaProvider] Saved SFX to {temp_file.name} ({len(audio_bytes)} bytes)")
                return temp_file.name

            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"[DiaProvider] Attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                    import asyncio
                    await asyncio.sleep(wait_time)
                else:
                    print(f"[DiaProvider] All {max_retries} attempts failed for '{description}'")

        raise RuntimeError(f"Failed to generate SFX after {max_retries} attempts: {last_error}")

def get_sfx_provider(provider_type: str = "stock", **kwargs) -> SfxProvider:
    if provider_type == "audiogen":
        endpoint = kwargs.get("endpoint_url") or os.getenv("AUDIOGEN_MODAL_ENDPOINT")
        if not endpoint:
            raise ValueError("AudioGen endpoint URL not provided")
        return AudioGenProvider(
            endpoint_url=endpoint,
            duration=kwargs.get("duration", 5.0)
        )
    elif provider_type == "dia":
        endpoint = kwargs.get("endpoint_url") or os.getenv("DIA_MODAL_ENDPOINT")
        if not endpoint:
            raise ValueError("Dia endpoint URL not provided")
        return DiaProvider(endpoint_url=endpoint)
    else:
        return StockSfxProvider(library_path=kwargs.get("library_path", "assets/sfx"))

