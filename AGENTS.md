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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
