## RivalNxt — Graphify Knowledge Graph

This project has a graphify knowledge graph at graphify-out/.

### Project Overview
- Frontend: React + TypeScript (`src/`)
- Backend: Python (`src-python/`, `core/`)
- Desktop shell: Tauri / Rust (`src-tauri/`)
- Build scripts: `scripts/`, `build_local.bat`, `build_local.sh`

### Rules for AI Agents
- Before answering architecture or codebase questions, read
  `graphify-out/GRAPH_REPORT.md` for god nodes and community structure
- If `graphify-out/wiki/index.md` exists, navigate it instead of reading raw files
- After modifying code files in this session, run `graphify update .`
  to keep the graph current (AST-only, no API cost)
- Do NOT scan all of `src/`, `src-python/`, or `src-tauri/` raw —
  query the graph first and only open specific files when needed
