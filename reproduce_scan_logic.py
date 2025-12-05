import os
import re
import json
import shutil
from datetime import datetime
from typing import List, Optional, Dict
from pydantic import BaseModel

# --- Mock Classes ---
class RenderHistoryEntry(BaseModel):
    timestamp: str
    engine: str
    output_path: Optional[str] = None
    layers: Optional[List[str]] = None
    notes: Optional[List[str]] = None

# --- Logic to Test ---

def scan_project_outputs(project_id: str, project_data: Dict) -> Dict:
    """
    Scans the project output directory for:
    1. abml.json -> to restore bible and manifest if missing
    2. *.m4b -> to populate render_history
    """
    output_dir = os.path.join("outputs", project_id)
    if not os.path.exists(output_dir):
        print(f"Output dir {output_dir} does not exist")
        return project_data

    updates = {}
    
    # 1. Check for ABML (restore direction)
    abml_path = os.path.join(output_dir, "abml.json")
    if os.path.exists(abml_path) and (not project_data.get("manifest") or not project_data.get("bible")):
        print("Found abml.json, restoring manifest and bible...")
        try:
            with open(abml_path, 'r') as f:
                manifest_data = json.load(f)
            
            # Manifest contains bible usually? 
            # Looking at src/core/abml.py (inferred), ScriptManifest has 'bible' field.
            # Let's assume the JSON is the dumped ScriptManifest.
            
            updates["manifest"] = manifest_data
            if "bible" in manifest_data:
                updates["bible"] = manifest_data["bible"]
            
            # If we restored manifest, status should probably be at least 'directed'
            if project_data.get("status") == "created":
                updates["status"] = "directed"
                
        except Exception as e:
            print(f"Failed to load abml.json: {e}")

    # 2. Check for Render History
    existing_history = project_data.get("render_history") or []
    existing_paths = {entry.get("output_path") for entry in existing_history if entry.get("output_path")}
    
    new_entries = []
    
    # Regex to parse filename: Title_layers__suffix.m4b
    # Example: My_Project_voice_sfx__01.m4b
    # We need to be careful about underscores in title.
    # The suffix is always __\d+.m4b
    
    for filename in os.listdir(output_dir):
        if not filename.endswith(".m4b"):
            continue
            
        file_path = os.path.join(output_dir, filename)
        # Store relative path for portability if needed, or absolute? 
        # The app seems to use relative paths like "outputs/..."
        rel_path = os.path.join("outputs", project_id, filename)
        
        if rel_path in existing_paths:
            continue
            
        # Get timestamp from file modification time
        mod_time = os.path.getmtime(file_path)
        timestamp = datetime.fromtimestamp(mod_time).isoformat() + 'Z'
        
        # Parse filename for layers
        layers = []
        engine = "Unknown"
        
        # Try to extract layers
        # Pattern: ..._layer1_layer2__XX.m4b
        # We look for the suffix __\d+.m4b
        match = re.search(r"(_([a-z_]+))?__\d+\.m4b$", filename)
        if match:
            # group 1 is like "_voice_sfx"
            layers_part = match.group(2) # "voice_sfx"
            if layers_part:
                possible_layers = layers_part.split('_')
                valid_layers = {'voice', 'sfx', 'music'}
                parsed_layers = [l for l in possible_layers if l in valid_layers]
                if parsed_layers:
                    layers = parsed_layers
        
        if not layers:
            layers = ["voice"] # Default assumption if parsing fails but it's an m4b
            
        new_entries.append({
            "timestamp": timestamp,
            "engine": "Detected",
            "output_path": rel_path,
            "layers": layers,
            "notes": ["Detected from disk"]
        })
    
    if new_entries:
        print(f"Found {len(new_entries)} new render history entries")
        # Sort by timestamp
        new_entries.sort(key=lambda x: x["timestamp"])
        
        # Append to existing
        updated_history = existing_history + new_entries
        # Sort all by timestamp descending
        updated_history.sort(key=lambda x: x["timestamp"], reverse=True)
        updates["render_history"] = updated_history

    # Apply updates
    project_data.update(updates)
    return project_data

# --- Test Execution ---

def run_test():
    project_id = "test_scan_project"
    output_dir = os.path.join("outputs", project_id)
    
    # Clean start
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Create dummy abml.json
    dummy_manifest = {
        "project_id": project_id,
        "title": "Test Project",
        "bible": {"characters": []},
        "scenes": []
    }
    with open(os.path.join(output_dir, "abml.json"), "w") as f:
        json.dump(dummy_manifest, f)
        
    # 2. Create dummy m4b files
    # File 1: Voice only
    with open(os.path.join(output_dir, "Test_Project_voice__01.m4b"), "w") as f:
        f.write("dummy audio")
    
    # File 2: Voice + SFX
    with open(os.path.join(output_dir, "Test_Project_voice_sfx__02.m4b"), "w") as f:
        f.write("dummy audio")
        
    # Mock DB Project Data (empty)
    project_data = {
        "id": project_id,
        "title": "Test Project",
        "status": "created",
        "manifest": None,
        "bible": None,
        "render_history": []
    }
    
    print("--- Before Scan ---")
    print(f"Status: {project_data['status']}")
    print(f"Manifest: {project_data['manifest'] is not None}")
    print(f"History: {len(project_data['render_history'])}")
    
    # Run Scan
    updated_project = scan_project_outputs(project_id, project_data)
    
    print("\n--- After Scan ---")
    print(f"Status: {updated_project['status']}")
    print(f"Manifest: {updated_project['manifest'] is not None}")
    print(f"History: {len(updated_project['render_history'])}")
    
    for entry in updated_project['render_history']:
        print(f" - {entry['output_path']} (Layers: {entry['layers']})")

    # Cleanup
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)

if __name__ == "__main__":
    run_test()
