/**
 * Utility functions for Tauri-specific operations
 */

import { open } from "@tauri-apps/plugin-shell";
import { invoke } from "@tauri-apps/api/core";

// Extend Window interface to include __TAURI__
declare global {
  interface Window {
    __TAURI__?: unknown;
  }
}

/**
 * Opens a URL in the default browser.
 * Works in both Tauri desktop app and web browser.
 *
 * @param url - The URL to open
 * @returns Promise that resolves when the URL is opened
 */
export async function openInBrowser(url: string): Promise<void> {
  // Check if we're running in Tauri
  if (window.__TAURI__) {
    console.log(`[Tauri] Opening URL in default browser: ${url}`);
    try {
      await open(url);
      console.log(`[Tauri] Successfully opened URL`);
    } catch (error) {
      console.error("[Tauri] Failed to open URL with shell.open:", error);
      throw error;
    }
  } else {
    // Web browser mode - use traditional window.open
    console.log(`[Web] Opening URL in new tab: ${url}`);
    const popup = window.open(url, "_blank", "noopener,noreferrer");
    if (popup) {
      popup.opener = null;
      console.log(`[Web] Successfully opened URL`);
    } else {
      // Popup was blocked - try fallback method
      console.warn("[Web] Popup blocked, trying fallback method");
      try {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        anchor.style.display = "none";
        document.body?.appendChild(anchor);
        anchor.click();
        document.body?.removeChild(anchor);
        console.log(`[Web] Fallback method executed`);
      } catch (fallbackErr) {
        console.error("[Web] Fallback method failed:", fallbackErr);
        throw new Error(
          "Failed to open URL - popup was blocked and fallback failed"
        );
      }
    }
  }
}

/**
 * Check if running in Tauri desktop app
 */
export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI__" in window;
}

// ─── Backup File I/O ──────────────────────────────────────────────────────────

/**
 * Opens a native save-file dialog and returns the chosen path.
 * Returns null if the user cancels.
 */
export async function invokeSaveFileDialog(
  defaultName: string,
  extensions: string[] = ["json"]
): Promise<string | null> {
  try {
    const path = await invoke<string>("save_file_dialog", {
      defaultName,
      filterExtensions: extensions,
    });
    return path;
  } catch (err: any) {
    // "Selection cancelled" is expected – not an error
    if (String(err).includes("cancelled")) return null;
    throw err;
  }
}

/**
 * Opens a native open-file dialog and returns the chosen path.
 * Returns null if the user cancels.
 */
export async function invokeOpenFileDialog(
  extensions: string[] = ["json"]
): Promise<string | null> {
  try {
    const path = await invoke<string>("select_file_dialog", {
      defaultPath: null,
      filterExtensions: extensions,
    });
    return path;
  } catch (err: any) {
    if (String(err).includes("cancelled")) return null;
    throw err;
  }
}

/**
 * Writes text content to a file at the given absolute path.
 */
export async function invokeSaveTextFile(
  path: string,
  content: string
): Promise<void> {
  await invoke("save_text_file", { path, content });
}

/**
 * Reads text content from a file at the given absolute path.
 */
export async function invokeReadTextFile(path: string): Promise<string> {
  return await invoke<string>("read_text_file", { path });
}
