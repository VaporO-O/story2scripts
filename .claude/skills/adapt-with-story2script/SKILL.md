---
name: adapt-with-story2script
description: >-
  Orchestrate Story2Script MCP tools to turn novels into reviewed screenplay YAML.
  Use when a user asks to import or continue a novel or screenplay, preview chapters,
  build story knowledge, convert in demo or AI mode, inspect or rewrite scenes, run
  single-agent or multi-agent refinement, resume a saved session, validate results,
  export YAML and review reports, or recover a Story2Script adaptation workflow.
---

# Adapt With Story2Script

Drive the existing Story2Script MCP server from source material to a validated,
reviewed screenplay. Keep large novel and screenplay payloads in the server workspace;
pass short IDs between tools.

Before the first tool call, read [references/tool-contracts.md](references/tool-contracts.md).
Use it again when choosing a rewrite operation or recovering from a failed or restarted run.

## Operating Rules

1. Verify that the `story2script` MCP server and required tools are available. If they are
   unavailable, explain the setup problem; do not pretend to run the workflow.
2. Track `novel_id`, `screenplay_id`, scene IDs, and optional `session_id` separately. Never
   invent an ID. Treat workspace IDs as process-local and replace them with IDs returned by
   recovery tools after a server restart.
3. Pass exactly one of `novel_id` or `novel_text` to tools that accept both. Prefer
   `novel_id` after import so the client does not resend the full novel.
4. Use `demo` only for examples, offline checks, and deterministic dry runs. Prefer `ai` for
   an actual creative deliverable, but confirm the user's intent before a potentially billable
   run when provider configuration or budget is unclear. Never silently fall back from `ai`
   to `demo` after an error.
5. Keep tool output concise. Report IDs, counts, quality summaries, warnings, and next actions;
   do not paste the full novel, YAML, report, or trace unless the user asks for it.
6. Do not mutate one screenplay concurrently. Agent, team, rewrite, and review operations lock
   or update the same workspace object and must run in sequence.
7. Do not save outside `STORY2SCRIPT_FILE_ROOTS`. Ask for an allowed output path if none was
   provided. Never overwrite an existing user file without confirmation.

## Workflow

### 1. Establish The Input

- For a file path, call `import_novel_file` and record its `novel_id`.
- For the bundled demonstration, call `get_example_novel`.
- For novel text already supplied in the conversation, use it directly for the first
  `preview_chapters` call; `convert_novel` will create the screenplay workspace entry.
- To continue existing YAML, skip novel conversion and call `load_screenplay`; then continue at
  quality review. Re-import the novel as well when later Agent runs need RAG context.
- Resolve the requested output format. Supported adaptation types are `短剧`, `影视剧`, `舞台剧`,
  `广播剧`, and `分镜脚本`.

### 2. Run Preflight

1. Call `preview_chapters`. Stop if fewer than three chapters are recognized and show the user
   the parser error or detected titles instead of attempting conversion.
2. For an imported long novel, call `build_novel_knowledge` before an Agent run. Use `demo` for
   deterministic lexical retrieval; use `ai` only when semantic embeddings are configured and
   wanted.
3. Call `extract_character_profiles` when the user needs a cast check before conversion or when
   character identity is a major risk. Do not block a normal conversion on this optional step.

### 3. Convert And Inspect

1. Call `convert_novel` with the source, title, genre, adaptation type, and selected mode. Leave
   `enable_review=false` when following this full workflow so review remains an observable gate.
2. Record the returned `screenplay_id`. Use `list_scenes` to inspect scene count, headings, and
   cast. Use `get_scene` only for scenes that need detailed diagnosis.
3. Surface conversion warnings. Do not claim success from a non-empty scene list alone.

### 4. Establish A Quality Baseline

1. Call `review_screenplay` with `auto_fix=false` and the same mode as the intended quality gate.
2. Summarize failing scene IDs, four-part scores, recurring issues, and the current threshold.
   Call `get_review_report` only when detailed evidence or human review is needed.
3. If the user supplies human verdicts, normalize them to `approved`, `rejected`, or `pending`
   and call `merge_human_review`. Never fabricate a human verdict.

### 5. Choose One Refinement Path

- Use `rewrite_scene` for a known local defect and a specific operation. Pass review feedback so
  the rewrite targets evidence rather than a vague instruction.
- Use `run_adaptation_agent` as the default for a broad quality goal. Pass `novel_id` when
  available, and set `save_session=true` for long or AI runs that may need recovery.
- Use `run_adaptation_team` only when cross-scene continuity, character consistency, or a
  high-value complex task justifies extra latency and model usage. Pass `novel_id` and normally
  persist the session.
- Do not run single-Agent and team refinement speculatively on the same screenplay. Choose one,
  explain the tradeoff briefly, and keep the returned trace or message summary as evidence.

### 6. Recheck And Bound Iteration

1. Call `review_screenplay` again after refinement. Compare the baseline and final summary.
2. If a small number of scenes still fail, perform one evidence-based targeted rewrite pass and
   review those scene IDs again. Ask the user before starting another broad Agent/team run.
3. Stop when the threshold is met, the configured step/round budget is exhausted, quality stops
   improving, or the user accepts the remaining issues. Report the actual terminal condition.

### 7. Validate And Deliver

1. Call `get_screenplay_yaml`, then `validate_yaml`. Never export a result that fails validation.
2. Call `save_screenplay` when the user supplied an allowed path. Set `include_report=true` only
   after a review report exists.
3. Return the saved paths, scene count, final quality summary, mode, and any unresolved issues.
   Call `get_metrics` for a cost/latency summary when the user requests it or an AI run behaved
   unexpectedly.

## Recovery

- If a workspace ID is missing after restart, re-import the novel for a new `novel_id`. Restore
  a persisted Agent session with `load_agent_session` or a team session with `load_team_session`;
  both return a new `screenplay_id`. Otherwise reload previously exported YAML with
  `load_screenplay`.
- If AI configuration, network, timeout, or provider errors occur, preserve the current IDs and
  report the exact failure. Check `get_metrics` when useful. Retry only after addressing the
  cause or obtaining user approval for changed cost/quality settings.
- If a file is rejected by the sandbox, ask for a path under an allowed root or for an explicit
  `STORY2SCRIPT_FILE_ROOTS` change. Do not work around the sandbox.
- If chapter parsing fails, fix or clarify chapter headings and rerun `preview_chapters`; do not
  bypass the three-chapter requirement.

## End-To-End Call Sequence

For a standard long-form adaptation, use this sequence and omit only explicitly optional steps:

```text
import_novel_file
-> preview_chapters
-> build_novel_knowledge
-> convert_novel
-> list_scenes
-> review_screenplay(auto_fix=false)
-> run_adaptation_agent OR run_adaptation_team OR rewrite_scene
-> review_screenplay(auto_fix=false)
-> get_screenplay_yaml
-> validate_yaml
-> save_screenplay(include_report=true)
```
