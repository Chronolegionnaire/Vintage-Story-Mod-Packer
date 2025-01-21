import zipfile
import json
import shutil
import requests
from pathlib import Path
import os
import traceback
import concurrent.futures
import datetime

VINTAGESTORY_DATA_DIR = Path(os.getenv('APPDATA'), "VintagestoryData")
MODS_DIR = VINTAGESTORY_DATA_DIR / "Mods"
CONFIG_DIR = VINTAGESTORY_DATA_DIR / "ModConfig"
MODPACKS_DIR = VINTAGESTORY_DATA_DIR / "ModPacks"
MODDB_API_URL = "https://mods.vintagestory.at/api/mod/"  # Vintage Story Mod API

# Helper to ensure ModPacks folder exists
def ensure_modpacks_folder():
    if not MODPACKS_DIR.exists():
        MODPACKS_DIR.mkdir()
        print("No ModPacks folder found, a folder has been created for you, please drop mod pack zips here to install a mod pack.")
        return False
    return True

# Function to extract mod info from the modinfo.json file
def extract_mod_info(mod_file):
    try:
        with zipfile.ZipFile(mod_file, 'r') as zipf:
            with zipf.open("modinfo.json") as modinfo_file:
                modinfo = json.load(modinfo_file)
                # Handle any combination of capitalization by normalizing keys to lowercase
                modinfo_lower = {k.lower(): v for k, v in modinfo.items()}
                modid = modinfo_lower.get("modid", "unknown_modid")
                version = modinfo_lower.get("version", "unknown_version")
                return modid, version
    except (KeyError, zipfile.BadZipFile, json.JSONDecodeError):
        print(f"Error reading modinfo.json for {mod_file.stem}.")
        return "unknown_modid", "unknown_version"

def download_mod(modid, mod_version=None):
    """
    If mod_version is provided, attempts to download that specific version.
    If not found or mod_version is None, tries to download the latest release.
    """
    mod_api_url = f"{MODDB_API_URL}{modid}"

    try:
        response = requests.get(mod_api_url, timeout=10)
        response.raise_for_status()
        mod_data = response.json()

        # Sort releases by releaseid descending so the first item is the newest
        if 'releases' not in mod_data['mod'] or not mod_data['mod']['releases']:
            print(f"No releases found for {modid}.")
            return False

        releases = mod_data['mod']['releases']
        releases.sort(key=lambda r: r['releaseid'], reverse=True)

        mod_release = None

        if mod_version:
            # Find the correct version or fallback to the newest if not found
            for release in releases:
                if release['modversion'] == mod_version:
                    mod_release = release
                    break
            if mod_release is None:
                print(f"Mod {modid} version {mod_version} not found. Attempting to use latest version.")
        if mod_release is None:
            mod_release = releases[0]

        # Download the mod
        mod_url = f"https://mods.vintagestory.at/{mod_release['mainfile']}"
        mod_file_name = mod_release['mainfile'].split('/')[-1]
        mod_file_path = MODS_DIR / mod_file_name

        # If file already exists, skip
        if mod_file_path.exists():
            print(f"Mod {modid} version {mod_release['modversion']} already exists, skipping download.")
            return True

        print(f"Downloading {modid} version {mod_release['modversion']} from {mod_url}")
        with requests.get(mod_url, stream=True) as r:
            r.raise_for_status()
            with open(mod_file_path, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        print(f"Mod {modid} downloaded successfully!")
        return True

    except requests.RequestException as e:
        print(f"Failed to download {modid}. Error: {e}")
        return False

# Function to install a mod pack (reads pack.json and installs mods)
def install_mod_pack():
    if not ensure_modpacks_folder():
        return

    modpacks = list(MODPACKS_DIR.glob("*.zip"))
    if not modpacks:
        print("No mod packs in ModPacks folder.")
        return

    # List available mod packs
    print("Available Modpacks:")
    for idx, modpack in enumerate(modpacks):
        print(f"{idx + 1}) {modpack.name}")

    # Select mod pack by number
    try:
        choice = int(input("Select a modpack by number: ")) - 1
        chosen_modpack = modpacks[choice]
    except (ValueError, IndexError):
        print("Invalid selection. Returning to main menu.")
        return

    # Open and validate the mod pack (ensure pack.json exists)
    with zipfile.ZipFile(chosen_modpack, 'r') as zipf:
        try:
            with zipf.open("pack.json") as json_file:
                modpack_data = json.load(json_file)
        except KeyError:
            print(f"{chosen_modpack.stem} is not a valid mod pack, please try again.")
            return

        overwrite_mods = input("Do you want to overwrite your current mods or merge with existing? (Overwrite/Merge): ").strip().lower()

        # Overwrite mods if selected
        if overwrite_mods == 'overwrite':
            # Clear existing mods
            for mod_file in MODS_DIR.glob("*"):
                if mod_file.is_file() or mod_file.is_dir():
                    shutil.rmtree(mod_file) if mod_file.is_dir() else mod_file.unlink()

        # Install each mod from the mod pack concurrently
        mods_to_download = [(mod["name"], mod["version"]) for mod in modpack_data["mods"]]

        def download_task(mod):
            modid, mod_version = mod
            download_mod(modid, mod_version)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            executor.map(download_task, mods_to_download)

        # Handle config files
        if modpack_data["configs"]:
            apply_configs = input("Config files for mod pack found. Would you like to Overwrite current config or ignore? (Overwrite/Ignore): ").strip().lower()

            if apply_configs == 'overwrite':
                # Clear existing config files
                for config_file in CONFIG_DIR.glob("*"):
                    if config_file.is_file() or config_file.is_dir():
                        shutil.rmtree(config_file) if config_file.is_dir() else config_file.unlink()

                # Extract config files from the mod pack to the config directory
                for config in modpack_data["configs"]:
                    target_path = CONFIG_DIR / Path(config)
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zipf.open(config) as source_file:
                        with open(target_path, 'wb') as target_file:
                            shutil.copyfileobj(source_file, target_file)

    print("Mod pack installation complete.")

# Function to create a mod pack (gathers mods and configs and creates a zip file)
def create_mod_pack():
    if not ensure_modpacks_folder():
        return

    modpack_name = input("Enter a name for your mod pack: ").strip()
    modpack_zip_path = MODPACKS_DIR / f"{modpack_name}.zip"

    # Check if the mod pack already exists
    if modpack_zip_path.exists():
        overwrite = input(f"A mod pack named '{modpack_name}' already exists. Do you want to overwrite it? (Y/N): ").strip().lower()
        if overwrite == 'y':
            print(f"Overwriting existing mod pack '{modpack_name}'...")
        else:
            count = 1
            while modpack_zip_path.exists():
                modpack_zip_path = MODPACKS_DIR / f"{modpack_name}_{count}.zip"
                count += 1
            print(f"Creating new mod pack with name '{modpack_zip_path.stem}'...")

    # Actually create the pack
    _create_mod_pack_internal(modpack_zip_path, modpack_name, ask_for_configs=True)
    print(f"Mod pack '{modpack_zip_path.stem}' created successfully.")

def _create_mod_pack_internal(modpack_zip_path: Path, modpack_name: str, ask_for_configs: bool = True):
    """
    Internal helper to create a mod pack zip file that includes:
      - All mods in the Mods directory.
      - (Optionally) config files in the Config directory.
    """
    mods = []
    configs = []

    # Collect all mods in the Mods directory
    for mod_file in MODS_DIR.glob("*.zip"):
        modid, version = extract_mod_info(mod_file)
        mods.append({"name": modid, "version": version})

    include_configs = 'n'
    if ask_for_configs:
        include_configs = input("Do you want to include config files in the mod pack? (Y/N): ").strip().lower()

    # Collect all config files (including subfolders) if chosen
    if include_configs == 'y':
        for config_file in CONFIG_DIR.glob("**/*"):
            if config_file.is_file():
                configs.append(config_file.relative_to(CONFIG_DIR).as_posix())

    # Generate pack.json data
    pack_data = {
        "name": modpack_name,
        "mods": mods,
        "configs": configs
    }

    # Create or overwrite the mod pack zip file
    with zipfile.ZipFile(modpack_zip_path, 'w') as zipf:
        # Write pack.json inside the zip
        zipf.writestr("pack.json", json.dumps(pack_data, indent=4))

        # Include configuration files if chosen
        if include_configs == 'y':
            for config_file in CONFIG_DIR.glob("**/*"):
                if config_file.is_file():
                    zipf.write(config_file, arcname=config_file.relative_to(CONFIG_DIR).as_posix())

# Helper to quickly back up the currently installed mods without user interaction
def backup_installed_mods():
    """
    Creates a backup mod pack named 'backup_{timestamp}.zip' containing all currently installed mods.
    Does NOT prompt the user for config inclusion or a custom name.
    """
    if not ensure_modpacks_folder():
        return

    timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"backup_{timestamp_str}"
    backup_zip_path = MODPACKS_DIR / f"{backup_name}.zip"
    print(f"Creating a backup of currently installed mods: '{backup_zip_path.name}'...")

    # Internal create pack, skipping config prompt
    _create_mod_pack_internal(backup_zip_path, backup_name, ask_for_configs=False)
    print("Backup mod pack created.")

def install_mods_from_log():
    """
    1) Creates a backup of currently installed mods.
    2) Prompts for a Vintage Story log file path.
    3) Reads the mods from the line "Mods, sorted by dependency: ...",
       ignoring 'game', 'creative', and 'survival'.
    4) Completely overwrites (clears) the currently installed Mods folder.
    5) Downloads the latest version of each mod found.
    """
    # 1) Back up currently installed mods
    backup_installed_mods()

    # 2) Prompt user for the log file path
    log_file_path_str = input("Please enter the full path to your Vintage Story log file: ").strip()
    log_file_path = Path(log_file_path_str)
    if not log_file_path.is_file():
        print(f"Error: '{log_file_path}' is not a valid file.")
        return

    # 3) Parse the log file, find the line with "Mods, sorted by dependency:"
    found_mods = []
    with open(log_file_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "Mods, sorted by dependency:" in line:
                # Example: "Mods, sorted by dependency: game, customtransitionlib, creative, survival, brainfreeze"
                start_index = line.index("Mods, sorted by dependency:") + len("Mods, sorted by dependency:")
                mods_part = line[start_index:].strip()
                # split by comma
                mods_raw = mods_part.split(",")
                # strip whitespace from each mod
                found_mods = [m.strip() for m in mods_raw if m.strip()]
                break

    if not found_mods:
        print("Could not find any mods in the specified log file. Make sure you selected the correct file.")
        return

    # 4) Remove vanilla mods: 'game', 'creative', 'survival'
    vanilla_mods = {"game", "creative", "survival"}
    filtered_mods = [m for m in found_mods if m.lower() not in vanilla_mods]

    if not filtered_mods:
        print("No non-vanilla mods were found in the log. Nothing to install.")
        return

    print("Mods found in log (excluding vanilla):", filtered_mods)

    # Confirm user wants to proceed
    confirm = input("This will completely overwrite your currently installed Mods. Continue? (Y/N): ").strip().lower()
    if confirm != 'y':
        print("Aborting operation.")
        return

    # Completely clear Mods folder
    for mod_file in MODS_DIR.glob("*"):
        if mod_file.is_file() or mod_file.is_dir():
            shutil.rmtree(mod_file) if mod_file.is_dir() else mod_file.unlink()

    # 5) Download each mod found, retrieving the latest version
    print("Attempting to download the latest version of each mod found in the log...")
    def download_task(modid):
        download_mod(modid, None)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        executor.map(download_task, filtered_mods)

    print("Mods successfully installed from log file.")

# Function to display the main menu
def main_menu():
    while True:
        print("\nMain Menu:")
        print("1) Create a mod pack")
        print("2) Install a mod pack")
        print("3) Exit")
        print("4) Install Mods from Log File")
        choice = input("Select an option: ").strip()

        if choice == '1':
            create_mod_pack()
        elif choice == '2':
            install_mod_pack()
        elif choice == '3':
            print("Exiting...")
            break
        elif choice == '4':
            install_mods_from_log()
        else:
            print("Invalid choice. Please select 1, 2, 3, or 4.")

# Global exception handler to prevent the window from closing on errors
if __name__ == "__main__":
    try:
        main_menu()  # Run the main menu
    except KeyboardInterrupt:
        print("\nExiting due to user interrupt (Ctrl+C)...")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        print(traceback.format_exc())  # Prints detailed traceback
    finally:
        input("Press Enter to exit...")
