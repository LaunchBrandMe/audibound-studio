#!/usr/bin/env python3
"""
Diagnose production issues by analyzing recent render output.
"""
import json
import sys

# Read the most recent ABML
abml_path = "outputs/6370250f-f631-47bd-b734-d0873548b7af/abml.json"

with open(abml_path) as f:
    abml = json.load(f)

print("=== PRODUCTION DIAGNOSTIC ===\n")

# Count blocks
blocks = abml['scenes'][0]['blocks']
print(f"Total blocks: {len(blocks)}")

# Count SFX blocks
sfx_blocks = [b for b in blocks if b.get('sfx') and b['sfx'].get('enabled')]
sfx_with_files = [b for b in sfx_blocks if b['sfx'].get('file_path')]
print(f"\nSFX Analysis:")
print(f"  Total SFX blocks (enabled): {len(sfx_blocks)}")
print(f"  SFX blocks with files: {len(sfx_with_files)}")
print(f"  SFX blocks FAILED: {len(sfx_blocks) - len(sfx_with_files)}")

if sfx_blocks and not sfx_with_files:
    print("\n❌ CRITICAL: ALL SFX FAILED TO GENERATE!")
    print("\nFailed SFX blocks:")
    for b in sfx_blocks:
        print(f"  - Block {b['id']}: {b['sfx'].get('description', 'NO DESCRIPTION')}")

# Count music blocks
music_blocks = [b for b in blocks if b.get('music') and b['music'].get('enabled')]
music_with_files = [b for b in music_blocks if b['music'].get('file_path')]
print(f"\nMusic Analysis:")
print(f"  Total music blocks (enabled): {len(music_blocks)}")
print(f"  Music blocks with files: {len(music_with_files)}")

for b in music_with_files:
    import os
    music_path = b['music']['file_path']
    if os.path.exists(music_path):
        size = os.path.getsize(music_path)
        print(f"  - Block {b['id']}: {size:,} bytes ({size/1024:.1f} KB)")
        # Estimate duration from file size (rough: ~170KB per second for WAV)
        est_duration = (size / 1024) / 170
        print(f"    Estimated duration: {est_duration:.1f}s")
    else:
        print(f"  - Block {b['id']}: FILE NOT FOUND")

# Calculate narration duration
narration_blocks = [b for b in blocks if b.get('narration') and b.get('duration_ms')]
total_narration_ms = sum(b.get('duration_ms', 0) for b in narration_blocks)
print(f"\nNarration Analysis:")
print(f"  Total narration duration: {total_narration_ms/1000:.1f}s ({total_narration_ms/60000:.2f} minutes)")

# Expected music duration
print(f"\n⚠️  Music should be at least {total_narration_ms/1000:.1f}s to cover all narration!")
