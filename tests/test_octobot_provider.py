import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from octobot_provider_compat import adapt_chat_kwargs
from patch_octobot import patch, sync_installation_context


class ProviderTests(unittest.TestCase):
    def test_deepseek_preserves_schema_in_prompt_without_mutating_input(self):
        kwargs = {'messages': [{'role': 'user', 'content': 'Decide'}],
                  'response_format': {'type': 'json_schema', 'json_schema': {
                      'name': 'Answer', 'schema': {'type': 'object', 'required': ['allocation']}}}}
        before = copy.deepcopy(kwargs)
        result = adapt_chat_kwargs(kwargs, 'https://api.deepseek.com/v1')
        self.assertEqual(result['response_format'], {'type': 'json_object'})
        self.assertIn('allocation', result['messages'][0]['content'])
        self.assertEqual(result['extra_body']['thinking']['type'], 'disabled')
        self.assertEqual(kwargs, before)

    def test_other_hosts_remain_unchanged(self):
        for host in ('https://api.openai.com/v1', 'https://api.deepseek.com.example.org', None):
            kwargs = {'response_format': {'type': 'json_schema'}}
            self.assertIs(adapt_chat_kwargs(kwargs, host), kwargs)

    def test_patch_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'tentacles/Services/Services_bases/gpt_service/gpt.py'
            target.parent.mkdir(parents=True)
            target.write_text('def prepare():\n        return api_kwargs\n', encoding='utf-8')
            patch(tmp)
            first = target.read_text(encoding='utf-8')
            patch(tmp)
            self.assertEqual(target.read_text(encoding='utf-8'), first)
            self.assertEqual(first.count('from octobot_provider_compat'), 1)

    def test_minimal_presets_inherit_installed_metadata_without_changing_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / 'user/reference_tentacles_config/tentacles_config.json'
            reference.parent.mkdir(parents=True)
            reference.write_text(json.dumps({'installation_context': {'octobot_version': '2.1.1'},
                'tentacle_activation': {'Trading': {'Bitget': True, 'DailyTradingMode': False},
                                       'Services': {'WebInterface': True}}}))
            preset = root / 'user/profiles/astra_index/tentacles_config.json'
            preset.parent.mkdir(parents=True)
            activation = {'Trading': {'IndexTradingMode': True}}
            preset.write_text(json.dumps({'tentacle_activation': activation}))
            sync_installation_context(root)
            result = json.loads(preset.read_text())
            self.assertTrue(result['tentacle_activation']['Trading']['IndexTradingMode'])
            self.assertFalse(result['tentacle_activation']['Trading']['DailyTradingMode'])
            self.assertTrue(result['tentacle_activation']['Services']['WebInterface'])
            self.assertEqual(result['installation_context']['octobot_version'], '2.1.1')
