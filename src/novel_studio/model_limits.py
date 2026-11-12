"""Exact official API metadata; host subscription limits are never inferred."""

MAX_TOKEN_PARAMETER = 2_147_483_647  # Integer representation, not a model limit.
REVIEWED = '2026-11-07'
OPENAI_MODELS = ('gpt-6-astra', 'gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-luna')


def positive_limit(value):
    return value if type(value) is int and 0 < value <= MAX_TOKEN_PARAMETER else None


def apply_limits(provider, selected, metadata):
    """Live catalog values take precedence over a dated exact-ID docs fallback."""
    result = dict(metadata)
    fallback = {}
    if provider == 'openai' and selected in OPENAI_MODELS:
        fallback = {'context_window_tokens': 1_050_000, 'max_output_tokens': 128_000,
                    'limit_source': 'official-docs:openai/' + selected}
    elif provider == 'gemini' and selected == 'gemini-3.8-flash':
        fallback = {'max_input_tokens': 1_048_576, 'max_output_tokens': 65_536,
                    'limit_source': 'official-docs:gemini/gemini-3.8-flash'}
    for key in ('max_input_tokens', 'max_output_tokens', 'context_window_tokens'):
        value = positive_limit(result.get(key))
        result[key] = value if value is not None else fallback.get(key)
    if fallback and not positive_limit(metadata.get('max_output_tokens')):
        result.update(limit_source=fallback['limit_source'], limits_reviewed=REVIEWED)
    return result
