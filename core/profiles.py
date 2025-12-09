"""
Filament Profiles Management
"""
import uuid
from core.config import PROFILES_FILE, DATA_DIR
from core.storage import load_profiles_data, save_profiles_data


def get_all_profiles():
    """
    Get all filament profiles.
    Returns a dict of profile_id -> profile_data.
    """
    data = load_profiles_data(PROFILES_FILE)
    return data.get("profiles", {})


def get_profile(profile_id):
    """
    Get a specific profile by ID.
    Returns profile dict or None if not found.
    """
    profiles = get_all_profiles()
    return profiles.get(profile_id)


def upsert_profile(profile_data):
    """
    Add or update a profile.
    If 'id' is missing, a new one is generated.
    Returns the profile_id.
    """
    data = load_profiles_data(PROFILES_FILE)
    profiles = data.get("profiles", {})
    
    profile_id = profile_data.get("id")
    if not profile_id:
        profile_id = str(uuid.uuid4())
        profile_data["id"] = profile_id
    
    # Ensure required fields have defaults if missing (though caller should provide them)
    # We don't strictly enforce schema here, but good to have some safety
    if "name" not in profile_data:
        profile_data["name"] = "Unnamed Profile"
        
    profiles[profile_id] = profile_data
    data["profiles"] = profiles
    
    save_profiles_data(PROFILES_FILE, DATA_DIR, data)
    return profile_id


def update_profile(profile_id, updates):
    """
    Update an existing profile with provided fields.
    Returns True if updated, False if profile not found.
    """
    data = load_profiles_data(PROFILES_FILE)
    profiles = data.get("profiles", {})
    
    if profile_id not in profiles:
        return False
    
    profile = profiles[profile_id]
    
    allowed_keys = {
        "name",
        "material",
        "brand",
        "color",
        "filament_mode",
        "filament_rate",
        "grams_per_meter",
    }
    
    for key, value in updates.items():
        if key in allowed_keys and value is not None:
            profile[key] = value
    
    profiles[profile_id] = profile
    data["profiles"] = profiles
    save_profiles_data(PROFILES_FILE, DATA_DIR, data)
    return True


def delete_profile(profile_id):
    """
    Delete a profile by ID.
    Also removes any printer mappings to this profile.
    """
    data = load_profiles_data(PROFILES_FILE)
    profiles = data.get("profiles", {})
    mappings = data.get("mappings", {})
    
    if profile_id in profiles:
        del profiles[profile_id]
        
        # Remove mappings pointing to this profile
        keys_to_remove = [k for k, v in mappings.items() if v == profile_id]
        for k in keys_to_remove:
            del mappings[k]
            
        data["profiles"] = profiles
        data["mappings"] = mappings
        save_profiles_data(PROFILES_FILE, DATA_DIR, data)
        return True
    return False


def get_printer_mapping(printer_name):
    """
    Get the active profile ID for a printer.
    Returns profile_id or None.
    """
    data = load_profiles_data(PROFILES_FILE)
    mappings = data.get("mappings", {})
    return mappings.get(printer_name)


def set_printer_mapping(printer_name, profile_id):
    """
    Set the active profile for a printer.
    If profile_id is None, removes the mapping.
    """
    data = load_profiles_data(PROFILES_FILE)
    mappings = data.get("mappings", {})
    
    if profile_id is None:
        if printer_name in mappings:
            del mappings[printer_name]
    else:
        # Verify profile exists
        profiles = data.get("profiles", {})
        if profile_id in profiles:
            mappings[printer_name] = profile_id
        else:
            return False # Profile not found
            
    data["mappings"] = mappings
    save_profiles_data(PROFILES_FILE, DATA_DIR, data)
    return True


def get_all_printer_mappings():
    """
    Get all printer-to-profile mappings.
    Returns a dict of printer_name -> profile_id.
    """
    data = load_profiles_data(PROFILES_FILE)
    return data.get("mappings", {})
