"""Small provider adapter for the pinned OctoBot 2.1.1 chat client."""
import json
from urllib.parse import urlparse


def adapt_chat_kwargs(kwargs, base_url):
    if urlparse(base_url or '').hostname != 'api.deepseek.com':
        return kwargs
    result = dict(kwargs)
    result['extra_body'] = {**result.get('extra_body', {}), 'thinking': {'type': 'disabled'}}
    response_format = result.get('response_format') or {}
    if response_format.get('type') == 'json_schema':
        schema = response_format['json_schema']['schema']
        result['response_format'] = {'type': 'json_object'}
        result['messages'] = [{
            'role': 'system',
            'content': 'Return only a JSON object matching this JSON schema. '
                       'Do not add markdown or commentary. Schema: ' + json.dumps(schema),
        }, *result['messages']]
    return result
