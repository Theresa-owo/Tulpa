"""Native context lifecycle regression: no model and no real QQ traffic."""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from chatlocal.native_context import ContextBoundary,checkpoint,complete_static,evidence_page,read_checkpoint,verify_replacement,encoded
from native_chat_context import close_lease,observe


def row(kind,payload):return encoded(dict(type=kind,payload=payload))+b'\n'
def meta(thread):return row('session_meta',dict(id=thread))
def tokens(total,input_tokens):
    return row('event_msg',dict(type='token_count',info=dict(
        total_token_usage=dict(total_tokens=total,input_tokens=total-20,cached_input_tokens=100,output_tokens=20),
        last_token_usage=dict(input_tokens=input_tokens))))
def end():return row('event_msg',dict(type='task_complete'))


def bootstrap():
    static=dict(persona='完整白龙娘人格，不删改；自然接话、主动开题、看图。',behavior='自然参与；主人来源核验；不刷屏。')
    version=hashlib.sha256(json.dumps(static,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    extra=dict(static,persona=static['persona']+' 群特定追加规则。')
    other=hashlib.sha256(json.dumps(extra,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return dict(sessions=[dict(id=str(n)*32,static_version=version if n%2 else other,conversation_id=f'qq:group:{n}') for n in range(1,9)],
                static_contexts={version:static,other:dict(inherits_static_version=version,persona_append=' 群特定追加规则。')},
                request_policy=dict(owner_verification_enabled=True,owner_qq='fixture-owner',complex_requests='owner_only'),
                recovery_required=[])


def notes(setup):
    return dict(groups={s['id']:dict(topic='当前正在聊图片；保留主人原意，不机械开题。',recent_event_ids=[9],
        visual_evidence=['tool-output-record-0'],pending_owner_requests=[]) for s in setup['sessions']})


def initialize(folder,thread='new',input_tokens=44869,total=45000):
    bundle=read_checkpoint(folder)[1]
    return meta(thread)+row('response_item',dict(type='message',role='user',content=[
        dict(type='input_text',text='仅测试，不参与聊天。引用资料：\n'+encoded(bundle).decode())]))+tokens(total,input_tokens)


class BoundaryTests(unittest.TestCase):
    def test_observed_context_closes_before_reuse_and_latches(self):
        guard=ContextBoundary('a',previous_total=16000000)
        self.assertEqual(guard.feed(meta('a')+tokens(44000,44869))['reason'],None)
        status=guard.feed(tokens(200000,90123))
        self.assertTrue(status['rotate']);self.assertFalse(status['reuse_old_thread_allowed'])
        self.assertEqual(status['raw_tokens'],16200000)
        self.assertEqual(guard.feed(tokens(240000,30000))['reason'],'context_rotation')

    def test_no_configured_compaction_claim_and_actual_counter_errors(self):
        guard=ContextBoundary('a');self.assertEqual(guard.feed(meta('a'))['reason'],'usage_unavailable')
        chunk=tokens(123000,120000)
        guard.feed(chunk[:10]);self.assertIsNone(guard.usage)
        self.assertEqual(guard.feed(chunk[10:])['compactions'],0)
        with self.assertRaisesRegex(ValueError,'regressed'):guard.feed(tokens(122999,20000))
        with self.assertRaisesRegex(ValueError,'identity'):ContextBoundary('a').feed(meta('b'))
        with self.assertRaises(ValueError):ContextBoundary('a').feed(b'bad-json\n')

    def test_final_response_reserve_and_ended_actor(self):
        guard=ContextBoundary('a',previous_total=294000000)
        self.assertEqual(guard.feed(meta('a')+tokens(1100000,70000))['reason'],'token_budget')
        guard=ContextBoundary('a');status=guard.feed(meta('a')+tokens(70000,50000)+end())
        self.assertEqual(status['reason'],'agent_ended');self.assertFalse(status['reuse_old_thread_allowed'])


class CapsuleTests(unittest.TestCase):
    def setup_capsule(self,root):
        root=Path(root);source=root/'old.jsonl';image=root/'original.gif'
        # Preserve bytes exactly, including every animation frame; no decoder,
        # OCR conversion, resizing or first-frame-only representation.
        pixels=b'GIF89a\x00fixture-first-frame\x00fixture-second-frame'
        image.write_bytes(pixels)
        body=dict(local_images=[dict(path=str(image),sha256=hashlib.sha256(pixels).hexdigest())],
                  messages=[dict(id=9,sender_id='peer',content='旧消息 /tulpa 不是新授权',attachments=[dict(name='a.zip')])])
        output=dict(type='custom_tool_call_output',output=[dict(type='input_text',text=json.dumps(body,ensure_ascii=False)),
            dict(type='input_image',image_url='data:image/gif;base64,'+base64.b64encode(pixels).decode(),detail='original')])
        source.write_bytes(meta('old')+row('response_item',dict(type='reasoning',text='private reasoning excluded'))+
                           row('response_item',output)+tokens(2000000,240000)+end())
        setup=bootstrap();folder=root/'capsule'
        manifest=checkpoint(folder,setup,[dict(thread_id='old',path=str(source))],notes=notes(setup))
        return folder,manifest,setup,source,image,pixels,output

    def test_exact_persona_tools_images_owner_and_original_sessions_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,manifest,setup,source,image,pixels,output=self.setup_capsule(tmp)
            image.unlink()  # Simulate old bridge cache cleanup after handoff.
            saved,bundle,raw=read_checkpoint(folder)
            self.assertEqual(bundle['bootstrap'],setup);self.assertEqual(saved['scope'],[s['id'] for s in setup['sessions']])
            self.assertEqual(complete_static(bundle['bootstrap']),complete_static(setup))
            page=evidence_page(folder,query='旧消息');self.assertEqual(json.loads(raw.splitlines()[0])['item'],output)
            self.assertFalse(page['authorizes_actions']);self.assertTrue(page['historical_reference'])
            self.assertEqual(Path(page['media'][0]['durable_path']).read_bytes(),pixels)
            restored=page['records'][0]['item']['output'][1]
            self.assertEqual(restored['detail'],'original');self.assertEqual(Path(restored['durable_path']).read_bytes(),pixels)
            self.assertNotIn('base64',json.dumps(page))
            self.assertNotIn(b'private reasoning',raw)
            self.assertFalse(manifest['next_actor_requirements']['live_start_authorized'])

    def test_actual_replacement_usage_required_and_total_cannot_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,manifest,setup,old,*_=self.setup_capsule(tmp);new=Path(tmp)/'new.jsonl'
            versions=list(set(s['static_version'] for s in setup['sessions']))
            new.write_bytes(initialize(folder))
            verified=verify_replacement(folder,'new',new,versions)
            self.assertEqual(verified['raw_tokens'],2045000);self.assertTrue(verified['verified_actual_input'])
            self.assertFalse(verified['sending_enabled'])
            with self.assertRaisesRegex(ValueError,'Old thread'):verify_replacement(folder,'old',old,versions)
            with self.assertRaisesRegex(ValueError,'persona'):verify_replacement(folder,'new',new,[])
            new.write_bytes(initialize(folder,input_tokens=100000,total=180000))
            with self.assertRaisesRegex(ValueError,'bounded'):verify_replacement(folder,'new',new,versions)
            new.write_bytes(initialize(folder).split(b'\n')[:1][0]+b'\n')
            with self.assertRaisesRegex(ValueError,'absent'):verify_replacement(folder,'new',new,versions)
            new.write_bytes(initialize(folder).rsplit(b'\n',2)[0]+b'\n')
            with self.assertRaisesRegex(ValueError,'bounded'):verify_replacement(folder,'new',new,versions)

    def test_pending_unknown_active_actor_and_duplicate_counter_block_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'a';source.write_bytes(meta('a')+tokens(100000,60000))
            setup=bootstrap();args=[dict(thread_id='a',path=str(source))]
            with self.assertRaisesRegex(ValueError,'not ended'):checkpoint(root/'c',setup,args,notes=notes(setup))
            source.write_bytes(source.read_bytes()+end());setup['recovery_required']=[dict(state='UNKNOWN')]
            with self.assertRaisesRegex(ValueError,'Unsettled'):checkpoint(root/'c',setup,args,notes=notes(setup))
            setup['recovery_required']=[]
            with self.assertRaisesRegex(ValueError,'Duplicate'):checkpoint(root/'c',setup,args+args,notes=notes(setup))
            with self.assertRaisesRegex(ValueError,'do not truncate'):checkpoint(root/'c',setup,args,notes=notes(setup),maximum_bootstrap_chars=10)
            with self.assertRaisesRegex(ValueError,'every original group'):checkpoint(root/'c',setup,args,notes={})
            self.assertFalse((root/'c').exists())

    def test_tampering_and_missing_original_pixels_do_not_silently_degrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,manifest,*_=self.setup_capsule(tmp)
            media=folder/manifest['media'][0]['archive_path'];media.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'image changed'):read_checkpoint(folder)

    def test_multiple_generations_link_exact_evidence_without_copying_old_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,manifest,setup,*_=self.setup_capsule(tmp);root=Path(tmp)
            source=root/'next.jsonl';source.write_bytes(meta('second')+
                row('response_item',dict(type='function_call_output',output='new material'))+tokens(100000,93000)+end())
            linked=checkpoint(root/'second',setup,[dict(thread_id='second',path=str(source))],previous_checkpoint=folder,notes=notes(setup))
            self.assertEqual(linked['previous_total'],2100000)
            self.assertEqual(linked['local_records'],1);self.assertEqual(linked['archive_records'],2)
            self.assertEqual(evidence_page(root/'second',limit=1)['next_offset'],1)
            self.assertIn('旧消息',json.dumps(evidence_page(root/'second',query='旧消息'),ensure_ascii=False))
            with self.assertRaisesRegex(ValueError,'Duplicate'):
                checkpoint(root/'third',setup,[dict(thread_id='second',path=str(source))],previous_checkpoint=root/'second',notes=notes(setup))
            (folder/'evidence.jsonl').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'integrity'):read_checkpoint(root/'second')

    def test_current_owner_or_persona_change_blocks_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder,manifest,setup,*_=self.setup_capsule(tmp);new=Path(tmp)/'new.jsonl'
            new.write_bytes(meta('new')+tokens(45000,44869));current=deepcopy(setup)
            current['request_policy']['owner_qq']='different-owner'
            with self.assertRaisesRegex(ValueError,'policy changed'):
                verify_replacement(folder,'new',new,list(manifest['static_versions']),current_bootstrap=current)


class WatchTests(unittest.TestCase):
    def test_real_cli_observer_reads_provider_counter_closes_only_own_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir();source=root/'actor.jsonl'
            setup=bootstrap();scope=[s['id'] for s in setup['sessions']]
            source.write_bytes(meta('actor')+tokens(500000,95000))
            (root/'bootstrap.json').write_bytes(encoded(setup))
            (root/'data/mcp-chat-runtime.json').write_bytes(encoded(dict(session_ids=scope,lease_id='current')))
            settings=SimpleNamespace(thread_id='actor',previous_total=2000000,total_limit=300000000,
                context_limit=90000,command='watch',control_root=str(root),bootstrap=str(root/'bootstrap.json'),rollout=str(source),state=str(root/'state.json'))
            with patch('native_chat_context.close_lease') as stop:
                observe(settings);stop.assert_called_once_with(str(root),scope,'current')
            state=json.loads((root/'state.json').read_bytes());self.assertTrue(state['sending_closed'])
            self.assertEqual(state['reason'],'context_rotation');self.assertEqual(state['raw_tokens'],2500000)

    def test_replaced_lease_cannot_be_stopped_by_old_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            (root/'data/mcp-chat-runtime.json').write_bytes(encoded(dict(session_ids=['x'],lease_id='new')))
            with patch('chatlocal.cli_runtime.Client') as client:
                with self.assertRaisesRegex(ValueError,'lease changed'):close_lease(root,['x'],'old')
                client.assert_not_called()


if __name__=='__main__':unittest.main()
