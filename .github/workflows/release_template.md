## 🎉 Version 0.5.6 – What's New

### ✨ New Features

- 🪪 **Sign in with Nexus** — You can now sign in to your Nexus Mods account directly from Settings — no need to manually copy and paste an API key.
- 🌟 **Favourites** — Star your favourite mods so you can find them instantly. Sort your list by favourites with one click.
- 🔞 **NSFW Blur Filter** — A toggle in the header lets you blur adult-content mod images across the whole app.
- 🔄 **Update Checker** — Each mod card now has a "Check for Update" button. It'll let you know if a newer version is available on Nexus Mods.
- 📂 **Drag & Drop Install** — Just drag a mod archive (zip, rar, 7z) straight into the app to install it. No file browsing needed.
- 🔍 **Conflict Details** — Click any mod in the conflict list to instantly see its full details without leaving the screen.
- 🔎 **Smarter Search & Sorting** — Added a quick-clear button on the search bar. Sorting options are now in a tidy dropdown, with options for Name, Favourites, and a newly added **Uploaded** sort option.
- 🚀 **Game Update Auto-detection** — Automatically detects Marvel Rivals updates on startup, rebuilding character/skin data if game files have changed.
- 📁 **Mod Variants & File Tree** — Added a hierarchical file tree UI to easily select and toggle specific `.pak` files for mods with complex nested subfolder variants.
- ⏱️ **Better Conflict Sorting** — Mod conflicts are now properly sorted by when they were first detected (newest first). Additionally, mod cards inside each conflict are sorted by install/update date.
- 🏷️ **NXM Download Details** — Nexus Mod download notifications now cleanly show the actual mod name instead of a numeric ID.
- 🗂️ **UI Components Revamp** — Skin filters in the sidebar have been refactored into clean, collapsible button groups.

### 🐛 Bug Fixes & Under the Hood

- **Nested Pak Activation Fix** — Fixed a bug where mods inside nested subfolders wouldn't activate correctly gracefully utilizing smarter fallback path strategies.
- **Empty Folder Cleanup** — Automatically cleans up leftover empty subdirectories after deactivating mods.
- **False-Positive Updates** — Fixed false-positive update badges; the "Update" badge now only appears when the latest release matches the downloaded mod variant.
- **No more duplicate downloads** — If a mod is already downloaded, it'll be skipped with a friendly notice instead of downloading again.
- **Mod list now refreshes properly** — The installed mods list now updates correctly after you add or remove a custom image.
- **Skin filter fix** — Skins with the same name across different characters (like "The Life Fantastic") now filter correctly.
- **Cleaner filter sidebar** — Fixed a visual glitch with misaligned checkboxes in the sidebar filters.
- **Nexus link fix** — Clicking "Mod Manager Download" on Nexus Mods now correctly opens RivalNxt on Windows.

### 📥 Installation

### Quick Start

1. Download `<INSTALLER_FILENAME>` from the downloads table below
2. Run the installer
3. Launch RivalNxt from your Start Menu
4. Configure in Settings:
   - **Local downloads directory** → Select the folder `where your Marvel Rivals mods are downloaded/saved (create anywhere or use existing folder)
   - **Sign in with Nexus** → Click the sign-in button to connect your Nexus Mods account automatically
   - _Or_ **Nexus Personal API Key** → Paste your [API key](https://next.nexusmods.com/settings/api-keys) manually if you prefer

> 📖 **Need more help?** See the [full installation guide](https://github.com/Rounak77382/RivalNxt?tab=readme-ov-file#-installation) for detailed setup instructions.

### 📥 Downloads

| File                             | Platform    | Checksum              |
| -------------------------------- | ----------- | --------------------- |
| [installer_filename](INSTALLER_URL) | x64 Windows | [checksum](CHECKSUM_URL) |

> To verify the download on Windows, run `certutil -hashfile <filename> SHA256` and compare it with the `.sha256` file.

---

**Questions or issues?** Please report bugs or feature requests via [GitHub Issues](https://github.com/Rounak77382/RivalNxt/issues).
