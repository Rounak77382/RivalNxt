## 🎉 Version 0.7.2 – What's New

### 🚀 New Features

* 📦 **Nexus Collections Support**: Added full support for downloading and integrating Nexus Collections. This ships with a dedicated Collections page, native NXM URI parsing, and robust import endpoints.
* 💾 **Backup & Restore Framework**: Implemented comprehensive backup and restore capabilities, allowing users to safeguard their configurations. This includes native Tauri file dialog integrations and distinct visual indicators.
* 🏷️ **Intelligent Custom Tag Engine**: Expanded the tag recommendation system to dynamically aggregate suggestions from custom tags, pak-extracted tag metadata, official characters, and official skins. The mod detail modal features a redesigned "+ Add Tag" button with a highly performant suggestion dropdown limited to 50 items.
* 🧹 **Outdated Mod Version Cleanup**: Added a setting/task (`delete_outdated_versions`) to automatically scan, delete, and clean up older variants and versions of downloaded mods from both local disk storage and the SQLite database.
* 📥 **Drag-and-Drop Image Uploads**: Introduced native Tauri webview listeners to seamlessly handle drag-and-drop actions for mod images. Supports multi-image HTML5 drops directly onto the application UI.
* 🔄 **Persistent Handoff Tracking**: Downloads and mod handoffs are now persistently tracked using SQLite. The system automatically handles failed downloads, incorporating retry limits and background checking logic to ensure reliability.
* 🌐 **Dynamic Fandom Wiki Skin Resolver**: Integrated an automated fallback crawler that queries the Marvel Rivals Fandom Wiki API to dynamically resolve unlocalized new skin names at extraction time.

### 🐛 Bug Fixes & Under the Hood

* 🔒 **Nexus SSL Bypass**: Resolved metadata fetching failures by globally disabling SSL certificate verification context for Nexus REST and GraphQL API calls to bypass expired cert issues.
* 📢 **Accurate Update Banners**: Integrated a strict version normalization comparison to eliminate false-positive update notifications, and added dynamic display of download variant filenames when they differ from the mod name.
* 💾 **Stable Backup & Restore Layout**: Enforced a stable width boundary (`w-full max-w-lg`) on the `BackupRestoreModal` component, successfully eliminating visual layout-shifting and width fluctuations during the restore process.
* ️ **Faster Image Previews**: Optimized mod preview images to load thumbnail versions, reducing dashboard latency and image loading lag.

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

| File                                  | Platform    | Checksum                 |
| ------------------------------------- | ----------- | ------------------------ |
| [<INSTALLER_FILENAME>](<INSTALLER_URL>) | x64 Windows | [checksum](<CHECKSUM_URL>) |

> To verify the download on Windows, run `certutil -hashfile <INSTALLER_FILENAME> SHA256` and compare it with the `.sha256` file.

---

**Questions or issues?** Please report bugs or feature requests via [GitHub Issues](https://github.com/Rounak77382/RivalNxt/issues).
