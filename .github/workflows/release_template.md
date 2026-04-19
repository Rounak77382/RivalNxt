## 🎉 Version 0.5.6 – What's New

### ✨ New Features

- 🎮 **Quick Launch Button** — Start Marvel Rivals directly from the app's top menu with a single click.
- 🔌 **Dynamic Backend Ports** — The local Python backend now automatically binds to dynamic, available ports instead of being hardcoded to port 8000. This completely eliminates "address already in use" errors and connectivity issues.
- ⚡ **Lazy Loaded Interface** — Drastically improved application performance. Mod cards now utilize an Intersection Observer to seamlessly lazy-load content, guaranteeing buttery-smooth scrolling even when you have hundreds of mods installed.
- ✨ **Better UI Visuals** — Mod cards now have slick interactive hover effects when you move your mouse over them.
- 🪪 **Sign in with Nexus** — Connect your Nexus Mods account directly from Settings — no need to copy and paste confusing API keys anymore.
- 🌟 **Favourites** — Star your favourite mods so you can find them instantly.
- 🔞 **NSFW Blur Filter** — A toggle in the top menu lets you blur adult-content mod images across the entire app.
- 🔄 **Update Checker** — Each mod now features a "Check for Update" button that tells you if a newer version is available.
- 📂 **Drag & Drop Install** — Just drag a downloaded mod (zip, rar, 7z) directly into the app to install it!
- 🔍 **Conflict Details** — Click any mod in the warnings list to immediately see its full details.
- 🔎 **Smarter Search & Sorting** — Clear your searches quickly, and sort your list by Name, Favourites, or the newly added **Uploaded Date**.
- 🚀 **Game Update Detection** — Automatically detects when Marvel Rivals updates and ensures your mods are still ready to go.
- 📁 **Advanced Mod Options** — Easily pick and choose specific parts of a mod (like colored variants) using a clean, new folder view.
- ⏱️ **Better Conflict Sorting** — Mod conflicts are now displayed with the newest issues first, making them easier to manage.
- 🏷️ **Clear Alerts** — Download notifications now show the actual, readable name of the mod instead of a confusing string of numbers.
- 🗂️ **Cleaner Menus** — The character skin filters in the sidebar have been redesigned into neat, space-saving buttons.

### 🐛 Bug Fixes & Under the Hood

- **Mod Toggle Fix** — Fixed bugs where complex mods wouldn't turn on properly, or where turning on one mod might accidentally affect another.
- **Accurate Update Badges** — The app no longer mistakenly tells you a mod has an update when you already have the right file.
- **Antivirus False Alarms** — Prevented security software from falsely flagging the app as unsafe on some computers.
- **Instant Image Updates** — When you add a new custom thumbnail to a mod, it instantly updates on your screen.
- **Invisible Scrollbars** — Fixed an issue where the scrollbar in the mod details menu was completely hidden.
- **Squashed Images** — Fixed some mod icons appearing stretched and distorted in warnings.
- **No More Duplicate Downloads** — If a mod is already downloaded, it'll gracefully skip it instead of downloading it twice.
- **Mod List Refreshes** — The installed mods list now reliably updates when background changes are made.
- **Cleaner Folders** — The app automatically cleans up leftover empty folders behind the scenes to keep your computer tidy.
- **Skin Filter Fix** — Skins with the same name across different characters now filter correctly.
- **Nexus Link Fix** — Clicking "Mod Manager Download" on Nexus Mods now correctly opens RivalNxt instead of failing.

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
