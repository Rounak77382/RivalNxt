import os
import sys
import json
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from core.nexus.nexus_api import get_mod_file_download_link, get_api_key
from core.config.settings import load_settings

def test_download_link():
    settings = load_settings()
    api_key = settings.nexus_api_key
    print(f"API Key exists: {bool(api_key)}")
    
    if not api_key:
        print("No API key configured. Cannot test Premium API endpoint.")
        return
        
    game = 'marvelrivals'
    mod_id = 7341
    file_id = 18883
    
    print(f"Querying download link for {game} mod {mod_id} file {file_id}...")
    status, data = get_mod_file_download_link(api_key, game, mod_id, file_id)
    
    print(f"\nHTTP Status: {status}")
    print("Response Data:")
    print(json.dumps(data, indent=2))

if __name__ == '__main__':
    test_download_link()
