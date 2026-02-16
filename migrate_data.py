import json
import os
from datetime import datetime

DATA_FILE = "data.json"
RESULTS_FILE = "results.json"
USERS_FILE = "users.json"
STATS_FILE = "stats.json"

def load_json(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_json(file_path, data):
    with open(file_path, "w") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def migrate():
    data = load_json(DATA_FILE)
    if "users" not in data: data["users"] = {}
    if "results" not in data: data["results"] = {}
    if "stats" not in data: data["stats"] = {}
    
    # Migrate Users
    users = load_json(USERS_FILE)
    if users:
        print(f"Migrating {len(users)} users...")
        data["users"].update(users)
        
    # Migrate Results
    results = load_json(RESULTS_FILE)
    if results:
        print(f"Migrating {len(results)} results...")
        data["results"].update(results)
        
    # Migrate Stats
    stats = load_json(STATS_FILE)
    if stats:
        print(f"Migrating stats...")
        data["stats"].update(stats)
        
    save_json(DATA_FILE, data)
    print("Migration complete!")

if __name__ == "__main__":
    migrate()
