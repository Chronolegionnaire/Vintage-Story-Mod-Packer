import zipfile
import json
import shutil
import requests
from pathlib import Path
import os
import traceback
import datetime

VINTAGESTORY_DATA_DIR = Path(os.getenv("APPDATA"), "VintagestoryData")
MODS_DIR = VINTAGESTORY_DATA_DIR / "Mods"
CONFIG_DIR = VINTAGESTORY_DATA_DIR / "ModConfig"
MODPACKS_DIR = VINTAGESTORY_DATA_DIR / "ModPacks"

MODDB_API_URL = "https://mods.vintagestory.at/api/mod/"

def ensure_mods_folder():
    if not MODS_DIR.exists():
        MODS_DIR.mkdir(parents=True, exist_ok=True)

def ensure_modpacks_folder():
    if not MODPACKS_DIR.exists():
        MODPACKS_DIR.mkdir(parents=True, exist_ok=True)
        print("No ModPacks folder found; a folder has been created. Drop mod pack zips here to install.")
        return False
    return True

def extract_mod_info(mod_file):
    try:
        with zipfile.ZipFile(mod_file, 'r') as zipf:
            with zipf.open("modinfo.json") as modinfo_file:
                modinfo = json.load(modinfo_file)
                modinfo_lower = {k.lower(): v for k, v in modinfo.items()}
                modid = modinfo_lower.get("modid", "unknown_modid")
                version = modinfo_lower.get("version", "unknown_version")
                return modid, version
    except (KeyError, zipfile.BadZipFile, json.JSONDecodeError):
        print(f"Error reading modinfo.json for {mod_file.stem}.")
        return "unknown_modid", "unknown_version"

def download_mod(modid, mod_version=None):
    """
    Downloads the mod using modid & version (if given),
    then saves locally as {modid}_{actualVersionFromAPI}.zip
    """
    ensure_mods_folder()

    mod_api_url = f"{MODDB_API_URL}{modid}"
    try:
        response = requests.get(mod_api_url, timeout=10)
        response.raise_for_status()
        mod_data = response.json()

        if 'releases' not in mod_data['mod'] or not mod_data['mod']['releases']:
            print(f"No releases found for {modid}.")
            return False

        releases = mod_data['mod']['releases']
        releases.sort(key=lambda r: r['releaseid'], reverse=True)

        # If user requested a specific version, see if we can find it
        chosen_release = None
        if mod_version:
            for r in releases:
                if r['modversion'] == mod_version:
                    chosen_release = r
                    break
            if chosen_release is None:
                print(f"Mod {modid} version {mod_version} not found. Using latest version instead.")

        # Fallback to newest if not found
        if chosen_release is None:
            chosen_release = releases[0]

        # The download URL might have '?dl=...' so we won't use that for the filename
        if chosen_release['mainfile'].startswith("http"):
            mod_url = chosen_release['mainfile']
        else:
            mod_url = f"https://mods.vintagestory.at/{chosen_release['mainfile']}"

        # Instead of letting the random hex name pass through,
        # build our own local filename: "<modid>_<modversion>.zip"
        actual_version = chosen_release['modversion']
        local_filename = f"{modid}_{actual_version}.zip"

        mod_file_path = MODS_DIR / local_filename
        if mod_file_path.exists():
            print(f"Mod {modid} version {actual_version} already exists, skipping.")
            return True

        print(f"Downloading {modid} version {actual_version} from {mod_url}")
        with requests.get(mod_url, stream=True) as r:
            r.raise_for_status()
            with open(mod_file_path, 'wb') as f:
                shutil.copyfileobj(r.raw, f)

        print(f"Mod {modid} downloaded successfully!")
        return True

    except requests.RequestException as e:
        print(f"Failed to download {modid}. Error: {e}")
        return False

def install_mod_pack():
    if not ensure_modpacks_folder():
        return

    modpacks = list(MODPACKS_DIR.glob("*.zip"))
    if not modpacks:
        print("No mod packs in ModPacks folder.")
        return

    print("Available Modpacks:")
    for idx, mp in enumerate(modpacks):
        print(f"{idx+1}) {mp.name}")

    try:
        choice = int(input("Select a modpack by number: ")) - 1
        chosen_modpack = modpacks[choice]
    except (ValueError, IndexError):
        print("Invalid selection. Returning to main menu.")
        return

    with zipfile.ZipFile(chosen_modpack, 'r') as zipf:
        try:
            with zipf.open("pack.json") as json_file:
                modpack_data = json.load(json_file)
        except KeyError:
            print(f"{chosen_modpack.stem} is not a valid mod pack, please try again.")
            return

        overwrite_mods = input("Overwrite current mods or merge? (Overwrite/Merge): ").strip().lower()
        if overwrite_mods == 'overwrite':
            for f in MODS_DIR.glob("*"):
                if f.is_file() or f.is_dir():
                    shutil.rmtree(f) if f.is_dir() else f.unlink()

        mods_to_download = [(m["name"], m["version"]) for m in modpack_data["mods"]]
        for (modid, version) in mods_to_download:
            download_mod(modid, version)

        if modpack_data["configs"]:
            apply_configs = input("Config files found. Overwrite or ignore? (Overwrite/Ignore): ").strip().lower()
            if apply_configs == 'overwrite':
                for cfile in CONFIG_DIR.glob("*"):
                    if cfile.is_file() or cfile.is_dir():
                        shutil.rmtree(cfile) if cfile.is_dir() else cfile.unlink()
                for config in modpack_data["configs"]:
                    target_path = CONFIG_DIR / Path(config)
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zipf.open(config) as src_file:
                        with open(target_path, 'wb') as tgt_file:
                            shutil.copyfileobj(src_file, tgt_file)

    print("Mod pack installation complete.")

def create_mod_pack():
    if not ensure_modpacks_folder():
        return

    name = input("Enter a name for your mod pack: ").strip()
    mp_zip_path = MODPACKS_DIR / f"{name}.zip"

    if mp_zip_path.exists():
        overwrite = input(f"Mod pack '{name}' exists. Overwrite? (Y/N): ").strip().lower()
        if overwrite == 'y':
            print(f"Overwriting '{name}'...")
        else:
            count = 1
            while mp_zip_path.exists():
                mp_zip_path = MODPACKS_DIR / f"{name}_{count}.zip"
                count += 1
            print(f"Creating new mod pack '{mp_zip_path.stem}'...")

    _create_mod_pack_internal(mp_zip_path, name, ask_for_configs=True)
    print(f"Mod pack '{mp_zip_path.stem}' created successfully.")

def _create_mod_pack_internal(modpack_zip_path: Path, modpack_name: str, ask_for_configs: bool):
    mods = []
    configs = []
    for mod_file in MODS_DIR.glob("*.zip"):
        modid, version = extract_mod_info(mod_file)
        mods.append({"name": modid, "version": version})

    if ask_for_configs:
        inc = input("Include config files? (Y/N): ").strip().lower()
        if inc == 'y':
            for cfile in CONFIG_DIR.glob("**/*"):
                if cfile.is_file():
                    configs.append(cfile.relative_to(CONFIG_DIR).as_posix())

    pack_data = {
        "name": modpack_name,
        "mods": mods,
        "configs": configs
    }

    with zipfile.ZipFile(modpack_zip_path, 'w') as zipf:
        zipf.writestr("pack.json", json.dumps(pack_data, indent=4))
        if configs:
            for cfile in CONFIG_DIR.glob("**/*"):
                if cfile.is_file():
                    arcpath = cfile.relative_to(CONFIG_DIR).as_posix()
                    zipf.write(cfile, arcname=arcpath)

def backup_installed_mods():
    if not ensure_modpacks_folder():
        return

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"backup_{ts}"
    backup_zip_path = MODPACKS_DIR / f"{backup_name}.zip"

    print(f"Creating a backup of currently installed mods: '{backup_zip_path.name}'...")
    _create_mod_pack_internal(backup_zip_path, backup_name, ask_for_configs=False)
    print("Backup mod pack created.")

def install_mods_from_log():
    backup_installed_mods()

    log_file_str = input("Please enter the full path to your Vintage Story log file: ").strip()
    log_file = Path(log_file_str)
    if not log_file.is_file():
        print(f"Error: '{log_file}' is not a valid file.")
        return

    found_mods = []
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "Mods, sorted by dependency:" in line:
                start_i = line.index("Mods, sorted by dependency:") + len("Mods, sorted by dependency:")
                mods_part = line[start_i:].strip()
                raw = mods_part.split(",")
                found_mods = [m.strip() for m in raw if m.strip()]
                break

    if not found_mods:
        print("No mods found in that log line. Wrong file or missing line?")
        return

    vanilla = {"game", "creative", "survival"}
    filtered_mods = [m for m in found_mods if m.lower() not in vanilla]
    if not filtered_mods:
        print("No non-vanilla mods found. Nothing to install.")
        return

    print("Mods found in log (excluding vanilla):", filtered_mods)
    confirm = input("Overwrite Mods folder? (Y/N): ").strip().lower()
    if confirm != 'y':
        print("Aborting.")
        return

    for f in MODS_DIR.glob("*"):
        if f.is_file() or f.is_dir():
            shutil.rmtree(f) if f.is_dir() else f.unlink()

    print("Attempting to download each mod...")
    for modid in filtered_mods:
        download_mod(modid, None)

    print("Mods successfully installed from log file.")

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

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\nExiting due to user interrupt (Ctrl+C)...")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        print(traceback.format_exc())
    finally:
        input("Press Enter to exit...")
