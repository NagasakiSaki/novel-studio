---
name: novel-continuity
description: Inspect novel continuity sources or explain local chapter counting and outline slicing when asked about character knowledge, timelines or foreshadowing.
---

# Continuity adapter

This workflow reads snapshots and offers proposals in chat or Writer-Workspace/Proposed-Changes when configured. It does not commit tracking state, promote final prose or reconstruct memory automatically. The separate author-operated workspace_admin.py handles setup, snapshots and approved single-file promotion/recovery; this Agent must never invoke it through a command tool.

The offline helper at ../../scripts/novelctl.py provides count and outline commands only. A user explicitly supplies an absolute external project root and a relative Markdown/text file. Count uses pinned Oh Story measurement; outline uses its scoped parser plus a separate range-format adapter. A role with no command tool must explain the command for the user instead of bypassing its permissions.

Keep author truth, character knowledge, reader revelation, story time and narrative order distinct. Distinguish fact, inference and pending. Every proposed fact cites an actual chapter/version or author confirmation; future plans remain plans. Outline slices are planning material, not a guarantee of spoiler-free character knowledge.

Do not invoke the unbundled upstream tracking writer or install extra services. Unified memory transactions remain design work; the author's single-file backup/approval protocol is not a complete continuity engine.

