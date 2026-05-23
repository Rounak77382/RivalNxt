import { invoke } from "@tauri-apps/api/core";

export class ApiError extends Error {
  status: number;
  detail?: unknown;
  body?: unknown;

  constructor(
    message: string,
    options: { status: number; detail?: unknown; body?: unknown },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.detail = options.detail;
    this.body = options.body;
  }
}

export type ApiMod = {
  mod_id: number;
  name: string | null;
  author: string | null;
  version: string | null;
  icon: string | null;
  active_conflicting_assets: number;
  active_opposing_mods: number;
};

export type ApiAddModRequest = {
  localPath: string;
  name?: string;
  modId?: number;
  version?: string;
  sourceUrl?: string;
};

export type ApiAddModResponse = {
  ok: boolean;
  inserted: number;
  name: string;
  mod_id: number | null;
  version: string | null;
  path: string;
  contents: string[];
  ingested_paks?: number;
  ingested_assets?: number;
  ingest_warning?: string;
  source_url?: string;
  metadata_warning?: string;
  synced_mod_id?: number;
};

export type ApiUploadModResponse = {
  ok: boolean;
  path: string;
  filename: string;
  size: number;
  relative_path: string;
  downloads_root: string;
};

export type ApiConflictParticipantMod = {
  mod_id: number | null;
  mod_name: string | null;
  pak_file: string;
  icon: string | null;
  is_current: boolean;
  local_download_id?: number | null;
};

export type ApiConflictParticipant = {
  pak_name: string;
  merged_tag?: string | null;
  mods: ApiConflictParticipantMod[];
};

export type ApiConflict = {
  asset_path: string;
  category?: string | null;
  conflicting_mod_count: number;
  total_paks: number;
  winner_mod_id: number | null;
  participants: ApiConflictParticipant[];
  detected_at?: string | null;
};

export type ApiNxmDownloadProgress = {
  stage?: string;
  message?: string | null;
  bytes_downloaded?: number;
  bytes_total?: number | null;
  percent?: number | null;
  error?: string | null;
  updated_at?: number | null;
  retry_count?: number;
  permanently_failed?: boolean;
};

export type ApiNxmHandoffSummary = {
  id: string;
  created_at?: number | null;
  expires_at?: number | null;
  request?: {
    raw?: string;
    game?: string;
    mod_id?: number | null;
    file_id?: number | null;
    query?: Record<string, string>;
  } | null;
  metadata?: {
    mod_info?: Record<string, unknown> | null;
    fetched_at?: number | null;
  } | null;
  progress?: ApiNxmDownloadProgress | null;
};

export type ApiNxmHandoffList = {
  ok: boolean;
  handoffs: ApiNxmHandoffSummary[];
};

export type ApiNxmPreview = {
  ok: boolean;
  handoff: ApiNxmHandoffSummary;
  game: string;
  mod_info?: Record<string, unknown> | null;
  files?: Array<Record<string, unknown>>;
  selected_file_id?: number | null;
  selected_file?: Record<string, unknown> | null;
};

export type ApiNxmIngestOptions = {
  fileId?: number;
  desiredPaks?: string[];
  activate?: boolean;
  deactivateExisting?: boolean;
};

export type ApiNxmIngestResponse = {
  ok: boolean;
  handoff: ApiNxmHandoffSummary;
  mod_id: number;
  mod_name?: string | null;
  file_id: number;
  download_id: number;
  download: Record<string, unknown>;
  selected_file?: Record<string, unknown> | null;
  activated_paks: string[];
  activation_warning?: string | null;
  deactivated_download_ids: number[];
  deactivation_warnings?: string[];
  desired_active_paks?: string[];
  needs_refresh?: boolean;
  deactivated_existing?: boolean;
};

export type ApiSubmitNxmHandoffResponse = {
  ok: boolean;
  handoff: ApiNxmHandoffSummary;
};

export type ApiSettings = {
  backend_host: string;
  backend_port: number;
  data_dir: string | null;
  marvel_rivals_root: string | null;
  marvel_rivals_local_downloads_root: string | null;
  nexus_api_key: string;
  aes_key_hex: string;
  allow_direct_api_downloads: boolean;
  repak_bin: string | null;
  retoc_cli: string | null;
  seven_zip_bin: string | null;
  validation: ApiSettingsValidation;
};

export type ApiSettingPathValidation = {
  ok: boolean;
  message: string;
  path?: string | null;
  exists?: boolean;
  reason?: string | null;
  optional?: boolean;
};

export type ApiSettingsValidation = {
  data_dir: ApiSettingPathValidation;
  marvel_rivals_root: ApiSettingPathValidation;
  marvel_rivals_local_downloads_root: ApiSettingPathValidation;
  repak_bin: ApiSettingPathValidation;
  retoc_cli: ApiSettingPathValidation;
  seven_zip_bin: ApiSettingPathValidation;
  nexus_api_key: ApiSettingPathValidation;
};

export interface ApiUpdateSettingsRequest {
  data_dir?: string;
  marvel_rivals_root?: string | null;
  marvel_rivals_local_downloads_root?: string | null;
  nexus_api_key?: string;
  aes_key_hex?: string;
  allow_direct_api_downloads?: boolean;
  repak_bin?: string | null;
  retoc_cli?: string | null;
  seven_zip_bin?: string | null;
}

export type SettingsTask =
  | "ingest_download_assets"
  | "scan_active_mods"
  | "sync_nexus"
  | "rebuild_tags"
  | "rebuild_conflicts"
  | "bootstrap_rebuild"
  | "rebuild_character_data";

export type ApiSettingsTaskStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed";

export type ApiSettingsTaskResponse = {
  id: string;
  task: SettingsTask;
  status: ApiSettingsTaskStatus;
  ok: boolean | null;
  exit_code: number | null;
  error?: string | null;
  output: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  created_at?: string | null;
  updated_at?: string | null;
  metadata?: any;
};

export type ApiBootstrapStatus = {
  db_exists: boolean;
  settings_exists: boolean;
  db_path: string | null;
  settings_path: string | null;
  downloads_count: number;
  mods_count: number;
  schema_migrations: number;
  needs_bootstrap: boolean;
};

export type ApiHealthResponse = {
  ok: boolean;
  mods?: number;
  paks?: number;
  assets?: number;
  error?: string;
};

// Marvel Rivals Character and Skin Types
export type CharacterSkin = {
  variant: string;
  name: string;
};

export type Character = {
  character_id: string;
  name: string;
  skins: CharacterSkin[];
};

export type RebuildCharacterDataResponse = {
  success: boolean;
  message: string;
  characters_count: number;
  skins_count: number;
};

// Tag Lookup Types
export type TagInfo = {
  type: "character" | "skin";
  character_id?: string;
  parent?: string; // Parent character name for skins
  parents?: string[]; // All possible parents for disambiguation
};

export type TagLookupRequest = {
  tags: string[];
};

export type TagLookupResponse = Record<string, TagInfo>;

let cachedBaseUrl: string | null = null;

export async function getBaseUrl(): Promise<string> {
  if (cachedBaseUrl) return cachedBaseUrl;

  cachedBaseUrl = (import.meta as any).env?.VITE_API_BASE_URL || null;
  if (cachedBaseUrl) return cachedBaseUrl;

  try {
    if ((window as any).__TAURI_INTERNALS__) {
      const port = await invoke<number>("get_backend_port");
      if (port) {
        cachedBaseUrl = `http://127.0.0.1:${port}`;
        return cachedBaseUrl;
      }
    }
  } catch (e) {
    console.error("Failed to get dynamic backend port, falling back to 8000", e);
  }

  cachedBaseUrl = "http://127.0.0.1:8000";
  return cachedBaseUrl;
}

async function handleError(
  res: Response,
  method: string,
  path: string,
): Promise<never> {
  let message = `${method} ${path} failed: ${res.status}`;
  let parsedBody: unknown = undefined;
  let detail: unknown = undefined;
  try {
    const raw = await res.text();
    if (raw) {
      try {
        parsedBody = JSON.parse(raw);
        if (parsedBody && typeof parsedBody === "object") {
          const container = parsedBody as Record<string, unknown>;
          detail = container.detail ?? container.message ?? container.error;
          if (detail == null) {
            detail = parsedBody;
          }
        } else {
          detail = parsedBody;
        }
      } catch {
        parsedBody = raw;
        detail = raw;
      }
    }
  } catch {
    // ignore parsing failures and use fallback message
  }

  if (typeof detail === "string" && detail.trim().length > 0) {
    message = detail.trim();
  } else if (detail && typeof detail === "object") {
    const detailObj = detail as Record<string, unknown>;
    const maybeMessage = detailObj.message ?? detailObj.detail;
    if (typeof maybeMessage === "string" && maybeMessage.trim().length > 0) {
      message = maybeMessage.trim();
    } else {
      try {
        message = JSON.stringify(detailObj);
      } catch {
        message = `${method} ${path} failed: ${res.status}`;
      }
    }
  }

  throw new ApiError(message, {
    status: res.status,
    detail,
    body: parsedBody,
  });
}

// Debug logging helper - logs to backend
async function debugLog(message: string, data?: any, level: string = "INFO") {
  try {
    const baseUrl = await getBaseUrl();
    await fetch(`${baseUrl}/api/debug/log`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, data, level }),
    });
  } catch (e) {
    // Silently fail if debug logging doesn't work
  }
}

async function getJson<T>(path: string): Promise<T> {
  const baseUrl = await getBaseUrl();
  const res = await fetch(`${baseUrl}${path}`, {
    headers: {
      "Cache-Control": "no-cache",
      Pragma: "no-cache",
    },
  });
  if (!res.ok) {
    await handleError(res, "GET", path);
  }
  const data = await res.json();

  // Debug logging for mod details and changelogs
  if (path.includes("/api/mods/") && !path.includes("/files")) {
    const debugData = {
      status: res.status,
      hasData: !!data,
      dataKeys: data ? Object.keys(data) : [],
      // Log specific fields for debugging
      ...(path.endsWith("/changelogs")
        ? { changelogCount: Array.isArray(data) ? data.length : 0 }
        : {
            hasMod: !!(data as any)?.mod,
            hasDescription: !!(data as any)?.mod?.description,
            descriptionPreview: (data as any)?.mod?.description?.substring(
              0,
              100,
            ),
          }),
    };
    console.log(`[API] GET ${path}`, debugData);
    // Also send to backend for production debugging
    await debugLog(`GET ${path}`, debugData);
  }

  return data;
}

async function postJson<TReq, TRes>(path: string, body: TReq): Promise<TRes> {
  const baseUrl = await getBaseUrl();
  const res = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    await handleError(res, "POST", path);
  }
  return res.json();
}

async function patchJson<TReq, TRes>(path: string, body: TReq): Promise<TRes> {
  const baseUrl = await getBaseUrl();
  const res = await fetch(`${baseUrl}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    await handleError(res, "PATCH", path);
  }
  return res.json();
}

async function putJson<TReq, TRes>(path: string, body: TReq): Promise<TRes> {
  const baseUrl = await getBaseUrl();
  const res = await fetch(`${baseUrl}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    await handleError(res, "PUT", path);
  }
  return res.json();
}

async function deleteJson<TRes>(path: string): Promise<TRes> {
  const baseUrl = await getBaseUrl();
  const res = await fetch(`${baseUrl}${path}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    await handleError(res, "DELETE", path);
  }
  return res.json();
}

export async function listMods(limit = 100): Promise<ApiMod[]> {
  return getJson<ApiMod[]>(`/api/mods?limit=${limit}`);
}

export async function listConflicts(
  limit = 20,
  active = false,
): Promise<ApiConflict[]> {
  const path = active ? "/api/conflicts/active" : "/api/conflicts";
  return getJson<ApiConflict[]>(`${path}?limit=${limit}`);
}

export async function getHealth(): Promise<ApiHealthResponse> {
  return getJson<ApiHealthResponse>("/health");
}

export async function addMod(
  req: ApiAddModRequest,
): Promise<ApiAddModResponse> {
  return postJson<ApiAddModRequest, ApiAddModResponse>("/api/mods/add", req);
}

export async function uploadModFile(file: File): Promise<ApiUploadModResponse> {
  const form = new FormData();
  form.append("file", file);
  const baseUrl = await getBaseUrl();
  const res = await fetch(`${baseUrl}/api/mods/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    let message: string | undefined;
    try {
      const raw = await res.text();
      if (raw) {
        try {
          const parsed = JSON.parse(raw);
          message = parsed?.detail || parsed?.message || parsed?.error;
        } catch (err) {
          message = raw;
        }
      }
    } catch (err) {
      message = undefined;
    }
    throw new Error(
      message?.trim() || `Upload failed with status ${res.status}`,
    );
  }
  return res.json();
}

export async function refreshConflicts(): Promise<{ ok: boolean }> {
  return postJson<{}, { ok: boolean }>("/api/refresh/conflicts", {});
}

export async function copyToDownloads(
  sourcePath: string,
): Promise<ApiUploadModResponse> {
  return postJson<{ source_path: string }, ApiUploadModResponse>(
    "/api/mods/copy-to-downloads",
    { source_path: sourcePath },
  );
}

export async function getSettings(): Promise<ApiSettings> {
  return getJson<ApiSettings>("/api/settings");
}

export async function updateSettings(
  payload: ApiUpdateSettingsRequest,
): Promise<ApiSettings> {
  return putJson<ApiUpdateSettingsRequest, ApiSettings>(
    "/api/settings",
    payload,
  );
}

export async function runSettingsTask(
  task: SettingsTask,
): Promise<ApiSettingsTaskResponse> {
  return postJson<{ task: SettingsTask }, ApiSettingsTaskResponse>(
    "/api/settings/run-task",
    { task },
  );
}

export async function getSettingsTaskJob(
  jobId: string,
): Promise<ApiSettingsTaskResponse> {
  return getJson<ApiSettingsTaskResponse>(`/api/settings/tasks/${jobId}`);
}

export async function listSettingsTaskJobs(): Promise<
  ApiSettingsTaskResponse[]
> {
  return getJson<ApiSettingsTaskResponse[]>("/api/settings/tasks");
}

export async function getBootstrapStatus(): Promise<ApiBootstrapStatus> {
  return getJson<ApiBootstrapStatus>("/api/bootstrap/status");
}

export async function validatePath(
  field: string,
  value: string,
): Promise<{
  ok: boolean;
  message: string;
  exists: boolean;
  reason: string | null;
}> {
  return postJson<
    { field: string; value: string },
    {
      ok: boolean;
      message: string;
      exists: boolean;
      reason: string | null;
    }
  >("/api/settings/validate-path", { field, value });
}

// NXM Protocol Management
export type NxmProtocolStatus = {
  registered: boolean;
  tauri_path?: string | null;
  registered_path?: string | null;
  system: string;
  error?: string;
};

export async function getNxmProtocolStatus(): Promise<NxmProtocolStatus> {
  return getJson<NxmProtocolStatus>("/api/nxm/protocol/status");
}

export async function registerNxmProtocol(
  tauriPath: string,
): Promise<{ ok: boolean; message?: string; error?: string }> {
  return postJson<
    { tauri_path: string },
    { ok: boolean; message?: string; error?: string }
  >("/api/nxm/protocol/register", { tauri_path: tauriPath });
}

export async function unregisterNxmProtocol(): Promise<{
  ok: boolean;
  message?: string;
  error?: string;
}> {
  return postJson<
    Record<string, never>,
    { ok: boolean; message?: string; error?: string }
  >("/api/nxm/protocol/unregister", {});
}

export type LastNxmUrl = {
  ok: boolean;
  last_url: {
    url: string;
    received_at: string;
    parsed?: {
      game_domain: string;
      mod_id: number;
      file_id: number;
      query_params: Record<string, string>;
      has_key: boolean;
      has_expires: boolean;
      has_user_id: boolean;
    };
    parse_error?: string;
  } | null;
  message?: string;
};

export async function getLastNxmUrl(): Promise<LastNxmUrl> {
  return getJson<LastNxmUrl>("/api/nxm/last-received");
}

// Mod details
export type ApiModDetails = {
  mod?: {
    mod_id: number;
    name: string | null;
    author: string | null;
    version?: string | null;
    picture_url?: string | null;
    summary?: string | null;
    description?: string | null;
    description_bbcode?: string | null;
    mod_downloads?: number | null;
    mod_unique_downloads?: number | null;
    endorsement_count?: number | null;
  } | null;
  latest_file?: {
    file_id?: number;
    file_name?: string;
    file_version?: string;
    file_category?: string;
    file_size_in_bytes?: number;
    is_primary?: number | boolean;
    uploaded_at?: string;
    version_key?: string | null;
  } | null;
  local_count?: number;
  active_conflicting_assets?: number;
  active_opposing_mods?: number;
  tags?: string[];
};

export async function getModDetails(modId: number): Promise<ApiModDetails> {
  return getJson<ApiModDetails>(`/api/mods/${modId}`);
}

export async function updateModDetails(
  modId: number,
  data: { description?: string },
): Promise<{ ok: boolean }> {
  try {
    return patchJson<{ description?: string }, { ok: boolean }>(
      `/api/mods/${modId}`,
      data,
    );
  } catch (e) {
    console.warn("updateModDetails failed", e);
    throw e;
  }
}

export type ApiModFile = {
  file_id: number;
  name: string;
  version: string | null;
  category: string | null;
  size_in_bytes: number | null;
  is_primary: number | boolean | null;
  uploaded_at: string | null;
};

export async function getModFiles(modId: number): Promise<ApiModFile[]> {
  return getJson<ApiModFile[]>(`/api/mods/${modId}/files`);
}

export type ApiChangelog = {
  version: string | null;
  changelog: string | null;
  uploaded_at: string | null;
};

export async function getModChangelogs(modId: number): Promise<ApiChangelog[]> {
  return getJson<ApiChangelog[]>(`/api/mods/${modId}/changelogs`);
}

// Mod Images
export type ModImage = {
  id: number;
  source: "nexus" | "custom";
  url?: string; // For nexus images
  data?: string; // For custom images (base64)
  filename?: string;
  mimeType?: string;
  uploadedAt?: string;
};

export type ApiModImagesResponse = {
  ok: boolean;
  nexus_images: ModImage[];
  custom_images: ModImage[];
};

export async function fetchModImages(modId: number): Promise<ModImage[]> {
  const response = await getJson<ApiModImagesResponse>(
    `/api/mods/${modId}/images`,
  );
  return [...response.nexus_images, ...response.custom_images];
}

export async function uploadModImages(
  modId: number,
  files: File[],
): Promise<{ ok: boolean; uploaded_count: number; image_ids: number[] }> {
  // Convert files to base64
  const images = await Promise.all(
    files.map(async (file) => {
      const base64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          const result = reader.result as string;
          // Extract base64 data (remove data:image/...;base64, prefix)
          const base64Data = result.split(",")[1] || result;
          resolve(base64Data);
        };
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });

      return {
        data: base64,
        filename: file.name,
        mimeType: file.type,
      };
    }),
  );

  return postJson<
    { images: Array<{ data: string; filename: string; mimeType: string }> },
    { ok: boolean; uploaded_count: number; image_ids: number[] }
  >(`/api/mods/${modId}/images`, { images });
}

export async function uploadModImagesByPath(
  modId: number,
  paths: string[],
): Promise<{ ok: boolean; uploaded_count: number; image_ids: number[] }> {
  return postJson<{ paths: string[] }, { ok: boolean; uploaded_count: number; image_ids: number[] }>(
    `/api/mods/${modId}/images/upload-by-path`,
    { paths },
  );
}

export async function deleteModImage(
  imageId: number,
): Promise<{ ok: boolean; deleted_id: number }> {
  return deleteJson<{ ok: boolean; deleted_id: number }>(
    `/api/mods/images/${imageId}`,
  );
}

export async function getModCustomImagePreviews(
  modIds: number[],
): Promise<Record<number, string>> {
  if (!modIds || modIds.length === 0) {
    return {};
  }
  const idsParam = modIds.join(",");
  const response = await getJson<{
    ok: boolean;
    images: Record<string, string>;
  }>(`/api/mods/custom-images-preview?mod_ids=${encodeURIComponent(idsParam)}`);
  // Convert string keys to number keys
  const result: Record<number, string> = {};
  for (const [key, value] of Object.entries(response.images)) {
    result[Number(key)] = value;
  }
  return result;
}

// Downloads
export type ApiDownload = {
  id: number;
  name: string;
  mod_id: number | null;
  version: string | null;
  path: string;
  contents: string[];
  active_paks: string[];
  // Client-side aggregation helper: when grouping multiple local_downloads for the same mod,
  // keep track of which download rows were merged.
  source_download_ids?: number[];
  // Client-side aggregation helper: track all latest_file_ids across merged downloads
  source_file_ids?: number[];
  // Client-side aggregation helper: track all source paths across merged downloads
  source_paths?: string[];
  created_at: string;
  mod_name: string | null;
  mod_author: string | null;
  picture_url: string | null;
  tags: string[];
  mod_downloads?: number | null;
  endorsement_count?: number | null;
  mod_author_profile_url?: string | null;
  mod_author_member_id?: number | null;
  mod_author_avatar_url?: string | null;
  mod_created_time?: string | null;
  mod_updated_at?: string | null;
  download_id?: number;
  latest_version?: string | null;
  latest_uploaded_at?: string | null;
  latest_file_id?: number | null;
  latest_version_key?: string | null;
  latest_file_name?: string | null;
  local_version_key?: string | null;
  needs_update?: boolean;
  contains_adult_content?: boolean;
};

export interface ApiPakVersionStatus {
  pak_name: string;
  mod_id: number | null;
  source_zip: string | null;
  local_download_id: number | null;
  local_path: string | null;
  local_name: string | null;
  local_version: string | null;
  reference_file_id: number | null;
  reference_version: string | null;
  version_status:
    | "match"
    | "mismatch"
    | "missing_local_version"
    | "missing_remote_version";
  needs_update: boolean;
  display_version?: string | null;
}

export interface ApiPakAsset {
  pak_name: string;
  assets: string[];
}

export async function listDownloads(limit = 500): Promise<ApiDownload[]> {
  return getJson<ApiDownload[]>(`/api/downloads?limit=${limit}`);
}

export type ApiDownloadsSummary = {
  ok: boolean;
  total_size_bytes: number;
  total_size_human: string;
  download_count: number;
  missing_paths: string[];
  last_check?: string | null;
};

export async function getDownloadsSummary(): Promise<ApiDownloadsSummary> {
  return getJson<ApiDownloadsSummary>(`/api/downloads/summary`);
}

export async function setActivePaks(downloadId: number, active_paks: string[]) {
  return postJson<{ active_paks: string[] }, { ok: boolean }>(
    `/api/local_downloads/${downloadId}/set-active`,
    { active_paks },
  );
}

export async function scanActive(): Promise<{ ok: boolean }> {
  return postJson<{}, { ok: boolean }>(`/api/scan/active`, {});
}

export async function getLocalDownload(
  downloadId: number,
): Promise<ApiDownload> {
  return getJson<ApiDownload>(`/api/local_downloads/${downloadId}`);
}

export async function getPakVersionStatus(
  params: {
    modId?: number | null;
    downloadIds?: number[];
    onlyNeedsUpdate?: boolean;
  } = {},
): Promise<ApiPakVersionStatus[]> {
  const search = new URLSearchParams();
  if (params.modId != null) {
    search.set("mod_id", String(params.modId));
  }
  if (params.downloadIds && params.downloadIds.length > 0) {
    search.set("download_ids", params.downloadIds.join(","));
  }
  if (params.onlyNeedsUpdate) {
    search.set("only_needs_update", "true");
  }
  const query = search.toString();
  const path = query
    ? `/api/pak-version-status?${query}`
    : `/api/pak-version-status`;
  return getJson<ApiPakVersionStatus[]>(path);
}

// By-name activation/deactivation (server-side convenience endpoints)
export async function activateByName(
  name: string,
): Promise<{ ok: boolean } & { copied?: string[] }> {
  return postJson<{ name: string }, { ok: boolean; copied?: string[] }>(
    `/api/local_downloads/activate-by-name`,
    { name },
  );
}

export async function deactivateByName(
  name: string,
): Promise<{ ok: boolean } & { removed?: string[] }> {
  return postJson<{ name: string }, { ok: boolean; removed?: string[] }>(
    `/api/local_downloads/deactivate-by-name`,
    { name },
  );
}

export async function getPakAssets(
  downloadIds: number[],
): Promise<ApiPakAsset[]> {
  if (!downloadIds || downloadIds.length === 0) {
    return [];
  }
  const search = new URLSearchParams();
  search.set("download_ids", downloadIds.join(","));
  return getJson<ApiPakAsset[]>(`/api/pak-assets?${search.toString()}`);
}

export type ApiCheckModUpdateResponse = {
  ok: boolean;
  mod_id: number;
  needs_update: boolean;
  pending: Array<{
    pak_name?: string | null;
    local_download_id?: number | null;
    local_version?: string | null;
    reference_version?: string | null;
    version_status?: string | null;
    display_version?: string | null;
  }>;
  metadata_warning?: string;
  synced_mod_id?: number | null;
  checked_download_ids?: number[];
};

export type ApiUpdateModResponse = {
  ok: boolean;
  mod_id: number;
  mod_name?: string | null;
  latest_version: string;
  latest_file_id: number;
  latest_uploaded_at?: string | null;
  download_id: number;
  download: Record<string, unknown>;
  activated_paks: string[];
  activation_warning?: string | null;
  deactivated_download_ids: number[];
  deactivation_warnings?: string[];
  preflight_metadata?: Record<string, unknown>;
  local_versions: Array<Record<string, unknown>>;
  already_latest?: boolean;
  needs_refresh?: boolean;
};

export async function updateMod(
  modId: number,
  options: {
    fileId?: number;
    activate?: boolean;
    desiredPaks?: string[];
    force?: boolean;
    handoffId?: string;
  } = {},
): Promise<ApiUpdateModResponse> {
  const payload: Record<string, unknown> = {};
  if (typeof options.fileId === "number") payload.file_id = options.fileId;
  if (typeof options.activate === "boolean")
    payload.activate = options.activate;
  if (Array.isArray(options.desiredPaks))
    payload.desired_paks = options.desiredPaks;
  if (options.force) payload.force = true;
  if (typeof options.handoffId === "string" && options.handoffId.trim()) {
    payload.handoff_id = options.handoffId.trim();
  }
  return postJson<Record<string, unknown>, ApiUpdateModResponse>(
    `/api/mods/${modId}/update`,
    payload,
  );
}

export async function checkModUpdate(
  modId: number,
): Promise<ApiCheckModUpdateResponse> {
  return postJson<Record<string, never>, ApiCheckModUpdateResponse>(
    `/api/mods/${modId}/check-update`,
    {},
  );
}

export type DeleteLocalDownloadsResponse = {
  ok: boolean;
  deleted: number;
  removed_mod_ids: number[];
  removed_files?: string[];
  missing_files?: string[];
  failed_files?: string[];
};

export async function deleteLocalDownloads(
  downloadIds: number[],
  modId?: number | null,
): Promise<DeleteLocalDownloadsResponse> {
  const payload: Record<string, unknown> = {};
  if (Array.isArray(downloadIds) && downloadIds.length > 0) {
    payload.download_ids = downloadIds;
  }
  if (modId != null) {
    payload.mod_id = modId;
  }
  if (Object.keys(payload).length === 0) {
    throw new Error("At least one download id or mod id is required");
  }
  return postJson<Record<string, unknown>, DeleteLocalDownloadsResponse>(
    `/api/local_downloads/delete`,
    payload,
  );
}

export async function listNxmHandoffs(): Promise<ApiNxmHandoffSummary[]> {
  const response = await getJson<ApiNxmHandoffList>(`/api/nxm/handoffs`);
  return Array.isArray(response?.handoffs) ? response.handoffs : [];
}

export async function getNxmHandoff(
  handoffId: string,
): Promise<{ ok: boolean; handoff: ApiNxmHandoffSummary }> {
  const encoded = encodeURIComponent(handoffId);
  return getJson<{ ok: boolean; handoff: ApiNxmHandoffSummary }>(
    `/api/nxm/handoff/${encoded}`,
  );
}

export async function previewNxmHandoff(
  handoffId: string,
): Promise<ApiNxmPreview> {
  const encoded = encodeURIComponent(handoffId);
  return getJson<ApiNxmPreview>(`/api/nxm/handoff/${encoded}/preview`);
}

export async function ingestNxmHandoff(
  handoffId: string,
  options: ApiNxmIngestOptions = {},
): Promise<ApiNxmIngestResponse> {
  const payload: Record<string, unknown> = {};
  if (typeof options.fileId === "number") {
    payload.file_id = options.fileId;
  }
  if (Array.isArray(options.desiredPaks)) {
    payload.desired_paks = options.desiredPaks;
  }
  if (typeof options.activate === "boolean") {
    payload.activate = options.activate;
  }
  if (typeof options.deactivateExisting === "boolean") {
    payload.deactivate_existing = options.deactivateExisting;
  }
  const encoded = encodeURIComponent(handoffId);
  return postJson<Record<string, unknown>, ApiNxmIngestResponse>(
    `/api/nxm/handoff/${encoded}/ingest`,
    payload,
  );
}

// Character and Skin Data API
export async function getCharacters(): Promise<Character[]> {
  return getJson<Character[]>("/api/characters");
}

export async function getCharacterSkins(
  characterId: string,
): Promise<CharacterSkin[]> {
  return getJson<CharacterSkin[]>(`/api/characters/${characterId}/skins`);
}

export async function rebuildCharacterData(): Promise<RebuildCharacterDataResponse> {
  return postJson<{}, RebuildCharacterDataResponse>(
    "/api/rebuild-character-data",
    {},
  );
}

export async function lookupTags(tags: string[]): Promise<TagLookupResponse> {
  return postJson<TagLookupRequest, TagLookupResponse>(
    "/api/characters/lookup-tags",
    { tags },
  );
}

export async function dismissNxmHandoff(
  handoffId: string,
): Promise<ApiNxmHandoffSummary> {
  const encoded = encodeURIComponent(handoffId);
  const response = await deleteJson<{
    ok: boolean;
    handoff: ApiNxmHandoffSummary;
  }>(`/api/nxm/handoff/${encoded}`);
  return response.handoff;
}

export async function submitNxmHandoff(
  nxmUri: string,
): Promise<ApiSubmitNxmHandoffResponse> {
  return postJson<{ nxm: string }, ApiSubmitNxmHandoffResponse>(
    `/api/nxm/handoff`,
    { nxm: nxmUri },
  );
}

// Favourites
export async function toggleFavourite(
  modId: number,
): Promise<{ ok: boolean; favourited: boolean }> {
  return postJson<{ mod_id: number }, { ok: boolean; favourited: boolean }>(
    "/api/favourites/toggle",
    { mod_id: modId },
  );
}

export async function fetchFavourites(): Promise<number[]> {
  const response = await getJson<{ ok: boolean; mod_ids: number[] }>(
    "/api/favourites",
  );
  return response.mod_ids;
}

// Game Version Check
export type GameVersionCheckResponse = {
  ok: boolean;
  latest_modified: string | null;
  file_count: number;
  latest_file: string | null;
  error?: string;
};

export async function getGameVersionCheck(): Promise<GameVersionCheckResponse> {
  return getJson<GameVersionCheckResponse>("/api/game-version/check");
}

// ─── Collections API ──────────────────────────────────────────────────────────

export type ApiCollectionModFile = {
  id: number;
  entry_id: string;
  file_id: number;
  mod_id: number | null;
  optional: number; // 0 = required, 1 = optional
  version: string;
  file_name: string;
  file_uri: string;
  size_in_bytes: number | null;
  mod_name: string;
  picture_url: string;
  download_state: "pending" | "downloading" | "downloaded" | "failed";
};

export type ApiCollectionSummary = {
  id: number;
  slug: string;
  nexus_id: number | null;
  revision_num: number | null;
  game: string;
  name: string | null;
  summary: string | null;
  picture_url: string | null;
  author: string | null;
  total_mods: number | null;
  total_size: number | null;
  status: string | null;
  updated_at: string | null;
  fetched_at: string;
};

export type ApiCollection = ApiCollectionSummary & {
  revision_id: number | null;
  created_at: string | null;
  mod_files: ApiCollectionModFile[];
};

export async function listCollections(): Promise<ApiCollectionSummary[]> {
  const r = await getJson<{ ok: boolean; collections: ApiCollectionSummary[] }>("/api/collections");
  return r.collections ?? [];
}

export async function getCollection(id: number): Promise<ApiCollection> {
  const r = await getJson<{ ok: boolean; collection: ApiCollection }>(`/api/collections/${id}`);
  return r.collection;
}

export async function importCollection(payload: {
  nxm_url?: string;
  slug?: string;
  revision?: number;
}): Promise<ApiCollection> {
  const r = await postJson<typeof payload, { ok: boolean; collection: ApiCollection }>(
    "/api/collections/import",
    payload,
  );
  return r.collection;
}

export async function importCollectionRaw(payload: {
  slug: string;
  revision: Record<string, unknown>;
}): Promise<ApiCollection> {
  const r = await postJson<typeof payload, { ok: boolean; collection: ApiCollection }>(
    "/api/collections/import-raw",
    payload,
  );
  return r.collection;
}

export async function deleteCollection(id: number): Promise<{ ok: boolean }> {
  return deleteJson<{ ok: boolean }>(`/api/collections/${id}`);
}

export async function refreshCollection(id: number): Promise<ApiCollection> {
  const r = await postJson<Record<string, never>, { ok: boolean; collection: ApiCollection }>(
    `/api/collections/${id}/refresh`,
    {},
  );
  return r.collection;
}

export async function updateCollectionModFileState(
  collectionId: number,
  fileId: number,
  state: "pending" | "downloading" | "downloaded" | "failed",
): Promise<{ ok: boolean }> {
  return postJson<{ state: string }, { ok: boolean }>(
    `/api/collections/${collectionId}/mod-files/${fileId}/state`,
    { state },
  );
}
