from abc import ABC, abstractmethod
import os

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

def get_sfx_provider(provider_type: str = "stock", **kwargs) -> SfxProvider:
    return StockSfxProvider(library_path=kwargs.get("library_path", "assets/sfx"))
