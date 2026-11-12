# Novel Studio

Professional literary writing and evidence-based review with two independent primary agents.

Writer runs in Writer-Workspace, reads Context-Snapshot, and edits Drafts/Proposed-Changes. Editor runs with Editor-Workspace/Review-Reports as its active directory, reads Manuscript-Snapshot, and creates separate versioned Review-Reports. Canonical and Backups are outside both active workspaces and explicitly denied by generated session settings. These are host permission boundaries, not OS account isolation.

## Author setup and daily use

Use Python 3.10+ and an officially installed Antigravity CLI. Create a NEW project outside the plugin and private workspace. Replace placeholders with absolute paths:

```text
python -B <plugin>/scripts/workspace_admin.py init --project <new-project> --plugin <installed-plugin> --deny-private <private-directory> --apply
python -B <plugin>/scripts/workspace_admin.py context --project <project> --apply
python -B <plugin>/scripts/workspace_admin.py launch --project <project> --role writer --cli <official-agy-path>
python -B <plugin>/scripts/workspace_admin.py review-snapshot --project <project> --source Writer-Workspace/Drafts/chapter.md --context Writer-Workspace/Context-Snapshot/<id>/SNAPSHOT.json --apply
python -B <plugin>/scripts/workspace_admin.py launch --project <project> --role editor --cli <official-agy-path>
```

Populate Canonical yourself before the first context snapshot; there is no private-book importer. The launcher verifies the generated session file, selects only the role's workspace, and passes --settings on every invocation. It does not modify global settings, use a shell, or install a service. Resume one chosen conversation per role using --conversation <UUID>; roles do not automatically create persistent conversations or shared memory. Normal draft/report writes use accept-edits with request-review for other actions, plus explicit denies for commands, URL/MCP and protected sources. There is no Turbo/Always Proceed.

## Author approval

```text
python -B <plugin>/scripts/workspace_admin.py promote --project <project> --source Writer-Workspace/Drafts/chapter.md --target Canonical/Final-Manuscript/chapter.md --dry-run
python -B <plugin>/scripts/workspace_admin.py promote --project <project> --source Writer-Workspace/Drafts/chapter.md --target Canonical/Final-Manuscript/chapter.md --apply --approve <exact-reviewed-approval_digest>
python -B <plugin>/scripts/workspace_admin.py restore --project <project> --transaction <id> --dry-run
```

Read the diff, confirm scope and both revisions, then explicitly paste the exact digest. This is an author-operated acknowledgement, not proof of human identity. The agents have no command tool and must not run the author utility. Existing versions are backed up before atomic publication; restore also requires a newly reviewed digest. No automatic file deletion or canon inference. A cooperating-tool lock does not block arbitrary same-user processes; crash journals and backups support recovery, not permission isolation.

Installation alone does not bind session settings. Start through launch or explicitly pass the generated --settings file and the correct cwd every time. The generic assets/permissions/read-only-session.json remains an all-write-denied fallback. Never use the project root, Canonical, a drive or the private workspace as the Agent workspace. Source snapshots are copies, not junctions or shared originals.

No accounts, credits, proxies, model relays, restriction bypasses, key collection, hidden uploads, telemetry, high-privilege hooks or opaque services. AI context follows the host/provider data policies; the full AI workflow is not guaranteed offline. Offline count/outline helpers remain human-run optional commands; no full Shell was added to agents. Original core and selected upstream files retain MIT notices. See UPSTREAM_SOURCES.md, THIRD_PARTY_NOTICES.md and docs/SECURITY_AUDIT.md.
