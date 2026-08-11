---
name: skill-creator
description: Use when the user asks Akane to create, install, revise, or package an Akane Skill; guides concise progressive instructions, references, scripts, validation, and hot publication.
---

# Akane Skill Creator

Create a Skill only when reusable task guidance will improve future execution. A Skill is an operation manual that coordinates existing model reasoning and host tools; it is not a plugin, does not add permissions, and does not dynamically register new tools.

## Workflow

1. Clarify the recurring task and the exact situations in which the Skill should load.
2. Use `exec_run` to create `skill_drafts/<skill-name>/SKILL.md` in the default execution workspace. Keep the directory name and frontmatter `name` identical.
3. Write this required frontmatter:

   ```yaml
   ---
   name: lowercase-slug
   description: State exactly what the Skill handles and when it should be loaded.
   ---
   ```

4. Put the essential workflow in `SKILL.md`. Keep it direct and operational. Do not repeat the full schemas of tools already visible to the model.
5. Put detailed, optional knowledge in `references/`. In `SKILL.md`, explicitly say which situation requires reading each reference.
6. Put deterministic reusable helpers in `scripts/`. Tell the model how to run them with `exec_run`, expected inputs and outputs, and how to recognize failure. A script remains an ordinary file; it is not automatically a tool.
7. Run `manage_skill(action="validate", draft_path="skill_drafts/<skill-name>")`.
8. Fix every structured validation failure. Then run `manage_skill(action="publish", ...)`; use `replace=true` only when intentionally updating an existing managed Skill.
9. Treat publication as successful only when the tool returns `published`. The new catalog becomes visible on the next model request without restarting Akane.

To remove an installed Skill, first use `load_skill` to confirm its exact name, source, and execution
cwd. A managed Skill may be removed with `exec_run` in `cwd=alias:skills` by deleting only its exact
relative directory and then verifying it is absent without using a wildcard or parent-directory
target. Re-check the catalog because removing a managed override may reveal a same-named bundled
Skill. Never delete a bundled Skill from
`alias:bundled_skills`. Removing the installed managed directory does not remove its
`skill_drafts/<skill-name>` source; delete that separate relative directory only when the user asks
to discard the draft too.

## Quality rules

- Prefer one focused Skill over a large handbook covering unrelated jobs.
- The description is routing metadata: include positive triggers and enough boundaries to avoid accidental loading.
- Load references progressively. Do not tell the model to read every file in the directory.
- Reuse `exec_run`, web search, memory, file handles, and delivery tools. Do not invent unavailable commands or APIs.
- State observable completion criteria and honest fallback behavior.
- Never embed secrets, API keys, tokens, or private cookies. Prefer portable aliases and relative
  paths in reusable Skill text; when a real host task requires an absolute path, discover and use
  the actual path at execution time instead of hard-coding one machine's path into the package.
- Skill instructions cannot weaken host approvals, QQ owner checks, filesystem policy, or MemCore recording.
- When updating a working Skill, preserve useful behavior and change the smallest necessary section.

## Suggested layout

```text
skill_drafts/<skill-name>/
  SKILL.md
  references/
    topic.md
  scripts/
    helper.py
```

Only add folders that the workflow genuinely needs.
