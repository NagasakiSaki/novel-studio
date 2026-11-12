"""Author-facing scope labels, independent of advisory word-count settings."""

def choice_label(mode, count=1):
    return {'book':'全书（按已确认大纲）','volumes':f'后续 {count} 卷','chapters':f'后续 {count} 章'}.get(mode,'当前任务')


def run_scope(run):
    label = choice_label(run.get('mode'), run.get('requested_count',1))
    execution = run.get('execution_scope',{})
    if run.get('mode')=='book':
        label += ' · 启动规划 ' + str(run.get('volume_count',1)) + ' 卷'
    if execution.get('dynamic'):
        label += ' · 章节随故事形成 · 本次最多 ' + str(execution.get('chapter_limit',len(run.get('chapter_ids',[])))) + ' 章'
    else:
        label += ' · 本次计划 ' + str(len(run.get('chapter_ids',[]))) + ' 章'
    return label
