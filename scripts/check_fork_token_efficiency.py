"""Token reductions with real local MCP and synthetic QQ. No real QQ/model IO."""
import asyncio
import base64
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
import httpx
from mcp import ClientSession,types
from mcp.client.streamable_http import streamable_http_client
from fastapi import FastAPI
from fastapi.testclient import TestClient
from check_fixture_support import isolated_env
from check_mcp_chat import OneBot,Events,eventually
from chatlocal.store import Store
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.onebot import invalidate_availability
from chatlocal.mcp_owner import OwnerGate
from chatlocal.mcp_efficiency import compact_dynamic
from chatlocal.mcp_language_store import LanguageStore


def chars(v):return len(json.dumps(v,ensure_ascii=False,separators=(',',':')))
def data(r):return r.structuredContent or json.loads(next(c.text for c in r.content if c.type=='text'))


async def transport(service,token,sids,configure):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},trust_env=False,timeout=20) as http:
        async with streamable_http_client(f'http://127.0.0.1:{service.access.settings()["port"]}/mcp',http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                init=await client.initialize()
                assert len(init.instructions)<800
                configure(False)
                legacy=await client.call_tool('list_chat_sessions',{'response_format':'full','include_stopped':True})
                configure(True)
                result=await client.call_tool('list_chat_sessions',{})
                assert result.structuredContent is None and len(data(result)['sessions'])==8
                assert all('persona' not in s for s in data(result)['sessions'])
                from chat_bridge import bootstrap,bounded
                start=await bootstrap(client,{})
                assert len(start['sessions'])==8 and len(start['static_contexts'])==2
                assert sum('inherits_static_version' not in c for c in start['static_contexts'].values())==1
                for version,c in start['static_contexts'].items():
                    if 'inherits_static_version' in c:
                        base=start['static_contexts'][c['inherits_static_version']]
                        rebuilt=dict(persona=base['persona']+c['persona_append'],behavior=base['behavior'])
                        import hashlib
                        assert hashlib.sha256(json.dumps(rebuilt,ensure_ascii=False,sort_keys=True).encode()).hexdigest()==version
                assert all(s['static_version'] in start['static_contexts'] for s in start['sessions'])
                assert start['recovery_required'] and 'next_call' not in start,'Pending WAITING hidden at initialization'
                assert json.loads(bounded(start)).get('delivery')!='deferred_unread'
                payload=await client.call_tool('wait_chat_messages_many',{'session_ids':sids,'timeout_seconds':0})
                assert payload.structuredContent is None and data(payload)['next_call']['arguments']['wait_state_id']
                context=data(await client.call_tool('get_chat_session',dict(session_id=sids[2],view='context')))
                plan=data(await client.call_tool('plan_chat_reply',dict(session_id=sids[2],batch_id=context['input_batch']['id'],action='reply',expressions=['text'],intent='自然接话')))
                assert plan['state']=='READY',plan
                reply=dict(session_id=sids[2],plan_id=plan['plan_id'],expression_ids=[],bubbles=[dict(kind='text',text='收到')])
                sent=await client.call_tool('send_chat_reply',reply)
                assert sent.structuredContent is None and data(sent)['state']=='SUCCEEDED'
                assert data(await client.call_tool('send_chat_reply',reply))['state']=='SUCCEEDED'
                assert len(OneBot.sent)==1,'Optimized replay duplicated a send'
                return dict(directory_legacy_chars=len(legacy.model_dump_json()),directory_optimized_chars=len(result.model_dump_json()),bootstrap_eight_groups_chars=chars(start),shared_persona_count=len(start['static_contexts']))


def learning():
    now=[1000.]
    with tempfile.TemporaryDirectory() as tmp:
        store=LanguageStore(Path(tmp)/'language.sqlite3',clock=lambda:now[0])
        cfg=dict(enabled=True,learning_hourly_calls=2,learning_interval_seconds=300)
        store.efficiency=lambda:cfg
        b=dict(scope='qq:111:group:222',gid='g',sid='s',epoch='e')
        store.attach(b);store.configure(b,enabled=True)
        for i in range(20):store.observe(b,dict(source_id=str(i),text='群友的完整原话'+str(i),at=now[0],peer=True))
        now[0]+=31
        assert store.readiness(b)['ready']
        task=store.claim(b,'fixture')
        assert task and task['material']
        with store.db() as db:db.execute("UPDATE jobs SET state='pending',lease=NULL,lease_until=0")
        assert store.readiness(b)['reason']=='learning_interval'
        now[0]+=301
        with store.db() as db:db.execute('INSERT INTO calls VALUES(?,?,?,?)',('another-group','other-grant',now[0],10))
        assert store.readiness(b)['reason']=='global_budget'
        # Work and its material are preserved; the budget release makes it
        # eligible again rather than disabling learning or deleting the job.
        now[0]+=3601
        with store.db() as db:assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]>0
        assert store.readiness(b).get('reason')!='global_budget'


def main():
    events=Events();upstream=ThreadingHTTPServer(('127.0.0.1',0),OneBot)
    threading.Thread(target=upstream.serve_forever,daemon=True).start();OneBot.sent=[]
    try:
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',isolated_env(),clear=True):
            root=Path(tmp)
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n',encoding='utf-8')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                app=FastAPI();service=install_mcp_routes(app,Store(root/'chats.sqlite3'));access=service.access;chat=service.tools.chat
                with TestClient(app) as ui:
                    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
                    access.configure(True,port);service.start();invalidate_availability()
                    g=access.create(dict(name='fixture',platforms=['qq'],conversations=[['qq',f'111:group:{n}'] for n in range(222,230)],chat=True,send=True,chat_images=True,chat_poke=True))
                    grant=access.authorize(g['token'],source=False);cancel=threading.Event()
                    sids=[chat.start(grant,dict(conversation_id=f'111:group:{n}',persona='自然群友设定。'*700,idempotency_key=f'start-{n}'),cancel)['session']['id'] for n in range(222,230)]
                    eventually(lambda:chat.receiver.status()['state']=='connected')
                    for sid in sids:chat.learning.configure(sid,dict(enabled=False))
                    def configure(enabled):(root/'mcp-efficiency.json').write_text(json.dumps(dict(enabled=enabled)),encoding='utf-8')
                    configure(False)
                    full=chat.call(grant,'list_chat_sessions',dict(response_format='full',include_stopped=True),cancel)
                    compact=chat.call(grant,'list_chat_sessions',{},cancel)
                    assert chars(compact)<chars(full)*.2
                    first=chat.get(grant,sids[0],'compact');cached=chat.get(grant,sids[1],'compact',known_static_version=first['static_version'])
                    assert first['chat_prompt']['persona'] and 'persona' not in cached['chat_prompt']
                    assert cached['chat_prompt']['request_policy']==first['chat_prompt']['request_policy']
                    assert chars(cached)<chars(first)*.7
                    configure(True)
                    untouched=chat.row(sids[0])
                    setup=chat.get(grant,sids[0],'compact')
                    assert 'context' not in setup and 'chat_prompt' not in setup and 'input_batch' not in setup
                    assert not setup['unsettled_operations'] and setup['next_call']['arguments']['view']=='persona'
                    assert (chat.row(sids[0])['cursor'],chat.row(sids[0])['offered'])==(untouched['cursor'],untouched['offered'])
                    persona=chat.get(grant,sids[0],'compact',view='persona')
                    assert persona['static_context']['persona']==chat.row(sids[0])['persona']
                    cached_setup=chat.get(grant,sids[1],'compact',known_static_version=persona['static_version'])
                    assert cached_setup['next_call']['tool']=='wait_chat_messages'
                    text='很长但完整的群友原话'*500
                    chat.receive(dict(post_type='message',message_type='group',self_id=111,group_id=222,user_id=333,message_id=500,time=time.time(),message=[dict(type='text',data={'text':text})]))
                    args=dict(session_ids=sids,timeout_seconds=0,known_prompt_versions={sid:chat.public(chat.row(sid))['prompt_version'] for sid in sids})
                    r=chat.wait_many(grant,args,cancel,allow_learning=False);p=r['results'][0]
                    assert p['messages'][-1]['content']==text and p['decision_required']
                    short=r['next_call']['arguments'];assert chars(short)<chars(args)*.2
                    acked=chat.wait_many(grant,dict(short,silence_batches={sids[0]:p['input_batch']['id']}),cancel,allow_learning=False)
                    assert chat.row(sids[0])['cursor']==p['read_through_id']
                    assert not OneBot.sent
                    # Persistent send fence blocks an already READY plan,
                    # survives lease expiry, and cannot be reopened by renewal.
                    from chatlocal.mcp_runtime_control import configure as fence,blocked
                    chat.receive(dict(post_type='message',message_type='group',self_id=111,group_id=228,user_id=333,message_id=505,time=time.time(),message=[dict(type='text',data={'text':'栅栏测试'})]))
                    fence_batch=chat.wait(grant,dict(session_id=sids[6],timeout_seconds=0),cancel,allow_learning=False)
                    time.sleep(2.1)
                    fence_plan=chat.turns.plan(grant,dict(session_id=sids[6],batch_id=fence_batch['input_batch']['id'],action='reply',expressions=['text'],intent='隔离栅栏验收'),cancel)
                    assert fence_plan['state']=='READY',fence_plan
                    fence_args=dict(session_ids=[sids[6]],paused=True,reason='initializing',lease_id='a'*32)
                    assert ui.put('/api/mcp/chat-runtime',json=fence_args).status_code==403
                    assert ui.put('/api/mcp/chat-runtime',json=fence_args,headers={'X-ChatWeave-UI':'1','Origin':'https://untrusted.example'}).status_code==403
                    assert ui.put('/api/mcp/chat-runtime',json=fence_args,headers={'X-ChatWeave-UI':'1'}).status_code==200
                    attempt=chat.turns.submit(grant,dict(session_id=sids[6],plan_id=fence_plan['plan_id'],expression_ids=[],bubbles=[dict(kind='text',text='不应发出')]),cancel)
                    assert attempt['error_code']=='agent_paused' and not OneBot.sent
                    assert chat.row(sids[6])['active']
                    fence(access,dict(fence_args,paused=False,reason='live'))
                    assert not blocked(access,sids[6])
                    with patch('chatlocal.mcp_runtime_control.time.time',return_value=time.time()+61):assert blocked(access,sids[6])
                    fence(access,dict(fence_args,reason='budget'))
                    try:fence(access,dict(fence_args,paused=False,reason='live',renew=True))
                    except ValueError:pass
                    else:raise AssertionError('Renewal reopened a paused send fence')
                    chat.turns.plan(grant,dict(session_id=sids[6],batch_id=fence_batch['input_batch']['id'],action='silence',intent='仅验收，不发言'),cancel)
                    before_receipts=chat.row(sids[6])
                    chat.get(grant,sids[6],view='receipts')
                    assert (chat.row(sids[6])['cursor'],chat.row(sids[6])['offered'])==(before_receipts['cursor'],before_receipts['offered'])
                    chat.wait(grant,dict(session_id=sids[6],acknowledge_through_id=fence_batch['read_through_id'],timeout_seconds=0),cancel,allow_learning=False)
                    assert 'silence_batches' not in chat.wait_states[acked['next_call']['arguments']['wait_state_id']][2]
                    try:chat.wait_many(dict(grant,id='another-grant'),short,cancel,allow_learning=False)
                    except ValueError:pass
                    else:raise AssertionError('Cross-grant continuation accepted')
                    chat.receive(dict(post_type='message',message_type='group',self_id=111,group_id=223,user_id=333,message_id=501,time=time.time(),message=[dict(type='image',data={'file':'fixture-image'})]))
                    batch=chat.wait_many(grant,args,cancel,allow_learning=False)['results'][0]
                    from chatlocal.mcp_chat_materials import prepare
                    with patch.object(chat.media,'event_image',side_effect=AssertionError('Unexpected eager pixels')):
                        material,images=prepare(chat,grant,deepcopy(batch),cancel)
                    assert not images and material['materials']['images'][0]['state']=='on_demand'
                    call=material['materials']['images'][0]['read_call']
                    image=types.ImageContent(type='image',mimeType='image/png',data=base64.b64encode(b'fixture-pixels').decode())
                    with patch.object(chat.media,'event_image',return_value=(dict(real_pixels=True),[image])):
                        read=service.tools.call(g['token'],call['tool'],call['arguments'],cancel)
                    assert any(c.type=='image' for c in read.content),'Explicit image read lost pixels'
                    with patch.object(chat.media,'read',return_value=(dict(frame_indices=[0,2,4]),[image,image,image])):
                        preview,frames=chat.media.event_image(grant,dict(call['arguments'],max_frames=1),cancel)
                    assert len(frames)==1 and preview['more_sampled_frames'] and preview['frame_indices']==[0]
                    before=chat.row(sids[1])['cursor']
                    waiting=chat.turns.plan(grant,dict(session_id=sids[1],batch_id=batch['input_batch']['id'],action='wait',intent='听后续',wait_seconds=20),cancel)
                    assert waiting['state']=='WAITING'
                    try:chat.wait_many(grant,dict(args,silence_batches={sids[1]:batch['input_batch']['id']}),cancel,allow_learning=False)
                    except ValueError:pass
                    else:raise AssertionError('WAITING bypassed by silence')
                    assert chat.row(sids[1])['cursor']==before
                    # Explicit context pagination remains complete and never
                    # appends old operation bodies to ordinary message reads.
                    for mid in range(510,530):
                        chat.receive(dict(post_type='message',message_type='group',self_id=111,group_id=225,user_id=333,message_id=mid,time=time.time(),message=[dict(type='text',data={'text':'完整原文-'+str(mid)})]))
                    page=chat.get(grant,sids[3],'compact',known_static_version=persona['static_version'],view='context',context_limit=3)
                    assert len(page['context']['messages'])==3 and page['context']['has_more']
                    assert 'recent_operations' not in page and 'recent_turns' not in page
                    assert 'persona' not in page['chat_prompt'] and page['context']['messages'][0]['content']=='完整原文-510'
                    owner=OwnerGate(access);owner.configure(g['id'],dict(enabled=True,owner_qq='33333',expected_revision=0))
                    denied=service.tools.call(g['token'],'send_chat_reply',dict(session_id=sids[0],plan_id='0'*32,expression_ids=[],bubbles=[dict(kind='text',text='x',mention_all=True)]),cancel)
                    assert data(denied)['error_code']=='owner_required'
                    unknown=dict(state='UNKNOWN',bubbles=[dict(state='UNKNOWN',receipt={'id':'receipt','result':{'message_id':42}})])
                    assert compact_dynamic(unknown)==unknown
                    chat.receive(dict(post_type='message',message_type='group',self_id=111,group_id=224,user_id=333,message_id=502,time=time.time(),message=[dict(type='text',data={'text':'自然接话测试'})]))
                    time.sleep(2.1)
                    with access.connect() as db:db.execute('UPDATE chat_sessions SET persona=persona||? WHERE id=?',('\n仅该群的额外表达偏好。',sids[7]))
                    report=asyncio.run(transport(service,g['token'],sids,configure))
                    assert report['directory_optimized_chars']<report['directory_legacy_chars']*.12
                    report.update(semantic_text_preserved=True,cross_group_static_cached=True,explicit_pixels_work=True,
                        silent_wait_uses_original_planner=True,waiting_not_acknowledged=True,owner_gate_preserved=True,
                        unknown_receipt_preserved=True,real_qq_writes=0,continuation_old_chars=chars(args),continuation_new_chars=chars(short))
                    print(json.dumps(report))
        learning();print('PASS: learning budget shared across groups; retained work eligible after budget release')
        import chat_bridge
        with tempfile.TemporaryDirectory() as bridge_tmp,patch.object(chat_bridge,'ROOT',Path(bridge_tmp)):
            source=dict(state='UNKNOWN',owner_proof={'source':'完整授权数据'},body='完整原文'*10000)
            deferred=json.loads(chat_bridge.bounded(source,1000))
            assert deferred['delivery']=='deferred_unread' and deferred['ack_allowed'] is False
            assert json.loads(Path(deferred['path']).read_text('utf-8'))==source
        print('PASS: bounded bridge preserves oversized result verbatim and forbids ACK before reading')
    finally:events.close();upstream.shutdown();upstream.server_close()


if __name__=='__main__':main()
