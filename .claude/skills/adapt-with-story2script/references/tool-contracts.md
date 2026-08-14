# Story2Script MCP Tool Contracts

Read this reference before invoking the workflow. Tool results use short workspace IDs; retain
the exact returned value and pass it to later tools.

## Source And Preflight

| Tool | Key input | Handoff |
| --- | --- | --- |
| `get_example_novel` | none | `novel_id`, title, genre, chapter count |
| `import_novel_file` | `file_path` | `novel_id`, detected type, title, chapter count/warning |
| `preview_chapters` | exactly one of `novel_id`, `novel_text` | detected chapter titles/count |
| `extract_character_profiles` | one source plus `mode` | character profile list |
| `build_novel_knowledge` | `novel_id`, `mode` | index statistics and retriever kind |
| `search_novel_knowledge` | `novel_id`, query, optional `top_k`, `before_chapter` | evidence hits |

`import_novel_file` accepts TXT, Markdown, CSV, LOG, and EPUB files up to 25 MB. It is subject to
`STORY2SCRIPT_FILE_ROOTS`. A chapter warning means the novel was stored but is not ready for
conversion.

Use `before_chapter=N` when checking what was known before chapter N. This prevents future plot
events from leaking into continuity decisions.

## Conversion And Scene Inspection

`convert_novel` accepts:

```text
novel_id or novel_text
title
genre
adaptation_type = 短剧 | 影视剧 | 舞台剧 | 广播剧 | 分镜脚本
mode = demo | ai
enable_review = false | true
```

It returns a `screenplay_id`, mode, title, scene/character summaries, and optional review summary.
The call can run for minutes in AI mode and reports progress. A timeout or failed status is an
error, not a partial success.

| Tool | Key input | Result |
| --- | --- | --- |
| `list_scenes` | `screenplay_id` | compact scene index |
| `get_scene` | `screenplay_id`, `scene_id` | complete scene structure |
| `rewrite_scene` | IDs, operation, mode, optional character/tone/feedback | updated scene |

Supported `rewrite_scene.operation` values:

| Operation | Use |
| --- | --- |
| `rewrite_dialogue` | replace dialogue while preserving scene identity |
| `strengthen_conflict` | raise dramatic opposition |
| `short_drama_pace` | tighten for short-drama pacing |
| `add_camera_hints` | add camera guidance |
| `reduce_narration` | replace excessive narration with playable action/dialogue |
| `adjust_character_voice` | adjust one `character_id`; pass `tone` |

Use `feedback` to pass machine- or human-review evidence into a rewrite.

## Review

`review_screenplay(screenplay_id, mode, auto_fix, threshold, max_rounds, scene_ids)` scores
dramatization, conflict-driving dialogue, residual narration, and character voice on a 0-10
scale. With `auto_fix=false`, `scene_ids` may limit review. With `auto_fix=true`, all failing scenes
can be rewritten and `scene_ids` is ignored.

| Tool | Requirement | Result |
| --- | --- | --- |
| `get_review_report` | an existing review | full machine/human report |
| `merge_human_review` | existing report plus verdict list | merged summary |

Human verdict shape:

```json
{"scene_id":"scene-1","status":"approved|rejected|pending","comment":"optional"}
```

## Agent Selection

`run_adaptation_agent` is the normal default. Inputs are `screenplay_id`, a natural-language
`goal`, `mode`, optional `threshold`/`max_steps`, `save_session`, and optional `novel_id`. It
returns the same screenplay workspace identity, before/after summaries, trace, and optional
`session_id`.

`run_adaptation_team` adds supervisor-directed review, continuity, and adaptation specialists.
Inputs add `max_rounds` and `max_steps_per_agent`. Use it for continuity-heavy or high-value work,
not merely because it is available. It returns role summaries, messages, continuity findings,
and an optional `mag-*` session ID.

Never mutate a screenplay while an Agent or team owns its transaction.

## Persistence And Export

| Tool | Key input | Handoff |
| --- | --- | --- |
| `load_agent_session` | persisted `ag-*` session ID | new `screenplay_id` |
| `load_team_session` | persisted `mag-*` session ID | new `screenplay_id` |
| `load_screenplay` | complete YAML text | new `screenplay_id` |
| `get_screenplay_yaml` | `screenplay_id` | complete YAML text |
| `validate_yaml` | YAML text | `{valid, message}` without storing |
| `save_screenplay` | `screenplay_id`, allowed path, `include_report` | saved YAML/report paths |
| `get_metrics` | none | LLM, task, token, latency, and cache summary |

`save_screenplay(include_report=true)` fails until a review report exists. The adjacent report is
written as `<name>.review.json`. The `screenplay://schema` MCP resource contains the complete JSON
Schema when field-level validation details are needed.

## ID And Restart Semantics

- `novel-*` and `sp-*` identify objects in the current MCP server process only.
- A server restart invalidates those workspace IDs.
- Persisted `ag-*` and `mag-*` sessions survive when `AGENT_SESSION_DIR` is persistent.
- Loading a session or YAML returns a new `screenplay_id`; replace the old ID everywhere.
- Novel context is not restored by loading an Agent session. Re-import the novel and pass the new
  `novel_id` if later work needs RAG evidence.
