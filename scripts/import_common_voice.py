#!/usr/bin/env python3
"""
Utility to bulk-import speakers from a downloaded Mozilla Common Voice dataset
into the local voice library so they can be cloned with IndexTTS2/StyleTTS2.

Usage:
    python scripts/import_common_voice.py --dataset /path/to/commonvoice/en --count 10 --engine indextts2

The script reads the dataset's `validated.tsv`, copies the referenced clips
into the voice library (reusing `VoiceLibrary.add_voice`), and tags them.
"""

import argparse
import csv
from pathlib import Path
from typing import List

from src.core.voice_library import get_voice_library


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Common Voice speakers")
    parser.add_argument("--dataset", required=True, type=Path,
                        help="Root folder of the extracted Common Voice dataset (contains validated.tsv/clips)")
    parser.add_argument("--count", type=int, default=5,
                        help="How many speakers to import (default: 5)")
    parser.add_argument("--engine", default="indextts2", choices=["indextts2", "styletts2", "sesame"],
                        help="Engine tag to store with each imported voice")
    parser.add_argument("--min-seconds", type=float, default=4.0,
                        help="Minimum clip duration to keep (default 4s)")
    parser.add_argument("--language", default=None,
                        help="Optional language/accent tag to add to the voice")
    return parser.parse_args()


def load_validated_entries(dataset_dir: Path) -> List[dict]:
    validated_path = dataset_dir / "validated.tsv"
    if not validated_path.exists():
        raise FileNotFoundError(f"Cannot find validated.tsv at {validated_path}")
    rows = []
    with validated_path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            if row.get("path"):
                rows.append(row)
    return rows


def main() -> None:
    args = parse_args()
    dataset_dir = args.dataset
    clips_dir = dataset_dir / "clips"
    if not clips_dir.exists():
        raise FileNotFoundError(f"Cannot find clips directory at {clips_dir}")

    entries = load_validated_entries(dataset_dir)
    if not entries:
        raise RuntimeError("No entries found in validated.tsv")

    voice_lib = get_voice_library()
    imported = 0

    for entry in entries:
        if imported >= args.count:
            break

        clip_path = clips_dir / entry["path"]
        if not clip_path.exists():
            continue

        try:
            if entry.get("duration"):
                duration_sec = float(entry["duration"]) / 1000.0
                if duration_sec < args.min_seconds:
                    continue
        except ValueError:
            pass

        speaker_id = entry.get("client_id") or clip_path.stem
        name = f"CV-{speaker_id[:8]}"
        tags = [entry.get("accent") or "common-voice"]
        if args.language:
            tags.append(args.language)

        audio_bytes = clip_path.read_bytes()
        voice_lib.add_voice(
            name=name,
            audio_bytes=audio_bytes,
            filename=clip_path.name,
            engine=args.engine,
            tags=[t for t in tags if t]
        )
        imported += 1
        print(f"Imported {name} from {clip_path}")

    if imported == 0:
        print("No clips met the criteria. Try lowering --min-seconds or verify the dataset path.")
    else:
        print(f"Done. Imported {imported} voices into the library.")


if __name__ == "__main__":
    main()
