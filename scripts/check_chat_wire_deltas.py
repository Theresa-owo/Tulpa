"""Continuation-specific reaction deltas: no model, network or real QQ."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chatlocal.mcp_chat import MCPChat
from chatlocal.mcp_chat_output import compact_wait,reaction_delta


def packet(sid,items,new=None,messages=None):
    return dict(event='messages',session_id=sid,messages=messages or [],decision_required=True,
                chat_guidance=dict(prompt_version='same',persona_name='full persona',capabilities=['text'],
                                   reaction_notices=dict(items=items,new_items=new or [])))


def notice(ident,target=30,description='original'):
    return dict(notice_id=ident,target_event_id=target,description=description,operator_id='333')


class Tests(unittest.TestCase):
    def test_exact_deltas_preserve_new_changed_current_targets_and_out_of_order(self):
        old=[notice(100),notice(200)]
        first,seen=reaction_delta(packet('a',old),{});self.assertEqual(first['chat_guidance']['reaction_notices']['items'],old)
        second,seen=reaction_delta(packet('a',old),seen)
        self.assertEqual(second['chat_guidance']['reaction_notices']['items'],[])
        self.assertEqual(second['chat_guidance']['reaction_notices']['history_omitted'],2)
        late=notice(101);changed=notice(200,description='recalled')
        third,seen=reaction_delta(packet('a',[old[0],late,changed],new=[changed]),seen)
        values=third['chat_guidance']['reaction_notices']
        self.assertEqual(values['items'],[late,changed]);self.assertEqual(values['new_items'],[changed])
        current,_=reaction_delta(packet('a',old,messages=[dict(id=30)]),seen)
        self.assertEqual(current['chat_guidance']['reaction_notices']['items'],old)
        rendered=compact_wait(second,'same')['chat_guidance']['reaction_notices']
        self.assertEqual(rendered['history_omitted'],2);self.assertIn('get_chat_session',rendered['history_read'])

    def test_state_lineage_retry_group_scope_and_full_response(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'mcp-efficiency.json').write_text('{"enabled":true}','utf-8')
            class Fake:
                access=SimpleNamespace(path=root/'mcp-access.sqlite3');lock=threading.RLock()
                result=None
                def _wait_sessions(self,*args,**kwargs):return [deepcopy(self.result)]
            f=Fake();grant={'id':'connection'};cancel=threading.Event()
            a,b='a'*32,'b'*32;first_notice=notice(9)
            f.result=packet(a,[first_notice]);initial=MCPChat.wait_many(f,grant,dict(session_ids=[a,b],known_prompt_versions={a:'same'},timeout_seconds=0),cancel)
            state=initial['next_call']['arguments']['wait_state_id']
            self.assertEqual(initial['results'][0]['chat_guidance']['reaction_notices']['items'],[first_notice])
            f.result=packet(a,[first_notice]);again=MCPChat.wait_many(f,grant,dict(wait_state_id=state),cancel)
            self.assertEqual(again['results'][0]['chat_guidance']['reaction_notices']['items'],[])
            self.assertEqual(again['_resolved_prompt_versions'][a],'same')
            # Reusing the previous state is independent; it does not mutate the
            # prior snapshot and suppress an observation that it never saw.
            f.result=packet(a,[first_notice,notice(10)]);retry=MCPChat.wait_many(f,grant,dict(wait_state_id=state),cancel)
            self.assertEqual(retry['results'][0]['chat_guidance']['reaction_notices']['items'],[notice(10)])
            continuation=again['next_call']['arguments']['wait_state_id'];f.result=packet(b,[first_notice])
            other=MCPChat.wait_many(f,grant,dict(wait_state_id=continuation),cancel)
            self.assertEqual(other['results'][0]['chat_guidance']['reaction_notices']['items'],[first_notice])
            f.result=packet(a,[first_notice]);full=MCPChat.wait_many(f,grant,dict(session_ids=[a],response_format='full',timeout_seconds=0),cancel)
            self.assertEqual(full['results'][0]['chat_guidance']['reaction_notices']['items'],[first_notice])

    def test_wait_reports_only_its_send_fence_and_treats_expiry_as_paused(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);a,b='a'*32,'b'*32
            (root/'mcp-chat-runtime.json').write_text(json.dumps(dict(paused=False,session_ids=[a],lease_until=0)),'utf-8')
            class Fake:
                access=SimpleNamespace(path=root/'mcp-access.sqlite3');lock=threading.RLock();result=packet(a,[])
                def _wait_sessions(self,*args,**kwargs):return [deepcopy(self.result)]
            f=Fake();grant={'id':'connection'};cancel=threading.Event()
            result=MCPChat.wait_many(f,grant,dict(session_ids=[a],timeout_seconds=0),cancel)
            self.assertTrue(result['chat_runtime']['paused'])
            f.result=packet(b,[]);other=MCPChat.wait_many(f,grant,dict(session_ids=[b],timeout_seconds=0),cancel)
            self.assertNotIn('chat_runtime',other)


if __name__=='__main__':unittest.main()
