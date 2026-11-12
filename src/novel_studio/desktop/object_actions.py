"""State-aware object actions shared by visible controls and context menus."""
from .context_menu import entry


def chapter_actions(row, project_id, busy=False, source_kind=None):
    if not row:
        return []
    formal = source_kind == 'canonical' or source_kind is None and (row['status'] == 'ARCHIVED' or not row.get('draft'))
    data = {'chapter_id':row['chapter_id'], '_project_id':project_id, 'source_kind':'canonical' if formal else 'drafts'}
    source = row.get('canonical') if formal else row.get('draft')
    values = []
    if source:
        values.append(entry('阅读正式正文' if formal else '阅读正文', 'object_document_read' if formal else 'object_chapter_read', {**data, 'document_id':source['document_id']}))
    if row.get('draft') and not busy and not formal:
        values.append(entry('编辑草稿', 'object_chapter_edit', data))
    if source and not busy:
        values += [entry('让主笔修改' if not formal else '提出修改要求（新草稿）', 'object_writer_revise', data),
                   entry('让责编审核', 'object_editor_review', data)]
    if row.get('editor_plan'):
        values.append(entry('查看章节计划', 'object_chapter_plan', data))
    if ((row.get('formal_review') or row.get('review')) if formal else row.get('review')):
        values.append(entry('查看正式稿审查意见' if formal and row.get('formal_review') else '查看收录前审稿意见' if formal else '查看审稿意见', 'object_chapter_review', data))
    if source:
        values.append(entry('查看版本与比较', 'object_chapter_versions', data, '版本与整理'))
        if not busy:
            values.append(entry('导出正式本章' if formal else '导出本章草稿', 'object_chapter_export', data))
    if not busy:
        values += [entry('与主笔讨论本章', 'object_writer_discuss', data, '讨论'), entry('与责编讨论本章', 'object_editor_discuss', data, '讨论'),
                   entry('调整目录标题', 'workflow_rename', data, '版本与整理'), entry('调整所属卷与阅读顺序', 'workflow_place', data, '版本与整理')]
        if row['status'] == 'AUTHOR_APPROVAL_PENDING':
            values.append(entry('批准收录（先看差异）', 'workflow_accept', {**data, 'chapter_ids':[row['chapter_id']]}))
    return values


def outline_actions(record, formal, project_id, busy=False):
    if not record:
        return []
    data = {'version_id':record['version_id'], '_project_id':project_id}
    values = [entry('阅读完整大纲', 'object_outline_read', data), entry('查看版本差异', 'outline_diff', data)]
    if not busy:
        values += [entry('与责编讨论', 'object_outline_discuss', data), entry('要求责编修改', 'object_outline_revise', data),
                   entry('编辑并另存候选', 'outline_edit', data, '版本操作'), entry('恢复为新候选', 'outline_restore', data, '版本操作'),
                   entry('导出此大纲（Markdown）', 'object_outline_export', data)]
        if not formal:
            values.append(entry('确认正式大纲（先看差异）', 'outline_approve', data))
    return values
