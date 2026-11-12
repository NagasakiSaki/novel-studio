"""Display labels only; author titles and manuscript bytes are never changed."""
import re


def numbered_title(number, title, kind='章'):
    title = (title or '').strip()
    if re.match(r'^第?[0-9零一二两三四五六七八九十百千]+\s*' + kind, title):
        return title
    return f'第{number}{kind}' + (' ' + title if title else '')


def same_heading(left, right):
    def normalized(value):
        value = value.lstrip('#').strip()
        value = re.sub(r'^第?[0-9零一二两三四五六七八九十百千]+\s*[卷章]\s*[：:、.．·—-]?\s*', '', value)
        return re.sub(r'\s+', '', value)
    return normalized(left) == normalized(right)
