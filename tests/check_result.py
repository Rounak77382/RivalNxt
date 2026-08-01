
import json
from pathlib import Path

def verify():
    json_path = Path("marvel_rivals_ids.json")
    if not json_path.exists():
        print("JSON file not found!")
        return

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Check Magik (1029) Skin 303
    char_id = "1029"
    skin_variant = "303"
    
    if char_id in data:
        print(f"Character {char_id} found: {data[char_id]['name']}")
        skins = data[char_id]['skins']
        if skin_variant in skins:
            name = skins[skin_variant]
            print(f"Skin {char_id}{skin_variant}: '{name}'")
            
            if name == "new millenia might":
                print("\n✅ VERIFICATION SUCCESS: Name matches expected value!")
            elif "variant" in name:
                print("\n❌ VERIFICATION FAILED: Name is still a fallback variant.")
            else:
                print(f"\n⚠️ VERIFICATION SUCCESS-ish: Name found ('{name}'), but differs from expected 'new millenia might'. Check casing?")
        else:
            print(f"\n❌ Skin {char_id}{skin_variant} NOT FOUND in output JSON.")
    else:
        print(f"\n❌ Character {char_id} NOT FOUND in output JSON.")

if __name__ == "__main__":
    verify()
