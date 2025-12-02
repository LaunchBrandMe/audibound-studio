import ffmpeg
import os
import json
import re
from typing import List, Optional
from src.core.abml import ScriptManifest, Scene, AudioBlock


def _sanitize_filename(title: str) -> str:
    """Sanitize title for use as filename (remove special chars, limit length)."""
    # Remove or replace special characters
    sanitized = re.sub(r'[<>:"/\\|?*]', '', title)
    sanitized = re.sub(r'\s+', '_', sanitized.strip())
    # Limit length to 50 chars
    return sanitized[:50]

def _next_render_suffix(output_dir: str, base_name: str) -> str:
    """Return next sequential suffix like '__01' for project renders."""
    pattern = re.compile(rf"^{re.escape(base_name)}__(\\d+)\\.m4b$")
    max_index = 0
    for name in os.listdir(output_dir):
        match = pattern.match(name)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return f"__{max_index + 1:02d}"

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
    
    def get_track_duration_ms(self, audio_path: str) -> int:
        """
        Get the duration of an audio file in milliseconds using ffprobe.
        
        Args:
            audio_path: Path to the audio file
            
        Returns:
            Duration in milliseconds
        """
        try:
            probe = ffmpeg.probe(audio_path)
            duration = float(probe['format']['duration'])
            return int(duration * 1000)
        except Exception as e:
            print(f"[Assembly] Error getting duration for {audio_path}: {e}")
            return 0


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
        Stitches music clips into a single 'Music Stem' by overlaying them at their timestamps.
        """
        output_path = os.path.join(self.output_dir, "stem_music.mp3")

        # Filter blocks that have music files
        music_blocks = [b for b in blocks if b.music and b.music.file_path and os.path.exists(b.music.file_path)]

        if not music_blocks:
            # No music, create silence
            self.create_silence(total_duration_ms, output_path)
            return output_path

        print(f"[Assembly] Stitching {len(music_blocks)} music clips into stem...")

        # Calculate cumulative timestamps for each music block
        cumulative_time_ms = 0
        music_with_timestamps = []

        for block in blocks:
            # If this block has narration, get its duration
            if block.narration and block.narration.file_path and os.path.exists(block.narration.file_path):
                duration = self.get_track_duration_ms(block.narration.file_path)
                block.start_time_ms = cumulative_time_ms
                cumulative_time_ms += duration

            # If this block has music, record its timestamp
            if block.music and block.music.file_path and os.path.exists(block.music.file_path):
                # Use block.start_time_ms if it exists and is not None, otherwise use cumulative_time_ms
                start_ms = getattr(block, 'start_time_ms', None)
                music_with_timestamps.append({
                    'file': block.music.file_path,
                    'start_ms': start_ms if start_ms is not None else cumulative_time_ms
                })

        # Build ffmpeg filter to overlay all music at their timestamps
        # Start with silence as base
        base = ffmpeg.input(f'anullsrc=r=24000:cl=stereo', f='lavfi', t=total_duration_ms/1000.0)

        # Load all music inputs
        music_inputs = [ffmpeg.input(m['file']) for m in music_with_timestamps]

        # Build overlay chain with delays
        filter_chain = []
        for i, music_data in enumerate(music_with_timestamps):
            delay_ms = music_data['start_ms']
            # Music is typically stereo
            try:
                probe = ffmpeg.probe(music_data['file'])
                channels = probe['streams'][0].get('channels')

                if channels == 1:
                    # Mono: single delay value
                    delayed = music_inputs[i].filter('adelay', f'{int(delay_ms)}')
                else:
                    # Stereo: pipe-separated delay values
                    delayed = music_inputs[i].filter('adelay', f'{int(delay_ms)}|{int(delay_ms)}')

                filter_chain.append(delayed)
            except Exception as probe_error:
                print(f"[Assembly] Warning: Could not probe music file {music_data['file']}: {probe_error}")
                # Default to stereo delay
                delayed = music_inputs[i].filter('adelay', f'{int(delay_ms)}|{int(delay_ms)}')
                filter_chain.append(delayed)

        if not filter_chain:
            # No valid music to mix, return silence
            self.create_silence(total_duration_ms, output_path)
            return output_path

        # Mix base silence with all delayed music
        all_inputs = [base] + filter_chain
        mixed = ffmpeg.filter(all_inputs, 'amix', inputs=len(all_inputs), duration='longest', normalize=0)

        try:
            stream = ffmpeg.output(mixed, output_path, acodec='libmp3lame', qscale=2)
            stream.overwrite_output().run(capture_stderr=True, quiet=False)
        except ffmpeg.Error as e:
            error_msg = e.stderr.decode('utf8') if e.stderr else 'Unknown error'
            print(f"[Assembly] FFmpeg error stitching music: {error_msg}")
            # Fallback to silence
            self.create_silence(total_duration_ms, output_path)

        return output_path

    def stitch_sfx_track(self, blocks: List[AudioBlock], total_duration_ms: int, temp_dir: str) -> str:
        """
        Stitches SFX clips into a single 'SFX Stem' by overlaying them at their timestamps.
        """
        output_path = os.path.join(self.output_dir, "stem_sfx.mp3")
        
        # Filter blocks that have SFX files
        sfx_blocks = [b for b in blocks if b.sfx and b.sfx.file_path and os.path.exists(b.sfx.file_path)]
        
        if not sfx_blocks:
            # No SFX, create silence
            self.create_silence(total_duration_ms, output_path)
            return output_path
        
        print(f"[Assembly] Stitching {len(sfx_blocks)} SFX clips into stem...")
        
        # Calculate cumulative timestamps  for each SFX block
        # We'll use the cumulative duration of all previous narration blocks
        cumulative_time_ms = 0
        sfx_with_timestamps = []
        
        for block in blocks:
            # If this block has narration, get its duration
            if block.narration and block.narration.file_path and os.path.exists(block.narration.file_path):
                duration = self.get_track_duration_ms(block.narration.file_path)
                block.start_time_ms = cumulative_time_ms
                cumulative_time_ms += duration
            
            # If this block has SFX, record its timestamp
            if block.sfx and block.sfx.file_path and os.path.exists(block.sfx.file_path):
                # Use block.start_time_ms if it exists and is not None, otherwise use cumulative_time_ms
                start_ms = getattr(block, 'start_time_ms', None)
                sfx_with_timestamps.append({
                    'file': block.sfx.file_path,
                    'start_ms': start_ms if start_ms is not None else cumulative_time_ms
                })
        
        # Build ffmpeg filter to overlay all SFX at their timestamps
        # Start with silence as base
        base = ffmpeg.input(f'anullsrc=r=24000:cl=mono', f='lavfi', t=total_duration_ms/1000.0)
        
        # Load all SFX inputs
        sfx_inputs = [ffmpeg.input(sfx['file']) for sfx in sfx_with_timestamps]
        
        # Build overlay chain
        # For simplicity, we'll use amix with multiple inputs
        # Each SFX will be delayed by its timestamp
        filter_chain = []
        for i, sfx_data in enumerate(sfx_with_timestamps):
            delay_ms = sfx_data['start_ms']
            # Use adelay filter to delay the SFX
            # Format: delays (just one value for mono, pipe-separated for stereo)
            # We need to probe the input to check if it's mono or stereo
            try:
                probe = ffmpeg.probe(sfx_data['file'])
                channels = int(probe['streams'][0].get('channels', 1))

                if channels == 1:
                    # Mono: single delay value
                    delayed = sfx_inputs[i].filter('adelay', f'{int(delay_ms)}')
                else:
                    # Stereo: pipe-separated delay values
                    delayed = sfx_inputs[i].filter('adelay', f'{int(delay_ms)}|{int(delay_ms)}')

                filter_chain.append(delayed)
            except Exception as probe_error:
                print(f"[Assembly] Warning: Could not probe SFX file {sfx_data['file']}: {probe_error}")
                # Default to mono delay
                delayed = sfx_inputs[i].filter('adelay', f'{int(delay_ms)}')
                filter_chain.append(delayed)

        if not filter_chain:
            # No valid SFX to mix, return silence
            self.create_silence(total_duration_ms, output_path)
            return output_path

        # Mix base silence with all delayed SFX
        all_inputs = [base] + filter_chain
        mixed = ffmpeg.filter(all_inputs, 'amix', inputs=len(all_inputs), duration='longest', normalize=0)

        try:
            stream = ffmpeg.output(mixed, output_path, acodec='libmp3lame', qscale=2)
            stream.overwrite_output().run(capture_stderr=True, quiet=False)
        except ffmpeg.Error as e:
            error_msg = e.stderr.decode('utf8') if e.stderr else 'Unknown error'
            print(f"[Assembly] FFmpeg SFX stitching error: {error_msg}")
            # Fallback to silence
            self.create_silence(total_duration_ms, output_path)
        
        return output_path

    def mix_stems_to_m4b(
        self,
        narration_path: str,
        music_path: Optional[str],
        sfx_path: Optional[str],
        manifest: ScriptManifest,
        engine_tag: Optional[str] = None,
        layers_label: Optional[str] = None,
    ) -> str:
        """
        Mixes the 3 stems into a final M4B and embeds the ABML JSON.
        """
        # Use sanitized title for filename instead of project_id
        base_name = _sanitize_filename(manifest.title)
        if layers_label:
            base_name = f"{base_name}_{layers_label}"
        suffix = _next_render_suffix(self.output_dir, base_name)
        output_m4b = os.path.join(self.output_dir, f"{base_name}{suffix}.m4b")
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
