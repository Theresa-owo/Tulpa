"""Realtime waits against temporary databases and loopback OneBot fixtures.

No credentials, real sessions, external models or QQ sends are used.
"""
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
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
                    active=[sid for sid in sessions if chat.row(sid)['active']]
                    with ThreadPoolExecutor(1) as pool:
                        future=pool.submit(chat.wait_many,grant,dict(session_ids=active,timeout_seconds=5),cancel,allow_learning=False)
                        eventually(lambda:len(chat.waiters)==len(active))
                        access.revoke(grant['id'])
                        try:future.result(2)
                        except AccessDenied:pass
                        else:raise AssertionError('Revoked grant continued waiting')
                    assert not chat.waiters and not chat.events
                    assert not OneBot.sent,'Realtime checks sent a message'
        print('PASS: immediate default delivery, zero-timeout scan, eight-group readiness/fairness, selective enrichment, per-group ACKs, deferred Planner, duplicate ownership, cancel/stop/revoke, scan/sleep race and learning priority/handoff; temporary data only, no QQ/model writes.')
    finally:
        events.close();upstream.shutdown();upstream.server_close()


if __name__=='__main__':main()
