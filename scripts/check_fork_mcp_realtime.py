"""Realtime waits against temporary databases and loopback OneBot fixtures.

No credentials, real sessions, external models or QQ sends are used.
"""
from concurrent.futures import ThreadPoolExecutor
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
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_fixture_support import isolated_env
from check_mcp_chat import OneBot, Events, eventually
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatlocal.store import Store
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.mcp_access import AccessDenied
from chatlocal.onebot import invalidate_availability


def main():
    assert Path(__import__('chatlocal.mcp_chat',fromlist=['MCPChat']).__file__).resolve()==ROOT/'chatlocal/mcp_chat.py'
    events=Events();OneBot.sent=[]
    upstream=ThreadingHTTPServer(('127.0.0.1',0),OneBot)
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    ports={events.port,upstream.server_port};connect=socket.create_connection
    def restrict(address,*args,**kwargs):
        assert address[0] in ('127.0.0.1','localhost','::1') and address[1] in ports,address
        return connect(address,*args,**kwargs)
    try:
        with tempfile.TemporaryDirectory(prefix='tulpa-realtime-') as tmp,patch.dict('os.environ',isolated_env(),clear=True),patch('socket.create_connection',restrict):
            root=Path(tmp);store=Store(root/'chats.sqlite3')
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                app=FastAPI();service=install_mcp_routes(app,store);access=service.access;chat=service.tools.chat
                with TestClient(app):
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    ports.add(port);access.configure(True,port);service.start();invalidate_availability()
                    g=access.create(dict(name='realtime-fixture',platforms=['qq'],
                        conversations=[['qq',f'111:group:{n}'] for n in range(222,230)],send=True,chat=True))
                    grant=access.authorize(g['token'],source=False);cancel=threading.Event()
                    sessions=[chat.start(grant,dict(conversation_id=f'111:group:{n}',persona='fixture persona',
                        idempotency_key=f'fixture-start-{n}'),cancel)['session']['id'] for n in range(222,230)]
                    counter=0
                    def add(index,text='synthetic input'):
                        nonlocal counter
                        counter+=1
                        chat.receive(dict(post_type='message',message_type='group',self_id=111,group_id=222+index,
                            message_id=counter,user_id=333,sender={'nickname':'Fixture'},time=time.time(),
                            message=[dict(type='text',data={'text':text})]))
                    def single(index=0,**args):
                        return chat.wait(grant,dict(session_id=sessions[index],**args),cancel,allow_learning=False)
                    def many(**args):
                        return chat.wait_many(grant,dict(session_ids=sessions,**args),cancel,allow_learning=False)
                    def drain():
                        for index,sid in enumerate(sessions):
                            if not chat.row(sid)['active']:continue
                            packet=single(index,timeout_seconds=0)
                            single(index,timeout_seconds=0,acknowledge_through_id=packet['read_through_id'])

                    for ids in ([],sessions+['f'*32],[sessions[0],sessions[0]]):
                        try:chat.wait_many(grant,dict(session_ids=ids,timeout_seconds=0),cancel)
                        except ValueError:pass
                        else:raise AssertionError('Accepted an invalid multi-group admission')

                    add(0);start=time.monotonic();packet=single()
                    assert packet['event']=='messages' and time.monotonic()-start<.75
                    assert packet['input_batch']['plan_call']['tool']=='plan_chat_reply' and packet['decision_required']
                    assert chat.row(sessions[0])['cursor']==0,'Delivery ACKed input'
                    drain();start=time.monotonic();assert single(timeout_seconds=0)['event']=='idle'
                    assert time.monotonic()-start<.75

                    # Zero timeout must inspect every group before selecting idle.
                    add(7);chat._wait_rotation=0;start=time.monotonic();packet=many(timeout_seconds=0)
                    assert packet['results'][0]['session_id']==sessions[7] and packet['event']=='messages'
                    assert time.monotonic()-start<.75
                    assert packet['next_call']['tool']=='wait_chat_messages_many'
                    assert packet['results'][0]['next_call']['tool']=='plan_chat_reply'
                    drain()

                    # No enrichment work for unselected groups; rotate hot input.
                    add(0);add(1);chat._wait_rotation=0
                    enrich=chat.details.enrich;enriched=[]
                    def selected_only(grant,row,items,*args,**kwargs):
                        enriched.append(row['id']);return enrich(grant,row,items,*args,**kwargs)
                    with patch.object(chat.details,'enrich',selected_only):
                        first=many(timeout_seconds=0);second=many(timeout_seconds=0)
                    assert enriched==sessions[:2]
                    assert [first['results'][0]['session_id'],second['results'][0]['session_id']]==sessions[:2]
                    cursor=first['results'][0]['read_through_id']
                    other_cursor=chat.row(sessions[1])['cursor']
                    many(timeout_seconds=0,acknowledge_through_ids={sessions[0]:cursor})
                    assert chat.row(sessions[0])['cursor']==cursor and chat.row(sessions[1])['cursor']==other_cursor
                    drain()

                    # ACK validation is all-or-nothing for malformed multi-group requests.
                    add(0);offered=single(timeout_seconds=0)['read_through_id'];old=chat.row(sessions[0])['cursor']
                    try:many(timeout_seconds=0,acknowledge_through_ids={sessions[0]:offered,sessions[1]:10**9})
                    except ValueError:pass
                    else:raise AssertionError('Accepted an unseen ACK')
                    assert chat.row(sessions[0])['cursor']==old
                    drain()

                    # A Planner WAITING decision is not an ACK, even through
                    # multi-group APIs. New input re-delivers the deferred batch.
                    add(0);packet=single(timeout_seconds=0);old=chat.row(sessions[0])['cursor']
                    planned=chat.turns.plan(grant,dict(session_id=sessions[0],batch_id=packet['input_batch']['id'],
                        action='wait',intent='fixture deferred decision',wait_seconds=20,idempotency_key='fixture-wait-plan'),cancel)
                    assert planned['state']=='WAITING'
                    early=many(timeout_seconds=0,acknowledge_through_ids={sessions[0]:packet['read_through_id']})
                    assert chat.row(sessions[0])['cursor']==old and not early['results'][0]['messages']
                    add(0,'new input ends deferred decision')
                    resumed=many(timeout_seconds=0)
                    assert resumed['event']=='messages' and len(resumed['results'][0]['messages'])==2
                    assert not chat.turns.waiting(sessions[0]) and resumed['results'][0]['decision_required']
                    drain()

                    # A real notice wakes the same multi-group wait, is deduped,
                    # and remains a notice (never an owner command or QQ quote).
                    poke=dict(post_type='notice',notice_type='notify',sub_type='poke',self_id=111,
                        group_id=222,user_id=333,target_id=111,time=time.time())
                    with ThreadPoolExecutor(1) as pool:
                        future=pool.submit(many,timeout_seconds=5)
                        eventually(lambda:len(chat.waiters)==8)
                        events.send(poke)
                        packet=future.result(2)['results'][0]
                    assert packet['session_id']==sessions[0] and packet['decision_required']
                    notice=packet['messages'][-1]
                    assert notice['source']=='onebot_notice' and notice['synthetic']
                    assert notice['notice']==dict(type='poke',operator_id='333',target_id='111')
                    chat.receive(poke)
                    assert len(single(timeout_seconds=0)['messages'])==1,'Duplicate poke was delivered twice'
                    for change in (dict(user_id=111),dict(target_id=444),dict(group_id=999),dict(self_id=999),dict(user_id='bad')):
                        chat.receive(dict(poke,**change))
                    assert len(single(timeout_seconds=0)['messages'])==1
                    from chatlocal.mcp_chat_output import compact_wait
                    compact=compact_wait(packet,packet['chat_guidance']['prompt_version'])
                    assert any('拍' in hint for hint in compact['chat_guidance']['reminders'])
                    try:chat.response_target(chat.row(sessions[0]),notice['id'])
                    except ValueError:pass
                    else:raise AssertionError('Notice accepted as a quotable QQ message')
                    time.sleep(2.1)  # Respect the existing Planner burst window.
                    packet=single(timeout_seconds=0)
                    planned=chat.turns.plan(grant,dict(session_id=sessions[0],batch_id=packet['input_batch']['id'],
                        action='reply',intent='natural reply to tap',target_event_id=notice['id'],expressions=['text']),cancel)
                    assert planned['state']=='READY',planned
                    outcome=chat.turns.submit(grant,dict(session_id=sessions[0],plan_id=planned['plan_id'],
                        expression_ids=[],bubbles=[dict(kind='text',text='？',quote=False)]),cancel)
                    assert outcome['state']=='SUCCEEDED',outcome
                    assert OneBot.sent[-1]['message']==[dict(type='text',data=dict(text='？'))]
                    drain()

                    # Same session cannot be owned by a second wait; cancellation
                    # and stop wake the shared signal without leaving owners.
                    with ThreadPoolExecutor(1) as pool:
                        future=pool.submit(many,timeout_seconds=5)
                        eventually(lambda:len(chat.waiters)==8)
                        try:single(timeout_seconds=0)
                        except ValueError:pass
                        else:raise AssertionError('Overlapping single wait accepted')
                        cancel.set();assert future.result(2)['event']=='cancelled';cancel.clear()
                        assert not chat.waiters and not chat.events
                        future=pool.submit(many,timeout_seconds=5)
                        eventually(lambda:len(chat.waiters)==8)
                        chat.stop(sessions[3],grant['id'])
                        assert future.result(2)['event']=='stopped'
                        assert not chat.waiters

                    # A receive between scanning durable state and sleeping must
                    # remain signalled (clear-after-sleep loses precisely this wake).
                    event_type=threading.Event;waited=[];injected=False
                    class ObservedEvent(event_type):
                        def wait(self,timeout=None):
                            waited.append(self.is_set())
                            assert self.is_set(),'Lost a receive after the durable scan'
                            return super().wait(timeout)
                    collection=chat.turns.collection
                    def inject(row,pending,quiet,started):
                        nonlocal injected
                        if not injected and row['id']==sessions[0] and not pending:
                            injected=True;add(0,'arrived at scan/sleep boundary')
                        return collection(row,pending,quiet,started)
                    with patch('chatlocal.mcp_chat.threading.Event',ObservedEvent),patch.object(chat.turns,'collection',inject):
                        packet=single(timeout_seconds=2)
                    assert packet['event']=='messages' and waited==[True]
                    drain()

                    # The same upstream kernel supplies learning handoffs for both APIs.
                    learning=dict(ready=True,next_call=dict(tool='claim_chat_learning',arguments={'session_id':sessions[0]}))
                    with patch.object(chat.learning,'wake',return_value=learning):
                        packet=chat.wait_many(grant,dict(session_ids=sessions[:2],timeout_seconds=0),cancel)
                    assert packet['results'][0]['event']=='learning_ready'
                    assert packet['results'][0]['next_call']['tool']=='claim_chat_learning'
                    assert packet['next_call']['tool']=='wait_chat_messages_many'
                    add(1,'messages take priority over learning in another group')
                    chat._wait_rotation=0
                    with patch.object(chat.learning,'wake',return_value=learning):
                        packet=chat.wait_many(grant,dict(session_ids=sessions[:2],timeout_seconds=0),cancel)
                    assert packet['event']=='messages' and packet['results'][0]['session_id']==sessions[1]
                    drain()

                    # An ignored learning hint must not wake the model hundreds
                    # of times for the same job; new chat and new stages still
                    # win immediately, and an abandoned hint is retried.
                    policy=root/'mcp-efficiency.json'
                    policy.write_text(json.dumps(dict(enabled=True,learning_wake_retry_seconds=60)),'utf-8')
                    wake_id=['a'*64]
                    def readiness(binding):
                        return (dict(ready=True,reason='ready',job_id='b'*32,stage='extract',wake_id=wake_id[0])
                                if binding['sid']==sessions[0] else dict(ready=False,reason='idle'))
                    with patch.object(chat.learning,'readiness',side_effect=readiness):
                        first=chat.wait_many(grant,dict(session_ids=sessions[:2],timeout_seconds=0),cancel)
                        assert first['event']=='learning_ready'
                        assert first['pending_action']==first['results'][0]['next_call']
                        again=chat.wait_many(grant,dict(session_ids=sessions[:2],timeout_seconds=0),cancel)
                        assert again['event']=='idle',again
                        add(1,'new input must not wait for a learning retry')
                        current=chat.wait_many(grant,dict(session_ids=sessions[:2],timeout_seconds=0),cancel)
                        assert current['event']=='messages' and current['results'][0]['session_id']==sessions[1]
                        drain()
                        wake_id[0]='c'*64
                        changed=chat.wait_many(grant,dict(session_ids=sessions[:2],timeout_seconds=0),cancel)
                        assert changed['event']=='learning_ready' and changed['pending_action']['arguments']['wake_id']==wake_id[0]
                        key=(grant['id'],sessions[0],wake_id[0]);chat.learning.delivered_wakes[key]-=61
                        retry=chat.wait_many(grant,dict(session_ids=sessions[:2],timeout_seconds=0),cancel)
                        assert retry['event']=='learning_ready' and retry['pending_action']['arguments']['wake_id']==wake_id[0]
                    policy.unlink()
                    active=[sid for sid in sessions if chat.row(sid)['active']]
                    with ThreadPoolExecutor(1) as pool:
                        future=pool.submit(chat.wait_many,grant,dict(session_ids=active,timeout_seconds=5),cancel,allow_learning=False)
                        eventually(lambda:len(chat.waiters)==len(active))
                        access.revoke(grant['id'])
                        try:future.result(2)
                        except AccessDenied:pass
                        else:raise AssertionError('Revoked grant continued waiting')
                    assert not chat.waiters and not chat.events
        assert len(OneBot.sent)==1,'Expected only the isolated poke text reply'
        print('PASS: realtime waits, poke wake/dedup/scope/echo suppression, Planner text reply, per-group ACKs, cancellation and learning; loopback fixtures only, no real QQ/model writes.')
    finally:
        events.close();upstream.shutdown();upstream.server_close()


if __name__=='__main__':main()
