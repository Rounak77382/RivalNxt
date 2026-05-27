## 🎉 Version 0.7.0 – What's New

### 🚀 New Features

* 📦 **Nexus Collections Support**: Added full support for downloading and integrating Nexus Collections. This ships with a dedicated Collections page, native NXM URI parsing, and robust import endpoints.
* 💾 **Backup & Restore Framework**: Implemented comprehensive backup and restore capabilities, allowing users to safeguard their configurations. This includes native Tauri file dialog integrations and distinct visual indicators.
* 📥 **Drag-and-Drop Image Uploads**: Introduced native Tauri webview listeners to seamlessly handle drag-and-drop actions for mod images. Supports multi-image HTML5 drops directly onto the application UI.
* 🔄 **Persistent Handoff Tracking**: Downloads and mod handoffs are now persistently tracked using SQLite. The system automatically handles failed downloads, incorporating retry limits and background checking logic to ensure reliability.
* 🌐 **Dynamic Fandom Wiki Skin Resolver**: Integrated an automated fallback crawler that queries the Marvel Rivals Fandom Wiki API to dynamically resolve unlocalized new skin names (e.g. Hela's "Savage Monarch") at extraction time. This runs entirely offline-safe with automatic timeouts, completely eliminating generic "variant XXX" naming fallbacks.

### 🐛 Bug Fixes & Under the Hood

* 🔗 **Reliable Nexus Links**: Overhauled the Nexus Mods link handler to make downloading mods via "Mod Manager Download" much more stable and reliable.
* ⚡ **Faster App Loading**: Optimized the mod conflict checking system, resulting in significantly faster dashboard loading times and smoother performance.
* 🏷️ **Smarter Skin Parsing**: Improved detection for character and skin names, including support for newly added skins and cleaner file name displays.
* 📢 **Accurate Update Banners**: Added clear "What's New" update notifications and fixed cases where update alerts didn't show up due to file name differences.
* 🖼️ **Faster Image Previews**: Optimized mod preview images to load thumbnail versions, reducing dashboard latency and image loading lag.

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
