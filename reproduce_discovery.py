import os
import json
import shutil
from typing import Dict, List

# --- Logic to Test ---

def _discover_projects_from_disk(current_db: Dict) -> Dict:
    """
    Scans outputs/ directory for folders containing abml.json.
    If a folder exists but is not in current_db, adds it.
    Returns updated db.
    """
    outputs_dir = "outputs"
    if not os.path.exists(outputs_dir):
        return current_db
        
    updates_made = False
    
    for project_id in os.listdir(outputs_dir):
        project_path = os.path.join(outputs_dir, project_id)
        if not os.path.isdir(project_path):
            continue
            
        # Skip system folders
        if project_id in ["cache", "playground_history", "voice_tests", "voice_cloning_tests"]:
            continue
            
        # Check if already in DB
        if project_id in current_db:
            continue
            
        # Check for abml.json
        abml_path = os.path.join(project_path, "abml.json")
        if os.path.exists(abml_path):
            try:
                print(f"[Discovery] Found new project on disk: {project_id}")
                with open(abml_path, 'r') as f:
                    manifest = json.load(f)
                
                # Create project entry
                new_project = {
                    "id": project_id,
                    "title": manifest.get("title", project_id),
                    "status": "directed", # At least directed if it has manifest
                    "manifest": manifest,
                    "bible": manifest.get("bible"),
                    "voice_overrides": {}, # We could try to infer this but empty is safe
                    "render_history": [], # Will be populated by _scan_and_update_project_outputs later
                    "raw_text": "" # We might not have raw text unless we saved it elsewhere
                }
                
                current_db[project_id] = new_project
                updates_made = True
            except Exception as e:
                print(f"[Discovery] Failed to import {project_id}: {e}")
                
    return current_db, updates_made

# --- Test Execution ---

def run_test():
    # Setup
    os.makedirs("outputs/proj_A", exist_ok=True)
    with open("outputs/proj_A/abml.json", "w") as f:
        json.dump({"title": "Project A", "bible": {}}, f)
        
    os.makedirs("outputs/proj_B", exist_ok=True)
    with open("outputs/proj_B/abml.json", "w") as f:
        json.dump({"title": "Project B", "bible": {}}, f)
        
    # Mock DB - empty
    db = {}
    
    print("--- Before Discovery ---")
    print(f"Projects: {list(db.keys())}")
    
    # Run
    db, updated = _discover_projects_from_disk(db)
    
    print("\n--- After Discovery ---")
    print(f"Projects: {list(db.keys())}")
    print(f"Proj A Title: {db['proj_A']['title']}")
    
    # Cleanup
    shutil.rmtree("outputs/proj_A")
    shutil.rmtree("outputs/proj_B")

if __name__ == "__main__":
    run_test()
