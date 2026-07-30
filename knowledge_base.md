# RivalNxt Mod Manager - Knowledge Base

This document serves as the primary, comprehensive source of truth for the RivalNxt Mod Manager. It is designed to be highly structured and token-efficient for AI support retrieval.

---

## 1. Overview

**What it is:** RivalNxt is a native desktop application (Tauri + React + Python/Rust) designed exclusively for managing Marvel Rivals mods.
**Primary Purpose:** To provide a seamless interface to install, manage, activate, and validate mods, ensuring they do not conflict and are correctly applied to the game.
**Supported Workflow:** Users find mods on NexusMods, click "Download with Mod Manager" (NXM protocol), let RivalNxt download, unpack, and scan the mod. Users then toggle mods on or off. RivalNxt automatically manages the `.pak` and `.utoc` files within the game directory, detecting crashes and resolving conflicts.

---

## 2. Installation & First-Time Setup

### Requirements
- OS: Windows 10/11
- Game: Marvel Rivals (Steam or Epic Games)
- Archive Tools: Auto-detected (WinRAR is automatically detected in standard program paths for `.rar` extraction fallback if native Rust extraction is unsupported for a specific archive format).

### First-Time Setup Flow
1. **Download & Install:** Download `RivalNxt_X.Y.Z_x64-setup.exe` from GitHub Releases and run the installer.
2. **First Launch:** The app opens to the **Get Started** wizard if paths are not configured.
3. **Auto-Detection:** The app attempts to automatically detect the Marvel Rivals installation path by scanning Windows Registry (Steam/Epic). 
4. **Manual Selection:** If auto-detection fails, the user must manually browse and select the game's root directory.
5. **Downloads Folder:** The user must specify a local downloads directory (`marvel_rivals_local_downloads_root`). This is where mod archives (`.zip`, `.rar`) and unpacked contents are permanently stored. Mod folders must follow the convention `<name>-<modid>-<version>`.
6. **Nexus API Setup:** To enable one-click downloads and metadata sync, the user must input their Personal Nexus API Key in Settings (retrieved from the NexusMods website).
7. **Permissions:** The app may require elevated permissions (Run as Administrator) to properly register the `nxm://` protocol handler in the Windows Registry and write to certain game folders.

---

## 3. Complete UI Documentation

### Global Navigation & Layout
- **Sidebar Navigation:** Contains primary tabs: Browse, Downloads, Collections, Settings.
- **Top Header:** Contains the search bar, tag filters, sorting dropdowns, and a global **"Start Game"** button to immediately launch Marvel Rivals.
- **Background Processes:** Invisible daemon tasks handle NXM handoffs, download progression, and database library sync.
- **Support & Donations:** Contains embedded Ko-fi and UPI support links (via `iconManager.ts`) for users wishing to support development.

### Browse Page (Main Mod Library)
- **Purpose:** View and manage installed mods.
- **Mod Cards:** Displays the mod thumbnail, name, author, version, update status, and active status.
- **Thumbnail Blur:** Adult/NSFW mods have thumbnails blurred automatically via CSS filter, toggleable via the NSFW filter setting.
- **Play/Checkmark Badge:** Indicates if a mod is currently Active (loaded into the game).
- **Warning/Update Badge:** Red alert badge indicates a newer version is available.
- **Trash Icon:** Uninstalls the mod (removes from game and deletes local files).
- **Refresh/Update Icon:** Force-checks for updates. If an update is available, it turns into a direct "Update" button.
- **Heart Icon:** Toggles favorite status.
- **Assign Mod ID Button:** Shown only for local mods not recognized by Nexus. Clicking prompts the user to input a valid Nexus Mod ID to fetch metadata.
- **Author Avatar/Name:** Clicking the author opens a context popover to view the author's Nexus profile or assign a custom author name (via `AuthorPopover`).

### Downloads Page
- **Purpose:** Track active and queued downloads originating from NexusMods.
- **List Items:** Show file name, download progress bar, speed, and status (Queued, Downloading, Extracting, Completed, Failed).
- **Controls:** Cancel (X icon) to stop a download. Downloads utilize background `NxmBackgroundListener`.

### Collections Page
- **Purpose:** Manage Nexus Collections (bulk mod lists).
- **Actions:** Import Collection, Update Collection.
- **Conflict Handling:** Automatically detects duplicates or conflicting files within a collection.

### Mod Details Modal (Double-click or 'Enter' on a Mod)
- **Overview Tab:** Shows description, summary, author, category, and tags. 
- **Files/Assets Tab:** Hierarchical file tree renderer (`FileTreeRenderer`). Users can toggle specific `.pak` files on or off within a single mod. Essential for complex mods with multiple variants.
- **Changelog Tab:** Fetches and displays version history from Nexus API.
- **Images Tab:** Displays mod screenshots. Supports native drag-and-drop custom image uploading directly in the interface.

### Crash Detector Modal
- **Purpose:** Automatically appears if RivalNxt detects a game crash log indicating a mod failure.
- **Behavior:** Parses UE crash logs against known `MOD_CRASH_PATTERNS`. Uses **Two-Tier Matching**: identifies exact file paths (Exact Match) or character meshes (Character Match) causing the crash and offers a one-click "Deactivate Faulty Mods" button.

### Mod Conflict Modal
- **Purpose:** Triggers when a user activates a mod that modifies the same character/asset as an already active mod.
- **Options:** 
  - **Cancel:** Abort activation.
  - **Override:** Deactivate the conflicting mods and activate the new one.

---

## 4. Mod Management

- **Installing:** Triggered via NXM link or manual folder drop. The backend extracts the archive (ZIP/RAR) using native `rust-ue-tools` (skipping external CLI dependencies), hashes the files, and ingests them into the SQLite database.
- **Updating:** Clicking "Update" triggers a background download of the latest file ID from Nexus, extracts it, and seamlessly replaces the older version. The older version is queued for deep cleanup.
- **Removing / Uninstalling:** Clicking the Trash icon removes the mod from the active game directory and deletes the source files in the local downloads folder.
- **Disabling / Enabling:** Toggling the Active switch links or unlinks the `.pak` and `.utoc` files to the `MarvelRivals/MarvelGame/Content/Paks/~mods` directory.
- **Conflict Detection:** AST-based asset mapping parses UE `.pak` files on ingestion. If two mods touch the same internal asset path (e.g., a specific character skin mesh), a conflict is registered.
- **Automated Mod Recovery:** Uses robust MD5 file hashing and background daemons to automatically recover and re-link broken, misnamed, or orphaned mod files.
- **Backups & Restore:** SQLite database state (active mods, custom tags) can be backed up to a `.json`/`.db` snapshot and restored later.
- **Custom Tags:** Users can manually type and assign custom tags.
- **Generated Tags:** The ingestion engine attempts to guess the character based on internal file paths (100% PAK-sourced). If the character code is obscure, it uses a dynamic fallback resolver (Fandom Wiki) to map skin IDs to human-readable names.
- **Library Sync & Smart Bootstrap:** On startup, the manager scans the game directory and downloads directory to ensure the database matches reality. If the game updated, it dynamically rebuilds character/skin data.
- **Outdated Cleanup:** A background daemon task (`_task_delete_outdated_versions`) scans for orphaned `.pak` files and older versions of updated mods, performing deep deletion logic to save disk space.

---

## 5. Settings & Configuration

The application allows users to configure essential paths via the UI, but it also supports advanced environment variables for debugging or portable setups.

**UI Settings:**
- **Game Path (`marvel_rivals_root`):** The absolute path to the Marvel Rivals executable/game folder.
- **Downloads Path (`marvel_rivals_local_downloads_root`):** The absolute path to store mod archives.
- **Nexus API Key:** Required for downloads and metadata. Validated against the Nexus GraphQL API.
- **NXM Protocol Handler Toggle:** Registers `nxm://` in the Windows registry to point to RivalNxt. Required for website integration.
- **NSFW Content Blur:** Toggle to obscure thumbnails tagged as Adult by NexusMods.
- **Check for Updates on Startup:** Toggle automatic update checks for both the Mod Manager itself and installed mods.
- **Auto-Cleanup Outdated Mods:** Toggle the background deep cleanup task.

**Environment Variables (Advanced):**
- `MM_DATA_DIR`: Overrides the default AppData directory location.
- `MM_BACKEND_HOST`: Overrides the backend bind address (Default: `127.0.0.1`).
- `MM_BACKEND_PORT`: Overrides the backend port (Default: `8000`).

---

## 6. Nexus Mods Integration

- **Authentication:** Uses the Personal API Key. No SSO OAuth.
- **Downloads:** When a user clicks "Mod Manager Download" on NexusMods, the browser triggers an `nxm://` URL. The `NxmBackgroundListener` component captures this.
- **Fast vs Slow Download:** Enforced by Nexus APIs based on the user's account tier (Premium vs Standard).
- **Metadata Sync:** Scheduled tasks pull the latest version numbers, thumbnails, summaries, and changelogs.
- **Rate Limits:** The manager respects Nexus API rate limits. Exceeding limits will temporarily pause background syncs.

---

## 7. Search & Filtering

- **Text Search:** Fuzzy matches against Mod Name, Author, Summary, and Custom Tags.
- **Filter by Status:** Active, Inactive, Has Update.
- **Filter by Tag:** Multi-select dropdown for tags (Generated and Custom).
- **Sorting:** Sort by Name (A-Z, Z-A), Install Date (Newest, Oldest), Last Updated.

---

## 8. Notifications

- **Success (Green Check):** Mod installed, Settings saved, Mod activated, Update complete.
- **Warning (Yellow Triangle):** Mod conflict detected, Nexus API rate limit approaching, Unknown mod format.
- **Error (Red Alert):** Game path invalid, Extraction failed (corrupt archive), NXM registration failed (requires admin).

---

## 9. Error Handling

- **"Game Path Not Found"**: The registry scan failed. **Fix:** Manually browse to the Steam/Epic library folder.
- **"Invalid API Key"**: Nexus rejected the key. **Fix:** Re-copy the Personal API Key from the bottom of the Nexus settings page.
- **"Extraction Failed / Corrupt Archive"**: The downloaded zip/rar is broken. **Fix:** Delete the mod from the UI and re-download. (Note: 7-zip cannot programmatically extract RARs in this release, so the app requires WinRAR as a fallback extractor for `.rar` files).
- **"NXM Protocol Registration Failed"**: Missing registry permissions. **Fix:** Run RivalNxt as Administrator and toggle the setting off and on.
- **"Failed to Fetch Metadata / Failed to Auto-Process"**: Usually a Nexus API outage, invalid Mod ID, or Nexus changing their download filename format. **Fix:** Ignore if transient, use "Assign Mod ID" to fix local mods, or perform a clean reinstall if the internal database corrupted during the failure.
- **"Duplicate Download Detected"**: Path-based duplicate detection triggered. **Fix:** The manager automatically skips re-downloading identical files.

---

## 10. Frequently Asked Questions

**Q: Can I use this with the Epic Games Store version?**
A: Yes, RivalNxt supports both Steam and Epic Games installations.

**Q: Should my Local Downloads folder be the same as the game's `~mods` folder?**
A: **No.** RivalNxt stores and fetches all mods in your Local Downloads folder, and automatically deploys only the active ones to the `~mods` directory.

**Q: Is there a way to switch between different versions or variants of the same mod?**
A: Yes. The manager intelligently compiles multi-option downloads (variants) into a single mod detail page. You can toggle specific variants on or off via the Files tab.

**Q: Will Presets or Load Orders be added?**
A: Presets and profiles are planned for a future update. Load order is currently handled automatically by the game's alphabetical `.pak` sorting.

**Q: Why are my mods not showing up in the game?**
A: Ensure they are marked as "Active" (green check) in the UI by clicking the mod, going to the Files tab, and toggling the specific `.pak` ON. If they are active but not working, the game might have updated or a conflicting mod is overriding it.

**Q: How do I install a mod I downloaded manually?**
A: Drag and drop the `.zip`/`.rar` file directly into the RivalNxt UI, or place it in your designated Downloads Folder following the `<name>-<id>-<version>` format and click Refresh.

**Q: Why are mod thumbnails blurry?**
A: You likely have the NSFW filter enabled in Settings, or the mod is flagged as Adult content on NexusMods.

**Q: What is a "Missing Companion Pak" error?**
A: UE5 mods often require both a `.pak` and `.utoc`/`.ucas` file. If the crash detector finds one without the other, it warns you that the mod author packaged it incorrectly.

---

## 11. Troubleshooting Guide

- **Symptom: Game not detected on startup.**
  - **Resolution:** Click "Browse" in the Get Started wizard and locate `MarvelRivals.exe`.
- **Symptom: Clicking "Download with Mod Manager" on Nexus does nothing.**
  - **Resolution:** Go to Settings -> Toggle "Register NXM Protocol" off then on again. Ensure you launched RivalNxt as Administrator.
- **Symptom: Mods show as "Active" but game crashes on startup.**
  - **Resolution:** Open RivalNxt. The Crash Detector Modal should automatically appear and identify the culprit. If not, use the "Disable All Mods" feature to isolate the issue.
- **Symptom: Mod thumbnails are broken or missing.**
  - **Resolution:** Right-click the mod or open Details -> Images tab, and drag-and-drop a custom image to act as the cover.
- **Symptom: "Assign Mod ID" keeps popping up.**
  - **Resolution:** The mod folder name does not contain a recognizable Nexus ID, so it cannot sync updates. Click it and type the numbers from the end of the NexusMods URL.
- **Symptom: WinRAR throws "Unknown Option: ba" or "Unknown Option: I" error.**
  - **Resolution:** Ensure the archiver path in settings ends with `rar.exe` (e.g., `C:\Program Files\WinRAR\rar.exe`), not `winrar.exe`. Use the autodetect button to fix this automatically.
- **Symptom: Stuck on Character Data Extraction (pakchunk-locres missing).**
  - **Resolution:** The game files are incomplete. Verify game files integrity in Steam or Epic Games.
- **Symptom: Database is corrupted ("unexpected end of json input" when uploading image) or mods disappear entirely.**
  - **Resolution:** Open `%APPDATA%/RivalNxt` and use a DB browser to delete the last corrupted image row, **OR** perform a **Clean Reinstall**.
- **How to perform a Clean Reinstall:**
  1. Uninstall RivalNxt via Windows Control Panel.
  2. When prompted, check "Delete application data".
  3. Complete the uninstall and reinstall the latest version. This will safely remove corrupted data without touching your downloaded mods.

---

## 12. Keyboard Shortcuts & Hidden Features

- **`Enter` or `Space`**: While focused on a Mod Card, opens the Mod Details Modal.
- **Drag & Drop Images**: You can drag and drop any `.png` or `.jpg` image from your computer directly onto the Mod Details Images tab to set a custom thumbnail.
- **Drag & Drop Mods**: Drag a downloaded `.zip` or `.rar` anywhere into the main window to instantly install it.
- **Hidden Avatar Actions**: Clicking on the Author's Avatar in the Mod Card opens an interactive popover. If it's a Nexus author, it links to their profile. If it's a local mod, you can type a custom author name directly.
- **Double Click**: Double clicking a mod card opens the Mod Details modal.

---

## 13. Supported File Types

- **Archives**: `.zip`, `.rar` (Handled natively by `rust-ue-tools` compression engines).
- **Game Files**: `.pak`, `.utoc`, `.ucas`, `.sig` (Unreal Engine 5 IoStore formats).
- **Images**: `.jpg`, `.jpeg`, `.png`, `.webp` (For custom thumbnails).

---

## 14. Internal Terminology

- **Library Sync**: The process of cross-referencing the SQLite database with the actual files on the hard drive to correct drift.
- **Smart Bootstrap**: The startup sequence that detects game updates, rebuilds the character/skin database, and synchronizes the library.
- **Two-Tier Crash Matching**: The crash detector algorithm parsing Unreal Engine `.log` files to match either exact asset path strings or broad character ID references to isolate faulty mods.
- **Variant**: A specific internal file within a mod (e.g., a Blue Suit vs a Red Suit). Represented in the UI as the hierarchical file tree in the Mod Details modal.
- **Custom Tag**: A user-defined string attached to a mod for personal organization.
- **Generated Tag**: A tag automatically applied by the backend during the `UE Scan` by analyzing the internal `.pak` file paths (e.g., guessing it's a "Spider-Man" mod because the path contains `Hero_SpiderMan`).
- **UE Scan**: The `rust-ue-tools` backend process that unpacks `.pak` headers without extracting the whole file to read the internal asset directory map.
- **NXM**: The custom URL protocol (`nxm://`) used by NexusMods to send download commands to desktop managers.
- **Collection**: A curated list of mods on NexusMods that can be downloaded sequentially.
- **Backup**: A JSON/SQLite snapshot of the user's active mod list, allowing them to switch between entirely different "loadouts" or recover after a fresh game install.
