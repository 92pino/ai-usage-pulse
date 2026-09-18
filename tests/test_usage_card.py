from collections import defaultdict
from datetime import datetime, timezone, date
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
from src.usage_card import (aggregate_ledgers, collect, collect_gemini, device_name,
                            import_records, merge, summarize, render, tokens)

TZ = ZoneInfo('Asia/Seoul')

class UsageTests(unittest.TestCase):
    def test_codex_cache_and_reasoning_are_not_double_counted(self):
        values = tokens({'input_tokens':100,'cached_input_tokens':70,'output_tokens':20,'reasoning_output_tokens':10}, 'Codex')
        self.assertEqual(sum(values.values()),120)
        self.assertEqual(values['input'],30)

    def test_claude_cache_is_additive(self):
        self.assertEqual(sum(tokens({'input_tokens':2,'output_tokens':7,'cache_read_input_tokens':80,'cache_creation_input_tokens':10},'Claude Code').values()),99)

    def test_codex_repeated_cumulative_events_and_copied_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rows = [{'type':'session_meta','payload':{'id':'session'}}, {'type':'turn_context','payload':{'model':'test-model'}}]
            for i in [1,1,2]:
                rows.append({'type':'event_msg','timestamp':'2026-09-17T18:00:00Z','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':100*i,'cached_input_tokens':50*i,'output_tokens':10*i}}}})
            for name in ['a','b']:
                (root/f'{name}.jsonl').write_text('\n'.join(map(json.dumps,rows)))
            found = collect({'Codex':[root]},TZ,defaultdict(int))
            report = summarize(found,TZ,datetime.now(timezone.utc))
            self.assertEqual(report['totals']['total'],220)
            self.assertEqual(list(report['daily']),['2026-09-18'])
            self.assertEqual(merge(found,found),found)
            self.assertEqual(merge(found,{}),found)

    def test_claude_stream_updates_and_malformed_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'a.jsonl'
            rows = [{'type':'assistant','timestamp':'2026-09-18T10:00:00Z','sessionId':'s','requestId':'r','message':{'id':'m','model':'claude','usage':{'input_tokens':2,'output_tokens':n}}} for n in [2,9,9]]
            p.write_text('\n'.join(map(json.dumps,rows))+'\n{')
            warnings=defaultdict(int)
            found=collect({'Claude Code':[Path(d)]},TZ,warnings)
            self.assertEqual(sum(sum(r[k] for k in ['input','output','cache_read','cache_write']) for r in found.values()),11)
            self.assertEqual(warnings['malformed_lines'],1)

    def test_gemini_json_and_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            chats=Path(d)/'project/chats';chats.mkdir(parents=True)
            msg={'id':'m','type':'gemini','model':'gemini','timestamp':'2026-09-18T00:00:00Z','tokens':{'input':100,'cached':50,'output':20,'thoughts':10,'tool':5}}
            (chats/'session-a.json').write_text(json.dumps({'sessionId':'s','messages':[msg]}))
            (chats/'session-b.jsonl').write_text(json.dumps({'sessionId':'s'})+'\n'+json.dumps(msg))
            found=collect_gemini(Path(d),TZ,defaultdict(int))
            self.assertEqual(len(found),1)
            self.assertEqual(sum(next(iter(found.values()))[k] for k in ['input','output','cache_read','cache_write']),135)

    def test_import_validation_and_deduplication(self):
        path=Path('examples/import-usage.json')
        self.assertEqual(import_records([path],TZ),import_records([path,path],TZ))
        with tempfile.TemporaryDirectory() as d:
            data=json.loads(path.read_text());data['records'][0]['input']=-1
            p=Path(d)/'invalid.json';p.write_text(json.dumps(data))
            with self.assertRaises(ValueError):import_records([p],TZ)

    def test_svg_xml_escaping_zero_usage_and_many_tools(self):
        data=summarize({},TZ,datetime.now(timezone.utc))
        for i in range(20):data['tools'][f'Tool <{i}>']={'total':10}
        for theme in ['light','dark']:
            svg=render(data,'Name <script> & "quote"',theme,date(2026,9,18))
            root=ET.fromstring(svg)
            self.assertGreater(int(root.attrib['height']),480)
            self.assertNotIn('<script>',svg)
            self.assertGreaterEqual(svg.count('class="reveal"'),8)
            self.assertIn('cubic-bezier(.22,.8,.24,1)',svg)
            self.assertIn('prefers-reduced-motion: reduce',svg)

    def test_device_ledgers_merge_and_dedupe_shared_requests(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            common = {'date':'2026-09-18','tool':'Codex','model':'model',
                      'input':100,'output':20,'cache_read':50,'cache_write':0}
            extra = {**common,'input':40,'output':5,'cache_read':0}
            base = {'version':2,'timezone':'Asia/Seoul','tools':'all'}
            (root/'macbook.json').write_text(json.dumps({**base,'device':'macbook','records':{'same':common}}))
            (root/'desktop.json').write_text(json.dumps({**base,'device':'desktop','records':{'same':common,'extra':extra}}))
            records = aggregate_ledgers(list(root.glob('*.json')),'Asia/Seoul','all')
            self.assertEqual(set(records), {'same','extra'})
            self.assertEqual(summarize(records,TZ,datetime.now(timezone.utc))['totals']['total'],215)

    def test_device_name_validation(self):
        self.assertEqual(device_name('macbook-work'),'macbook-work')
        with self.assertRaises(ValueError):
            device_name('../other')

if __name__=='__main__':unittest.main()
