import ffmpeg
import os
import json
from typing import List, Optional
from src.core.abml import ScriptManifest, Scene, AudioBlock

class AudioAssembler:
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def create_silence(self, duration_ms: int, output_path: str):
        """Generates a silence file of specific duration."""
        duration_sec = duration_ms / 1000.0
        (
            ffmpeg
            .input(f'anullsrc=r=24000:cl=mono', f='lavfi', t=duration_sec)
            .output(output_path)
            .overwrite_output()
            .run(quiet=True)
        )

    def stitch_voice_track(self, blocks: List[AudioBlock], temp_dir: str) -> str:
        """
        Stitches individual voice clips into a single 'Narration Stem'.
        Returns the path to the stitched file.
        """
        # This is a simplified stitching logic. 
        # In a real implementation, we would build a complex filter_complex graph
        # to place audio at exact timestamps.
        # For MVP, we assume sequential concatenation with pauses.
        
        inputs = []
        for block in blocks:
            if block.narration and block.narration.file_path:
                inputs.append(ffmpeg.input(block.narration.file_path))
                # Add a small pause after each block if needed
                # inputs.append(ffmpeg.input(silence_file)) 
        
        output_path = os.path.join(self.output_dir, "stem_narration.mp3")
        
        if not inputs:
            # Create 1 sec silence if no narration
            self.create_silence(1000, output_path)
            return output_path

        print(f"[Assembly] Stitching {len(inputs)} clips...")
        
        try:
            (
                ffmpeg
                .concat(*inputs, v=0, a=1)
                .output(output_path, acodec='libmp3lame', qscale=2)
                .overwrite_output()
                .run(capture_stderr=True)
            )
        except ffmpeg.Error as e:
            print(f"[Assembly] FFmpeg Error: {e.stderr.decode('utf8')}")
            raise e
        return output_path

    def stitch_music_track(self, blocks: List[AudioBlock], total_duration_ms: int, temp_dir: str) -> str:
        """
        Stitches music clips into a single 'Music Stem'.
        """
        output_path = os.path.join(self.output_dir, "stem_music.mp3")
        # For MVP, we will just create silence for the whole duration 
        # unless we have actual logic to loop/fade music.
        # This is a placeholder for the complex music logic.
        self.create_silence(total_duration_ms, output_path)
        return output_path

    def stitch_sfx_track(self, blocks: List[AudioBlock], total_duration_ms: int, temp_dir: str) -> str:
        """
        Stitches SFX clips into a single 'SFX Stem'.
        """
        output_path = os.path.join(self.output_dir, "stem_sfx.mp3")
        self.create_silence(total_duration_ms, output_path)
        return output_path

    def mix_stems_to_m4b(self, narration_path: str, music_path: Optional[str], sfx_path: Optional[str], manifest: ScriptManifest) -> str:
        """
        Mixes the 3 stems into a final M4B and embeds the ABML JSON.
        """
        output_m4b = os.path.join(self.output_dir, f"{manifest.project_id}.m4b")
        metadata_file = os.path.join(self.output_dir, "metadata.txt")

        # 1. Create FFmetadata file (simplified)
        with open(metadata_file, 'w') as f:
            f.write(f";FFMETADATA1\ntitle={manifest.title}\n")

        # 2. Save ABML JSON
        abml_path = os.path.join(self.output_dir, "abml.json")
        with open(abml_path, 'w') as f:
            f.write(manifest.model_dump_json())

        # 3. Mix
        inputs = []
        inputs.append(ffmpeg.input(narration_path))
        
        if music_path and os.path.exists(music_path):
            inputs.append(ffmpeg.input(music_path))
        else:
            # If no music, we don't add it to mix, or we add silence? 
            # For amix filter, it's better to have inputs.
            # But if we just want to mix what we have:
            pass

        if sfx_path and os.path.exists(sfx_path):
            inputs.append(ffmpeg.input(sfx_path))

        # Build the filter graph
        # We want to mix them down to stereo
        # If we have multiple inputs, use amix
        
        if len(inputs) > 1:
            # amix inputs=N:duration=longest
            mixed = ffmpeg.filter(inputs, 'amix', inputs=len(inputs), duration='longest')
        else:
            mixed = inputs[0]

        stream = ffmpeg.output(
            mixed, 
            output_m4b, 
            acodec='aac', 
            strict='experimental',
            **{'metadata:g:0': f"comment={manifest.model_dump_json()}"} 
        )
        
        stream.overwrite_output().run(quiet=True)
        return output_m4b
