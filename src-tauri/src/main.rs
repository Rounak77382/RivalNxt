#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs;
use std::sync::{Arc, Mutex};

use rfd::AsyncFileDialog;
use tauri::{AppHandle, Manager};
use rust_ue_tools::{Unpacker, PakUnpackOptions, UtocListOptions};
#[cfg(any(target_os = "linux", target_os = "macos", target_os = "windows"))]
use tauri_plugin_shell::process::CommandEvent;
#[cfg(any(target_os = "linux", target_os = "macos", target_os = "windows"))]
use tauri_plugin_shell::ShellExt;

#[cfg(target_os = "windows")]
use winreg::enums::*;
#[cfg(target_os = "windows")]
use winreg::RegKey;

#[derive(Clone, Default)]
struct BackendChild(Arc<Mutex<Option<tauri_plugin_shell::process::CommandChild>>>);

#[derive(Clone)]
struct BackendConfig {
    port: u16,
}

fn get_available_port() -> Option<u16> {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|listener| listener.local_addr())
        .map(|addr| addr.port())
        .ok()
}

impl BackendChild {
    fn new() -> Self {
        Self::default()
    }

    fn set(&self, child: tauri_plugin_shell::process::CommandChild) {
        if let Ok(mut guard) = self.0.lock() {
            *guard = Some(child);
        }
    }

    fn kill(&self) {
        if let Ok(mut guard) = self.0.lock() {
            if let Some(child) = guard.take() {
                println!("[Backend] Killing backend process...");
                let _ = child.kill();
            }
        }
    }
}

// Tauri command to open folder selection dialog
#[tauri::command]
async fn select_folder_dialog(default_path: Option<String>) -> Result<String, String> {
    println!("[Dialog] Requested folder selection with default path: {:?}", default_path);
    
    match AsyncFileDialog::new()
        .set_title("Select Folder")
        .pick_folder()
        .await
    {
        Some(path) => {
            let path_string = path.path().to_string_lossy().to_string();
            println!("[Dialog] Selected folder: {}", path_string);
            Ok(path_string)
        }
        None => {
            println!("[Dialog] Folder selection cancelled");
            Err("Selection cancelled".to_string())
        }
    }
}

// Tauri command to open file selection dialog
#[tauri::command]
async fn select_file_dialog(default_path: Option<String>, filter_extensions: Option<Vec<String>>) -> Result<String, String> {
    println!("[Dialog] Requested file selection with default path: {:?}, extensions: {:?}", default_path, filter_extensions);
    
    let mut dialog = AsyncFileDialog::new()
        .set_title("Select File");
    
    // Add filters if provided
    if let Some(extensions) = filter_extensions {
        for ext in extensions {
            dialog = dialog.add_filter("Files", &[&ext]);
        }
    }
    
    match dialog.pick_file().await {
        Some(file) => {
            let path_string = file.path().to_string_lossy().to_string();
            println!("[Dialog] Selected file: {}", path_string);
            Ok(path_string)
        }
        None => {
            println!("[Dialog] File selection cancelled");
            Err("Selection cancelled".to_string())
        }
    }
}

#[cfg(target_os = "windows")]
use std::path::Path;
#[cfg(target_os = "windows")]
use std::env;

// Windows-specific: Find installation directory from registry
#[cfg(target_os = "windows")]
fn find_install_dir(display_name_part: &str) -> Option<String> {
    let display_name_part = display_name_part.to_lowercase();
    let uninstall_keys = [
        (HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (HKEY_CURRENT_USER, r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ];

    for (root_key, subkey_path) in &uninstall_keys {
        if let Ok(key) = RegKey::predef(*root_key).open_subkey(subkey_path) {
            for i in 0.. {
                match key.enum_keys().nth(i as usize) {
                    Some(Ok(subkey_name)) => {
                        if let Ok(subkey) = key.open_subkey(&subkey_name) {
                            // Get DisplayName
                            if let Ok(name) = subkey.get_value::<String, &str>("DisplayName") {
                                if name.to_lowercase().contains(&display_name_part) {
                                    // Try InstallLocation first
                                    if let Ok(install_loc) = subkey.get_value::<String, &str>("InstallLocation") {
                                        if !install_loc.is_empty() && Path::new(&install_loc).exists() {
                                            return Some(install_loc);
                                        }
                                    }
                                    
                                    // Fallback: use UninstallString and strip exe
                                    if let Ok(uninstall_str) = subkey.get_value::<String, &str>("UninstallString") {
                                        let path = uninstall_str.trim_matches('"');
                                        if let Some(dir_path) = Path::new(path).parent() {
                                            let dir_str = dir_path.to_string_lossy().to_string();
                                            if Path::new(&dir_str).exists() {
                                                return Some(dir_str);
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Some(Err(_)) => continue,
                    None => break,
                }
            }
        }
    }
    None
}

// Windows-specific: Add directory to user PATH
#[cfg(target_os = "windows")]
fn persist_add_to_user_path(dir_path: &str) -> Result<(bool, String), String> {
    if !Path::new(dir_path).exists() {
        return Ok((false, "Invalid directory path".to_string()));
    }

    let key_path = r"Environment";
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    
    let key = hkcu.open_subkey_with_flags(key_path, KEY_READ | KEY_SET_VALUE)
        .map_err(|e| format!("Failed to open Environment key: {}", e))?;
    
    let current: String = match key.get_value("Path") {
        Ok(path) => path,
        Err(_) => String::new(),
    };
    
    let paths: Vec<&str> = if current.is_empty() {
        Vec::new()
    } else {
        current.split(';').collect()
    };
    
    if paths.contains(&dir_path) {
        return Ok((true, "Already in user PATH".to_string()));
    }
    
    let new_value = if current.is_empty() {
        dir_path.to_string()
    } else {
        format!("{};{}", dir_path, current)
    };
    
    key.set_value("Path", &new_value)
        .map_err(|e| format!("Failed to set PATH: {}", e))?;

    Ok((false, "Added to user PATH".to_string()))
}

// Tauri command to detect archive tool (WinRAR) - Pure Rust implementation
#[tauri::command]
async fn detect_archive_tool() -> Result<serde_json::Value, String> {
    #[cfg(not(target_os = "windows"))]
    {
        return Ok(serde_json::json!({
            "success": false,
            "message": "Archive tool detection is only supported on Windows",
            "already_in_path": false
        }));
    }
    
    #[cfg(target_os = "windows")]
    {
        // Try WinRAR first (preferred)
        if let Some(winrar_dir) = find_install_dir("winrar") {
            let rar_exe = Path::new(&winrar_dir).join("rar.exe");
            let winrar_exe = Path::new(&winrar_dir).join("WinRAR.exe");
            
            let executable = if rar_exe.exists() {
                rar_exe.to_string_lossy().to_string()
            } else if winrar_exe.exists() {
                winrar_exe.to_string_lossy().to_string()
            } else {
                return Ok(serde_json::json!({
                    "success": false,
                    "message": "WinRAR installation found but executables not detected",
                    "already_in_path": false
                }));
            };
            
            match persist_add_to_user_path(&winrar_dir) {
                Ok((already_in_path, _message)) => {
                    let path_status = if already_in_path {
                        "already in user PATH".to_string()
                    } else {
                        "added to user PATH (persistent)".to_string()
                    };
                    
                    return Ok(serde_json::json!({
                        "success": true,
                        "name": "WinRAR",
                        "path": winrar_dir,
                        "executable": executable,
                        "already_in_path": already_in_path,
                        "path_status": path_status,
                        "message": format!("WinRAR detected at {} and {}", winrar_dir, path_status)
                    }));
                }
                Err(e) => {
                    return Ok(serde_json::json!({
                        "success": false,
                        "message": format!("WinRAR found but PATH update failed: {}", e),
                        "already_in_path": false
                    }));
                }
            }
        }
        
        // Not found
        Ok(serde_json::json!({
            "success": false,
            "message": "WinRAR installation not found",
            "already_in_path": false
        }))
    }
}

// Tauri command to detect Marvel Rivals game installation path
#[tauri::command]
async fn detect_marvel_rivals_path() -> Result<serde_json::Value, String> {
    #[cfg(not(target_os = "windows"))]
    {
        return Ok(serde_json::json!({
            "success": false,
            "message": "Marvel Rivals auto-detection is only supported on Windows",
            "path": None::<String>
        }));
    }
    
    #[cfg(target_os = "windows")]
    {
        // Try Steam registry key first
        if let Some(steam_path) = find_install_dir("steam") {
            let steamapps_dir = Path::new(&steam_path).join("steamapps");
            
            if steamapps_dir.exists() {
                // Look for common Marvel Rivals installation paths in Steam
                let candidates = [
                    steamapps_dir.join("common").join("MarvelRivals"),
                    steamapps_dir.join("common").join("Marvel Rivals"),
                    steamapps_dir.join("common").join("MarvelRivalsBeta"),
                ];
                
                for candidate in &candidates {
                    if candidate.exists() && candidate.is_dir() {
                        // Verify this looks like a Marvel Rivals installation
                        if candidate.join("MarvelRivals.exe").exists() || 
                           candidate.join("MarvelRivals").exists() {
                            return Ok(serde_json::json!({
                                "success": true,
                                "path": candidate.to_string_lossy().to_string(),
                                "message": format!("Marvel Rivals found at: {}", candidate.display())
                            }));
                        }
                    }
                }
            }
        }
        
        // Try Epic Games Store registry key
        if let Some(epic_path) = find_install_dir("epic games") {
            let epic_games_dir = Path::new(&epic_path);
            
            if epic_games_dir.exists() {
                // Look for Marvel Rivals in Epic Games installations
                let candidates = [
                    epic_games_dir.join("MarvelRivals"),
                    epic_games_dir.join("Marvel Rivals"),
                ];
                
                for candidate in &candidates {
                    if candidate.exists() && candidate.is_dir() {
                        if candidate.join("MarvelRivals.exe").exists() || 
                           candidate.join("MarvelRivals").exists() {
                            return Ok(serde_json::json!({
                                "success": true,
                                "path": candidate.to_string_lossy().to_string(),
                                "message": format!("Marvel Rivals found at: {}", candidate.display())
                            }));
                        }
                    }
                }
            }
        }
        
        // Direct registry lookup for Marvel Rivals
        if let Some(game_path) = find_install_dir("marvel rivals") {
            let game_dir = Path::new(&game_path);
            
            if game_dir.exists() && game_dir.is_dir() {
                return Ok(serde_json::json!({
                    "success": true,
                    "path": game_dir.to_string_lossy().to_string(),
                    "message": format!("Marvel Rivals found at: {}", game_dir.display())
                }));
            }
        }
        
        Ok(serde_json::json!({
            "success": false,
            "message": "Marvel Rivals installation not found. Please install via Steam or Epic Games Store, or set the path manually.",
            "path": None::<String>
        }))
    }
}

// Tauri command to get archive tool information for Python backend
#[tauri::command]
fn get_archive_tool_info() -> Result<serde_json::Value, String> {
    #[cfg(not(target_os = "windows"))]
    {
        return Ok(serde_json::json!({
            "success": false,
            "message": "Archive tool detection is only supported on Windows",
            "rar_tool_path": None::<String>
        }));
    }
    
    #[cfg(target_os = "windows")]
    {
        // Reuse the same logic as detect_archive_tool but return the executable path
        if let Some(winrar_dir) = find_install_dir("winrar") {
            let rar_exe = Path::new(&winrar_dir).join("rar.exe");
            let winrar_exe = Path::new(&winrar_dir).join("WinRAR.exe");
            
            let executable = if rar_exe.exists() {
                rar_exe.to_string_lossy().to_string()
            } else if winrar_exe.exists() {
                winrar_exe.to_string_lossy().to_string()
            } else {
                return Ok(serde_json::json!({
                    "success": false,
                    "message": "WinRAR installation found but executables not detected",
                    "rar_tool_path": None::<String>
                }));
            };
            
            // Try to add to PATH if not already there (don't fail if it can't)
            let _ = persist_add_to_user_path(&winrar_dir);
            
            return Ok(serde_json::json!({
                "success": true,
                "name": "WinRAR",
                "path": winrar_dir,
                "executable": executable,
                "rar_tool_path": executable,
                "message": format!("WinRAR found at: {}", executable)
            }));
        }
        
        // Not found
        Ok(serde_json::json!({
            "success": false,
            "message": "WinRAR installation not found",
            "rar_tool_path": None::<String>
        }))
    }
}
// Tauri command to get sidecar paths (now using rust-ue-tools library)
#[tauri::command]
fn get_sidecar_path(sidecar_name: String) -> Result<String, String> {
    // Return library info instead of external executable paths
    match sidecar_name.as_str() {
        "repak" => {
            Ok("Using rust-ue-tools library - no external executable needed".to_string())
        }
        "retoc_cli" => {
            Ok("Using rust-ue-tools library - no external executable needed".to_string())
        }
        _ => return Err(format!("Unknown tool: {}", sidecar_name)),
    }
}

// Tauri command to unpack a pak file using rust-ue-tools
#[tauri::command]
async fn unpack_pak_file(pak_path: String, output_dir: String, aes_key: Option<String>) -> Result<Vec<String>, String> {
    let mut unpacker = Unpacker::new();
    let options = PakUnpackOptions::new()
        .with_aes_key(aes_key.unwrap_or_default())
        .with_strip_prefix("../../../")
        .with_force(true)
        .with_quiet(true);

    match unpacker.unpack_pak(std::path::Path::new(&pak_path), std::path::Path::new(&output_dir), &options) {
        Ok(asset_paths) => {
            let paths: Vec<String> = asset_paths.into_iter().map(|p| p.as_str().to_string()).collect();
            Ok(paths)
        }
        Err(e) => Err(format!("Failed to unpack pak file: {}", e)),
    }
}

// Tauri command to list contents of a utoc file using rust-ue-tools
#[tauri::command]
async fn list_utoc_file(utoc_path: String, aes_key: Option<String>) -> Result<Vec<String>, String> {
    let mut unpacker = Unpacker::new();
    let options = UtocListOptions::new()
        .with_aes_key(aes_key.unwrap_or_default())
        .with_json_format(false);

    match unpacker.list_utoc(std::path::Path::new(&utoc_path), &options) {
        Ok(asset_paths) => {
            let paths: Vec<String> = asset_paths.into_iter().map(|p| p.as_str().to_string()).collect();
            Ok(paths)
        }
        Err(e) => Err(format!("Failed to list utoc file: {}", e)),
    }
}

// Tauri command to extract asset paths from an archive file using rust-ue-tools
#[tauri::command]
async fn extract_asset_paths_from_archive(archive_path: String, aes_key: Option<String>) -> Result<Vec<String>, String> {
    let mut unpacker = Unpacker::new();

    match unpacker.extract_asset_paths_from_archive(&archive_path, aes_key.as_deref(), false) {
        Ok(asset_paths) => {
            let paths: Vec<String> = asset_paths.into_iter().map(|p| p.as_str().to_string()).collect();
            Ok(paths)
        }
        Err(e) => Err(format!("Failed to extract asset paths from archive: {}", e)),
    }
}

// Tauri command to run unpacking operation with custom options
#[tauri::command]
async fn unpack_pak_file_advanced(
    pak_path: String,
    output_dir: String,
    aes_key: Option<String>,
    force: bool,
    quiet: bool,
    strip_prefix: Option<String>,
) -> Result<serde_json::Value, String> {
    let mut unpacker = Unpacker::new();
    
    let mut options = PakUnpackOptions::new()
        .with_force(force)
        .with_quiet(quiet);
    
    if let Some(ref key) = aes_key {
        options = options.with_aes_key(key);
    }
    
    if let Some(ref prefix) = strip_prefix {
        options = options.with_strip_prefix(prefix);
    }

    match unpacker.unpack_pak(std::path::Path::new(&pak_path), std::path::Path::new(&output_dir), &options) {
        Ok(asset_paths) => {
            let result = serde_json::json!({
                "success": true,
                "files_extracted": asset_paths.len(),
                "output_directory": output_dir,
                "extracted_files": asset_paths.iter().map(|p| p.as_str()).collect::<Vec<_>>()
            });
            Ok(result)
        }
        Err(e) => {
            let error_result = serde_json::json!({
                "success": false,
                "error": e.to_string()
            });
            Ok(error_result)
        }
    }
}

// Tauri command to run UTOC listing with detailed output
#[tauri::command]
async fn list_utoc_file_detailed(
    utoc_path: String,
    aes_key: Option<String>,
    json_format: bool,
) -> Result<serde_json::Value, String> {
    let mut unpacker = Unpacker::new();
    let mut options = UtocListOptions::new()
        .with_json_format(json_format);
    
    if let Some(ref key) = aes_key {
        options = options.with_aes_key(key);
    }

    match unpacker.list_utoc(std::path::Path::new(&utoc_path), &options) {
        Ok(asset_paths) => {
            let result = serde_json::json!({
                "success": true,
                "asset_count": asset_paths.len(),
                "file": utoc_path,
                "assets": asset_paths.iter().map(|p| p.as_str()).collect::<Vec<_>>()
            });
            Ok(result)
        }
        Err(e) => {
            let error_result = serde_json::json!({
                "success": false,
                "error": e.to_string(),
                "file": utoc_path
            });
            Ok(error_result)
        }
    }
}

// Tauri command to run CLI commands directly using rust-ue-tools library
#[tauri::command]
async fn run_cli_command(
    command: String,
    args: Vec<String>,
) -> Result<serde_json::Value, String> {
    let mut unpacker = Unpacker::new();
    
    match command.as_str() {
        "unpack" => {
            // Args: pak_file, output_dir, [aes_key], [force], [quiet]
            if args.len() < 2 {
                return Err("Usage: unpack <pak_file> <output_dir> [aes_key] [force] [quiet]".to_string());
            }
            
            let pak_file = &args[0];
            let output_dir = &args[1];
            let aes_key = args.get(2).map(|s| s.as_str());
            let force = args.get(3).map(|s| s == "true").unwrap_or(false);
            let quiet = args.get(4).map(|s| s == "true").unwrap_or(false);
            
            let mut options = PakUnpackOptions::new()
                .with_force(force)
                .with_quiet(quiet);
            
            if let Some(key) = aes_key {
                options = options.with_aes_key(key);
            }
            
            match unpacker.unpack_pak(std::path::Path::new(pak_file), std::path::Path::new(output_dir), &options) {
                Ok(asset_paths) => {
                    Ok(serde_json::json!({
                        "command": command,
                        "files_extracted": asset_paths.len(),
                        "output_directory": output_dir,
                        "status": "success",
                        "extracted_files": asset_paths.iter().map(|p| p.as_str()).collect::<Vec<_>>()
                    }))
                }
                Err(e) => Err(format!("Failed to unpack pak file: {}", e))
            }
        }
        "retoc" => {
            // Args: list <utoc_file> [json] [aes_key]
            if args.len() < 2 {
                return Err("Usage: retoc list <utoc_file> [json] [aes_key]".to_string());
            }
            
            let utoc_file = &args[0];
            let json_format = args.get(1).map(|s| s == "true").unwrap_or(false);
            let aes_key = args.get(2).map(|s| s.as_str());
            
            let mut options = UtocListOptions::new()
                .with_json_format(json_format);
                
            if let Some(key) = aes_key {
                options = options.with_aes_key(key);
            }
            
            match unpacker.list_utoc(std::path::Path::new(utoc_file), &options) {
                Ok(asset_paths) => {
                    Ok(serde_json::json!({
                        "command": command,
                        "asset_count": asset_paths.len(),
                        "file": utoc_file,
                        "status": "success",
                        "assets": asset_paths.iter().map(|p| p.as_str()).collect::<Vec<_>>()
                    }))
                }
                Err(e) => Err(format!("Failed to list utoc file: {}", e))
            }
        }
        "extract" => {
            // Args: archive_file [aes_key] [keep_temp]
            if args.is_empty() {
                return Err("Usage: extract <archive_file> [aes_key] [keep_temp]".to_string());
            }
            
            let archive_file = &args[0];
            let aes_key = args.get(1).map(|s| s.as_str());
            let keep_temp = args.get(2).map(|s| s == "true").unwrap_or(false);
            
            match unpacker.extract_asset_paths_from_archive(archive_file, aes_key, keep_temp) {
                Ok(asset_paths) => {
                    Ok(serde_json::json!({
                        "command": command,
                        "asset_count": asset_paths.len(),
                        "archive_file": archive_file,
                        "status": "success",
                        "assets": asset_paths.iter().map(|p| p.as_str()).collect::<Vec<_>>()
                    }))
                }
                Err(e) => Err(format!("Failed to extract from archive: {}", e))
            }
        }
        _ => Err(format!("Invalid command: {}. Valid commands: unpack, retoc, extract", command))
    }
}

// Tauri command to get UE tools library version and capabilities
#[tauri::command]
async fn get_ue_tools_info() -> Result<serde_json::Value, String> {
    let info = serde_json::json!({
        "version": "1.0.0",
        "name": "UE Tools Rust Library",
        "implementation": "Pure Rust (no external executables required)",
        "capabilities": {
            "pak_unpacking": true,
            "utoc_listing": true,
            "archive_extraction": true,
            "aes_encryption": true,
            "compression_support": ["Zlib", "Gzip", "Oodle", "Zstd", "Lz4"],
            "output_formats": ["text", "json"],
            "cli_tools": ["ue-tools", "repak", "retoc"]
        },
        "supported_formats": {
            "input": [".pak", ".utoc", ".zip", ".rar"],
            "output": ["directory", "json"]
        },
        "features": [
            "Force overwrite",
            "Quiet mode", 
            "AES key support",
            "Progress reporting",
            "Parallel processing",
            "File validation",
            "Batch processing"
        ]
    });
    
    Ok(info)
}

// Tauri command to get detailed information about a PAK file
#[tauri::command]
async fn get_pak_file_info(pak_path: String, aes_key: Option<String>) -> Result<serde_json::Value, String> {
    let mut unpacker = Unpacker::new();
    
    let mut options = PakUnpackOptions::new().with_quiet(true);
    if let Some(ref key) = aes_key {
        options = options.with_aes_key(key);
    }
    
    match std::path::Path::new(&pak_path).exists() {
        false => Err(format!("PAK file not found: {}", pak_path)),
        true => {
            // For now, use the list function to get file information
            match unpacker.pak_unpacker.list_files(&pak_path, &options) {
                Ok(file_paths) => {
                    let pak_file = std::path::Path::new(&pak_path);
                    let metadata = pak_file.metadata()
                        .map_err(|e| format!("Failed to get file metadata: {}", e))?;
                    
                    Ok(serde_json::json!({
                        "file_path": pak_path,
                        "file_size": metadata.len(),
                        "modified": metadata.modified()
                            .map(|t| format!("{:?}", t))
                            .unwrap_or_else(|_| "unknown".to_string()),
                        "file_count": file_paths.len(),
                        "encryption": aes_key.is_some(),
                        "file_names": file_paths.iter().map(|p| p.as_str()).collect::<Vec<_>>(),
                        "status": "success"
                    }))
                }
                Err(e) => Err(format!("Failed to analyze PAK file: {}", e))
            }
        }
    }
}

// Tauri command to extract specific files from a PAK file
#[tauri::command]
async fn extract_files_from_pak(
    pak_path: String,
    output_dir: String,
    file_patterns: Option<Vec<String>>,
    aes_key: Option<String>
) -> Result<serde_json::Value, String> {
    let mut unpacker = Unpacker::new();
    
    let mut options = PakUnpackOptions::new()
        .with_force(true)
        .with_quiet(false);
        
    if let Some(ref key) = aes_key {
        options = options.with_aes_key(key);
    }
    
    // Apply file patterns if provided (skip pattern validation for now)
    // TODO: Implement proper pattern filtering in future version
    if let Some(ref _patterns) = file_patterns {
        // Pattern matching would require importing glob crate directly
        // For now, we'll extract all files without filtering
        println!("Pattern filtering not yet implemented in Tauri commands");
    }
    
    match unpacker.unpack_pak(std::path::Path::new(&pak_path), std::path::Path::new(&output_dir), &options) {
        Ok(asset_paths) => {
            Ok(serde_json::json!({
                "pak_file": pak_path,
                "output_directory": output_dir,
                "files_extracted": asset_paths.len(),
                "extracted_files": asset_paths.iter().map(|p| p.as_str()).collect::<Vec<_>>(),
                "status": "success"
            }))
        }
        Err(e) => Err(format!("Failed to extract files from PAK: {}", e))
    }
}

// Tauri command to batch process multiple files
#[tauri::command]
async fn batch_process_ue_files(
    file_paths: Vec<String>,
    operation: String,
    aes_key: Option<String>,
    output_dir: Option<String>
) -> Result<serde_json::Value, String> {
    let mut unpacker = Unpacker::new();
    let mut results = Vec::new();
    
    match operation.as_str() {
        "list" => {
            for file_path in &file_paths {
                let path = std::path::Path::new(file_path);
                
                if !path.exists() {
                    results.push(serde_json::json!({
                        "file": file_path,
                        "status": "error",
                        "error": "File not found"
                    }));
                    continue;
                }
                
                match path.extension().and_then(|e| e.to_str()) {
                    Some("pak") => {
                        let mut options = PakUnpackOptions::new().with_quiet(true);
                        if let Some(ref key) = aes_key {
                            options = options.with_aes_key(key);
                        }
                        
                        match unpacker.pak_unpacker.list_files(std::path::Path::new(file_path), &options) {
                            Ok(files) => {
                                results.push(serde_json::json!({
                                    "file": file_path,
                                    "type": "pak",
                                    "file_count": files.len(),
                                    "status": "success",
                                    "files": files.iter().map(|p| p.as_str()).collect::<Vec<_>>()
                                }));
                            }
                            Err(e) => {
                                results.push(serde_json::json!({
                                    "file": file_path,
                                    "type": "pak",
                                    "status": "error",
                                    "error": e.to_string()
                                }));
                            }
                        }
                    }
                    Some("utoc") => {
                        let mut options = UtocListOptions::new().with_json_format(false);
                        if let Some(ref key) = aes_key {
                            options = options.with_aes_key(key);
                        }
                        
                        match unpacker.list_utoc(std::path::Path::new(file_path), &options) {
                            Ok(assets) => {
                                results.push(serde_json::json!({
                                    "file": file_path,
                                    "type": "utoc",
                                    "asset_count": assets.len(),
                                    "status": "success",
                                    "assets": assets.iter().map(|p| p.as_str()).collect::<Vec<_>>()
                                }));
                            }
                            Err(e) => {
                                results.push(serde_json::json!({
                                    "file": file_path,
                                    "type": "utoc",
                                    "status": "error", 
                                    "error": e.to_string()
                                }));
                            }
                        }
                    }
                    _ => {
                        results.push(serde_json::json!({
                            "file": file_path,
                            "status": "error",
                            "error": "Unsupported file type"
                        }));
                    }
                }
            }
        }
        "extract" => {
            if let Some(ref output) = output_dir {
                for file_path in &file_paths {
                    let path = std::path::Path::new(file_path);
                    
                    if !path.exists() {
                        results.push(serde_json::json!({
                            "file": file_path,
                            "status": "error",
                            "error": "File not found"
                        }));
                        continue;
                    }
                    
                    let file_output = std::path::Path::new(output).join(
                        path.file_stem().unwrap_or_default()
                    );
                    
                    match path.extension().and_then(|e| e.to_str()) {
                        Some("pak") => {
                            let mut options = PakUnpackOptions::new()
                                .with_force(true)
                                .with_quiet(true);
                            if let Some(ref key) = aes_key {
                                options = options.with_aes_key(key);
                            }
                            
                            match unpacker.unpack_pak(std::path::Path::new(file_path), file_output.as_path(), &options) {
                                Ok(assets) => {
                                    results.push(serde_json::json!({
                                        "file": file_path,
                                        "output": file_output.to_string_lossy(),
                                        "files_extracted": assets.len(),
                                        "status": "success"
                                    }));
                                }
                                Err(e) => {
                                    results.push(serde_json::json!({
                                        "file": file_path,
                                        "status": "error",
                                        "error": e.to_string()
                                    }));
                                }
                            }
                        }
                        _ => {
                            results.push(serde_json::json!({
                                "file": file_path,
                                "status": "error",
                                "error": "Only PAK files can be extracted"
                            }));
                        }
                    }
                }
            } else {
                return Err("Output directory required for extract operation".to_string());
            }
        }
        _ => return Err(format!("Unsupported operation: {}. Supported: list, extract", operation))
    }
    
    Ok(serde_json::json!({
        "operation": operation,
        "file_count": file_paths.len(),
        "results": results,
        "success_count": results.iter().filter(|r| r["status"] == "success").count(),
        "error_count": results.iter().filter(|r| r["status"] == "error").count()
    }))
}

// Tauri command to validate UE files
#[tauri::command]
async fn validate_ue_file(file_path: String, aes_key: Option<String>) -> Result<serde_json::Value, String> {
    let path = std::path::Path::new(&file_path);
    
    if !path.exists() {
        return Err("File not found".to_string());
    }
    
    if !path.is_file() {
        return Err("Path is not a file".to_string());
    }
    
    let file_type = match path.extension().and_then(|e| e.to_str()) {
        Some("pak") => "pak",
        Some("utoc") => "utoc", 
        Some("zip") | Some("rar") => "archive",
        Some(_) => "unknown",
        None => "unknown"
    };
    
    let metadata = path.metadata()
        .map_err(|e| format!("Failed to get file metadata: {}", e))?;
    
    let mut validation = serde_json::json!({
        "file_path": file_path,
        "file_type": file_type,
        "file_size": metadata.len(),
        "readable": true,
        "valid": true,
        "encryption": aes_key.is_some()
    });
    
    // Test if file can be opened
    match file_type {
        "pak" => {
            let mut unpacker = Unpacker::new();
            let mut options = PakUnpackOptions::new().with_quiet(true);
            if let Some(ref key) = aes_key {
                options = options.with_aes_key(key);
            }
            
            match unpacker.pak_unpacker.list_files(std::path::Path::new(&file_path), &options) {
                Ok(_) => {
                    validation["can_read"] = serde_json::Value::Bool(true);
                    validation["message"] = serde_json::Value::String("PAK file is valid and readable".to_string());
                }
                Err(e) => {
                    validation["can_read"] = serde_json::Value::Bool(false);
                    validation["valid"] = serde_json::Value::Bool(false);
                    validation["message"] = serde_json::Value::String(format!("PAK validation failed: {}", e));
                }
            }
        }
        "utoc" => {
            let mut unpacker = Unpacker::new();
            let mut options = UtocListOptions::new().with_json_format(false);
            if let Some(ref key) = aes_key {
                options = options.with_aes_key(key);
            }
            
            match unpacker.list_utoc(std::path::Path::new(&file_path), &options) {
                Ok(_) => {
                    validation["can_read"] = serde_json::Value::Bool(true);
                    validation["message"] = serde_json::Value::String("UTOC file is valid and readable".to_string());
                }
                Err(e) => {
                    validation["can_read"] = serde_json::Value::Bool(false);
                    validation["valid"] = serde_json::Value::Bool(false);
                    validation["message"] = serde_json::Value::String(format!("UTOC validation failed: {}", e));
                }
            }
        }
        "archive" => {
            validation["message"] = serde_json::Value::String("Archive file detected".to_string());
        }
        "unknown" => {
            validation["valid"] = serde_json::Value::Bool(false);
            validation["message"] = serde_json::Value::String("Unknown file type".to_string());
        }
        _ => {
            validation["valid"] = serde_json::Value::Bool(false);
            validation["message"] = serde_json::Value::String("Unsupported file type".to_string());
        }
    }
    
    Ok(validation)
}

// Tauri command to get the current executable path
#[tauri::command]
fn get_executable_path() -> Result<String, String> {
    std::env::current_exe()
        .map(|p| p.to_string_lossy().to_string())
        .map_err(|e| format!("Failed to get executable path: {}", e))
}

// Windows-specific: Ensure NXM protocol is registered with proper quoting
// This fixes the issue where ampersands in URLs get split by Windows shell
#[cfg(target_os = "windows")]
fn ensure_nxm_protocol_registration() -> Result<(), String> {
    let exe_path = std::env::current_exe()
        .map_err(|e| format!("Failed to get executable path: {}", e))?;
    
    let exe_str = exe_path.to_string_lossy().to_string();
    
    // The critical fix: Use proper quoting so the full URL is passed as ONE argument
    // Format: "C:\Path\To\App.exe" "%1"
    // The "%1" in quotes ensures ampersands and other special chars are preserved
    let command_value = format!("\"{}\" \"%1\"", exe_str);
    
    println!("[NXM Protocol] Registering with command: {}", command_value);
    
    // Register under HKEY_CURRENT_USER (per-user, no admin needed)
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    
    // Create/open: HKCU\Software\Classes\nxm
    let (nxm_key, _) = hkcu
        .create_subkey(r"Software\Classes\nxm")
        .map_err(|e| format!("Failed to create nxm key: {}", e))?;
    
    // Set default value: "URL:nxm"
    nxm_key
        .set_value("", &"URL:nxm")
        .map_err(|e| format!("Failed to set nxm description: {}", e))?;
    
    // Set URL Protocol marker (empty string value)
    nxm_key
        .set_value("URL Protocol", &"")
        .map_err(|e| format!("Failed to set URL Protocol: {}", e))?;
    
    // Create: HKCU\Software\Classes\nxm\shell\open\command
    let (command_key, _) = hkcu
        .create_subkey(r"Software\Classes\nxm\shell\open\command")
        .map_err(|e| format!("Failed to create command key: {}", e))?;
    
    // Set default value with PROPER QUOTING: "C:\Path\To\App.exe" "%1"
    command_key
        .set_value("", &command_value)
        .map_err(|e| format!("Failed to set command value: {}", e))?;
    
    println!("[NXM Protocol] Successfully registered nxm:// protocol");
    println!("[NXM Protocol] Executable: {}", exe_str);
    println!("[NXM Protocol] Command registered: {}", command_value);
    
    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn ensure_nxm_protocol_registration() -> Result<(), String> {
    // Non-Windows platforms use Tauri's built-in deep-link plugin
    println!("[NXM Protocol] Using Tauri deep-link plugin (non-Windows)");
    Ok(())
}

// Tauri command to get the backend port
#[tauri::command]
fn get_backend_port(config: tauri::State<BackendConfig>) -> u16 {
    config.port
}

// Tauri command to handle NXM protocol URLs
#[tauri::command]
async fn handle_nxm_url(url: String, app_handle: AppHandle) -> Result<String, String> {
    println!("Received NXM URL: {}", url);
    
    // Forward the NXM URL to the backend API
    let client = reqwest::Client::new();
    let port = app_handle.state::<BackendConfig>().port;
    let backend_url = format!("http://127.0.0.1:{}/api/nxm/handoff", port);
    
    let payload = serde_json::json!({
        "nxm": url
    });
    
    match client.post(backend_url)
        .json(&payload)
        .send()
        .await
    {
        Ok(response) => {
            if response.status().is_success() {
                Ok(format!("NXM URL forwarded to backend: {}", url))
            } else {
                Err(format!("Backend returned error: {}", response.status()))
            }
        }
        Err(e) => Err(format!("Failed to contact backend: {}", e))
    }
}

async fn launch_backend(app_handle: AppHandle, backend_state: BackendChild) -> Result<(), String> {
    #[cfg(any(target_os = "linux", target_os = "macos", target_os = "windows"))]
    {
        // Set environment variable so Python backend can find the Tauri executable
        let exe_path = std::env::current_exe()
            .map_err(|e| format!("Failed to get executable path: {}", e))?;
        
        // Detect archive tools and set environment variables for Python backend
        #[cfg(target_os = "windows")]
        {
            // Try to get archive tool info
            match get_archive_tool_info() {
                Ok(archive_info) => {
                    if let Some(rar_tool_path) = archive_info.get("rar_tool_path").and_then(|v| v.as_str()) {
                        std::env::set_var("RAR_TOOL_PATH", rar_tool_path);
                        println!("[Archive Tool] Set RAR_TOOL_PATH={}", rar_tool_path);
                    }
                }
                Err(e) => {
                    println!("[Archive Tool] Failed to detect archive tools: {}", e);
                }
            }
        }
        std::env::set_var("TAURI_APP_PATH", exe_path.to_string_lossy().to_string());
        
        // In debug mode, use Python directly for live code updates
        // In release mode, use the compiled sidecar
        let use_python_direct = cfg!(debug_assertions);
        
        if use_python_direct {
            println!("[DEV MODE] Running backend from Python source for live updates");
            
            // Find Python executable
            let python_cmd = if cfg!(target_os = "windows") {
                "python"
            } else {
                "python3"
            };
            
            // Get the workspace root (parent of src-tauri directory)
            let exe_path = std::env::current_exe().map_err(|e| format!("Failed to get exe path: {e}"))?;
            let mut current = exe_path.parent();
            
            // Navigate up until we find src-tauri directory, then go one more level up
            let workspace_root = loop {
                match current {
                    Some(dir) => {
                        if dir.file_name().and_then(|n| n.to_str()) == Some("src-tauri") {
                            break dir.parent().ok_or("No workspace root")?;
                        }
                        current = dir.parent();
                    }
                    None => return Err("Could not find workspace root".to_string()),
                }
            };
            
            let python_script = workspace_root.join("src-python").join("run_server.py");
            
            println!("[DEV] Workspace root: {}", workspace_root.display());
            println!("[DEV] Looking for Python script at: {}", python_script.display());
            
            // Get PID for heartbeat monitoring (same as production)
            let current_pid = std::process::id();
            println!("[DEV] Current frontend PID: {}", current_pid);
            println!("[DEV] Setting RIVALNXT_PID={} for backend monitoring", current_pid);
            
            if !python_script.exists() {
                eprintln!("Python script not found at: {}", python_script.display());
                eprintln!("Falling back to sidecar...");
                // Fall through to sidecar logic below
            } else {
                match app_handle.shell().command(python_cmd)
                    .args(["-X", "utf8", python_script.to_str().unwrap()])
                    .env("MM_BACKEND_HOST", "127.0.0.1")
                    .env("MM_BACKEND_PORT", app_handle.state::<BackendConfig>().port.to_string())
                    .env("PYTHONPATH", workspace_root.to_str().unwrap())
                    .env("RIVALNXT_PID", current_pid.to_string())
                    .envs(if let Ok(data_dir) = app_handle.path().app_data_dir() {
                        println!("Using MODMANAGER_DATA_DIR at {}", data_dir.display());
                        if let Err(err) = fs::create_dir_all(&data_dir) {
                            eprintln!("Failed to create app data directory: {err}");
                        }
                        if let Some(path_str) = data_dir.to_str() {
                            std::env::set_var("MODMANAGER_DATA_DIR", path_str);
                            vec![("MODMANAGER_DATA_DIR".to_string(), path_str.to_string())]
                        } else {
                            vec![]
                        }
                    } else {
                        vec![]
                    })
                    .spawn()
                {
                    Ok((mut receiver, child)) => {
                        backend_state.set(child);
                        tauri::async_runtime::spawn(async move {
                            while let Some(event) = receiver.recv().await {
                                match event {
                                    CommandEvent::Stdout(line) => {
                                        let bytes: Vec<u8> = line.into();
                                        let text = String::from_utf8_lossy(&bytes);
                                        println!("[backend] {text}");
                                    }
                                    CommandEvent::Stderr(line) => {
                                        let bytes: Vec<u8> = line.into();
                                        let text = String::from_utf8_lossy(&bytes);
                                        eprintln!("[backend] {text}");
                                    }
                                    CommandEvent::Error(line) => {
                                        let bytes: Vec<u8> = line.into();
                                        let text = String::from_utf8_lossy(&bytes);
                                        eprintln!("[backend error] {text}");
                                    }
                                    _ => {}
                                }
                            }
                        });
                        return Ok(());
                    }
                    Err(err) => {
                        eprintln!("Failed to spawn Python backend: {err}");
                        eprintln!("Falling back to sidecar...");
                        // Fall through to sidecar logic below
                    }
                }
            }
        }
        
        // Production mode or fallback: use compiled sidecar
        println!("[PRODUCTION MODE] Using compiled sidecar");
    
    // Get the current process ID to pass to the backend for heartbeat monitoring
    let current_pid = std::process::id();
    println!("[App] Current PID: {}", current_pid);

    match app_handle.shell().sidecar("rivalnxt_backend") {
            Ok(command) => {
                let mut command = command
                    .env("MM_BACKEND_HOST", "127.0.0.1")
                    .env("MM_BACKEND_PORT", app_handle.state::<BackendConfig>().port.to_string())
                    .env("RIVALNXT_PID", current_pid.to_string());

                if let Ok(data_dir) = app_handle.path().app_data_dir() {
                    println!("[PRODUCTION] App data directory: {}", data_dir.display());
                    if let Err(err) = fs::create_dir_all(&data_dir) {
                        eprintln!("[PRODUCTION] Failed to create app data directory: {err}");
                    } else {
                        println!("[PRODUCTION] Successfully created/verified app data directory");
                    }
                    if let Some(path_str) = data_dir.to_str() {
                        println!("[PRODUCTION] Setting MODMANAGER_DATA_DIR={}", path_str);
                        command = command.env("MODMANAGER_DATA_DIR", path_str);
                        // Also set it as a process-wide environment variable for any child processes
                        std::env::set_var("MODMANAGER_DATA_DIR", path_str);
                    } else {
                        eprintln!("[PRODUCTION] Failed to convert data_dir path to string");
                    }
                } else {
                    eprintln!("[PRODUCTION] Failed to get app_data_dir from Tauri");
                }

                match command.spawn() {
                    Ok((mut receiver, child)) => {
                        backend_state.set(child);
                        tauri::async_runtime::spawn(async move {
                            while let Some(event) = receiver.recv().await {
                                match event {
                                    CommandEvent::Stdout(line) => {
                                        let bytes: Vec<u8> = line.into();
                                        let text = String::from_utf8_lossy(&bytes);
                                        println!("[backend] {text}");
                                    }
                                    CommandEvent::Stderr(line) => {
                                        let bytes: Vec<u8> = line.into();
                                        let text = String::from_utf8_lossy(&bytes);
                                        eprintln!("[backend] {text}");
                                    }
                                    CommandEvent::Error(line) => {
                                        let bytes: Vec<u8> = line.into();
                                        let text = String::from_utf8_lossy(&bytes);
                                        eprintln!("[backend error] {text}");
                                    }
                                    _ => {}
                                }
                            }
                        });
                    }
                    Err(err) => {
                        eprintln!("Failed to spawn backend sidecar: {err}");
                    }
                }
            }
            Err(err) => {
                eprintln!("Sidecar unavailable: {err}");
            }
        }
    }

    Ok(())
}

fn main() {
    let port = get_available_port().unwrap_or(8000);
    println!("[Backend Config] dynamically assigned port: {}", port);

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_single_instance::init(|app, args, cwd| {
            // This is called when a second instance is launched
            println!("Second instance detected!");
            println!("Args: {:?}", args);
            println!("CWD: {}", cwd);
            
            // Focus the existing window
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_focus();
                let _ = window.unminimize();
                let _ = window.show();
            }
            
            // Handle nxm:// URLs from the second instance
            for arg in args.iter().skip(1) {  // Skip the executable path
                if arg.starts_with("nxm://") {
                    let url = arg.clone();
                    let app_handle = app.clone();
                    tauri::async_runtime::spawn(async move {
                        if let Err(e) = handle_nxm_url(url.clone(), app_handle).await {
                            eprintln!("Failed to handle NXM URL from second instance: {}", e);
                        }
                    });
                }
            }
        }))
        .manage(BackendChild::new())
        .manage(BackendConfig { port })
        .invoke_handler(tauri::generate_handler![
            get_executable_path,
            handle_nxm_url,
            get_backend_port,
            select_folder_dialog,
            select_file_dialog,
            detect_archive_tool,
            detect_marvel_rivals_path,
            get_archive_tool_info,
            get_sidecar_path,
            unpack_pak_file,
            list_utoc_file,
            extract_asset_paths_from_archive,
            unpack_pak_file_advanced,
            list_utoc_file_detailed,
            get_ue_tools_info,
            run_cli_command,
            get_pak_file_info,
            extract_files_from_pak,
            batch_process_ue_files,
            validate_ue_file
        ])
        .setup(|app| {
            // CRITICAL FIX: Register NXM protocol with proper quoting on Windows
            // This ensures the full URL (including &key=...&expires=...) is passed intact
            #[cfg(target_os = "windows")]
            {
                if let Err(e) = ensure_nxm_protocol_registration() {
                    eprintln!("[NXM Protocol] WARNING: Failed to register protocol: {}", e);
                    eprintln!("[NXM Protocol] Deep links may not work correctly!");
                } else {
                    println!("[NXM Protocol] Successfully ensured proper registration with quoting");
                }
            }
            
            // Register deep link handler for nxm:// protocol
            #[cfg(any(target_os = "windows", target_os = "linux", target_os = "macos"))]
            {
                use tauri_plugin_deep_link::DeepLinkExt;
                app.deep_link().register_all()?;
                
                let handle = app.handle().clone();
                app.deep_link().on_open_url(move |event| {
                    // Get URLs once (consumes event)
                    let urls = event.urls();
                    
                    // Convert URLs to strings for logging and processing
                    let url_strings: Vec<String> = urls
                        .iter()
                        .map(|u| u.to_string())
                        .collect();
                    
                    println!("Deep link received: {}", url_strings.join(", "));
                    
                    // Handle NXM URLs
                    for url_str in url_strings {
                        if url_str.starts_with("nxm://") {
                            let handle_clone = handle.clone();
                            tauri::async_runtime::spawn(async move {
                                if let Err(e) = handle_nxm_url(url_str, handle_clone).await {
                                    eprintln!("Failed to handle NXM URL: {}", e);
                                }
                            });
                        }
                    }
                });
            }
            
            let handle = app.handle().clone();
            let backend_state = app.state::<BackendChild>().inner().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(err) = launch_backend(handle, backend_state).await {
                    eprintln!("Failed to start backend: {err}");
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| match event {
            tauri::RunEvent::ExitRequested { .. } => {
                println!("[App] Exit requested, cleaning up...");
                let backend = app_handle.state::<BackendChild>();
                backend.kill();
            }
            _ => {}
        });
}
