"""Lossless duplicate-outline references and one bounded workflow envelope."""
from .core import digest
from .prompting import canonical, MAX_PROMPT_BYTES, context_size_error


def prepare_context(current):
    """Keep full source text, rules and manuscripts; index only exact duplicates.

    Ranges are Python/Unicode character offsets into approved_outline_text in
    the SAME request. They grant no file access or extra model/tool operation.
    """
    raw_bytes = len(canonical(current).encode('utf-8'))
    original = current.get('approved_outline_text', '')
    references = []
    for field in ('outline_overview', 'volume_plan'):
        values = dict(current.get(field, {}))
        for key, value in values.items():
            if not isinstance(value, str) or len(value.encode('utf-8')) < 1024:
                continue
            start = original.find(value)
            if start < 0:
                continue  # Non-duplicate information always remains literal.
            reference = {'source_field': 'approved_outline_text', 'start': start, 'end': start + len(value),
                         'sha256': digest(value.encode('utf-8'))}
            values[key] = {'source_ref': reference}
            references.append(field + '.' + key)
        current[field] = values
    current['context_layout'] = {'format': 'workflow-context-v2', 'outline_delivery': 'full_original_text',
        'reference_note': 'source_ref仅索引同一请求approved_outline_text中的完整原文，按Unicode字符[start,end)定位；不是摘要、删节、外部文件或工具授权。正式大纲原文完整提供且优先级不变。',
        'deduplicated_fields': references}
    size = len(canonical(current).encode('utf-8'))
    metrics = {'context_bytes': size, 'raw_context_bytes': raw_bytes, 'limit_bytes': MAX_PROMPT_BYTES,
        'outline_bytes': len(original.encode('utf-8')), 'always_bytes': len(current.get('approved_always_rules', '').encode('utf-8')),
        'references_bytes': len(current.get('retrieved_references', '').encode('utf-8')),
        'manuscript_bytes': len(current.get('current_manuscript', {}).get('text', '').encode('utf-8')),
        'deduplicated_bytes': max(0, raw_bytes - size), 'context_stage': 'workflow'}
    if size > MAX_PROMPT_BYTES:
        raise context_size_error(size, 'workflow', **{k:v for k,v in metrics.items() if k not in {'context_bytes','limit_bytes','context_stage'}})
    return current, metrics
