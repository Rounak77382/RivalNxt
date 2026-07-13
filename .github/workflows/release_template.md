## 🎉 Version 0.8.0 – What's New

### 🚀 New Features

* 💾 **Backup & Restore Framework**: Implemented comprehensive backup and restore capabilities, allowing users to safeguard their configurations. This includes native Tauri file dialog integrations and distinct visual indicators.
* 💥 **Crash Log Parsing & Detection**: Implemented a persistent crash detection modal with two-tier exact/character matching and a log parsing utility to quickly identify faulty mods causing game crashes.
* 👤 **Custom Author Enhancements**: Enhanced custom author support, including them in global search indexing and increasing popover search limits.
* 🔍 **Custom Tag Search Integration**: Custom tags are now fully indexed for search and have been added to the downloads sidebar filter for deeper organizational control.
* 🧹 **Outdated Mod Version Cleanup**: Added a setting/task to automatically scan, delete, and clean up older variants and versions of downloaded mods from both local disk storage and the SQLite database.
* 📥 **Drag-and-Drop Image Uploads**: Introduced native Tauri webview listeners to seamlessly handle drag-and-drop actions for mod images. Supports multi-image HTML5 drops directly onto the application UI.
* 🔄 **Game Update Auto-Detection**: Integrated a Game Update Modal that automatically detects Marvel Rivals game updates on startup and dynamically rebuilds character, skin data, and tags.
* 📂 **Hierarchical File Tree UI**: Introduced a new nested file tree renderer in the Mod Modal for mods with subfolder variants, enabling granular pak activation for complex folder structures.
* 🌐 **Dynamic Fandom Wiki Skin Resolver**: Integrated an automated fallback crawler that queries the Marvel Rivals Fandom Wiki API to dynamically resolve unlocalized new skin names at extraction time.
* 🔍 **Enhanced Sorting Options**: Added a new "Uploaded" sort option across search, browse, and download views, and updated the conflict list to sort by detection timestamp.
* 🧠 **AI Knowledge Graph (Graphify)**: Integrated a cross-language AST knowledge graph generation tool (`graphify`) for enhanced AI agent assistance and updated project manifest generation.


### 🐛 Bug Fixes & Under the Hood

* 🔒 **Nexus SSL Bypass**: Resolved metadata fetching failures by globally disabling SSL certificate verification context for Nexus REST and GraphQL API calls to bypass expired cert issues.
* 🐛 **Pak Toggle Conflict Fix**: Resolved state issues related to pak toggling to ensure reliable mod activation.
* 🔌 **Dynamic Port Assignment**: Implemented dynamic port allocation for the backend to prevent startup conflicts.
* 🛠️ **Unified Duplicate Detection**: Hardened the ingestion pipeline with path-based duplication detection and unified naming conventions across all installation methods.
* 🚀 **Optimized Database Queries**: Overhauled the NXM handler and significantly optimized database conflict checks for smoother performance.
* 📢 **Accurate Update Banners**: Integrated a strict version normalization comparison to eliminate false-positive update notifications, and added dynamic display of download variant filenames when they differ from the mod name.
* 🔗 **Nexus Update Links**: Fixed broken Nexus update links and refined the overall visual aesthetics of mod cards.
* 🔧 **Assign Mod ID Fix**: Prevented an unnecessary "Assign Mod ID" prompt when API verification fails but the mod ID is already known locally.

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
