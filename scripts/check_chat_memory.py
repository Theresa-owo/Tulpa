"""Durable scoped memory over the real local MCP; no model or real QQ writes."""
import asyncio
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from check_fixture_support import isolated_env
from check_mcp_chat import Events,OneBot,eventually
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.onebot import invalidate_availability
from chatlocal.store import Store


def value(result):
    return result.structuredContent or json.loads(next(c.text for c in result.content if c.type=='text'))


def check_no_fts_migration():
    from chatlocal.mcp_chat_memory import ChatMemory
    with tempfile.TemporaryDirectory() as tmp:
        access=SimpleNamespace(path=Path(tmp)/'access.sqlite3',ensure_revision=lambda grant:grant)
        chat=SimpleNamespace(access=access);actual_connect=sqlite3.connect
        class NoFts:
            def __init__(self,inner):object.__setattr__(self,'inner',inner)
            def __getattr__(self,name):return getattr(self.inner,name)
            def __setattr__(self,name,value):setattr(self.inner,name,value)
            def executescript(self,script):
                if 'CREATE VIRTUAL TABLE' in script:raise sqlite3.OperationalError('no such module: fts5')
                return self.inner.executescript(script)
        with patch('chatlocal.mcp_chat_memory.sqlite3.connect',side_effect=lambda *a,**k:NoFts(actual_connect(*a,**k))):
            fallback=ChatMemory(chat)
        assert not fallback.fts_available
        with closing(fallback.connect()) as db:
            with db:db.execute('''INSERT INTO chat_memories(memory_id,grant_id,conversation_id,content,keywords,importance,evidence,fingerprint,created,updated)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',('a'*32,'g','c','无索引也记得白龙爱喝茶','[]',3,'[]','b'*64,1,1))
        fallback.scope=lambda grant,sid:(grant,'c')
        assert fallback.search({'id':'g'},'s',dict(query='白龙爱喝茶'),threading.Event())['items']
        rebuilt=ChatMemory(chat);rebuilt.scope=lambda grant,sid:(grant,'c')
        assert rebuilt.fts_available and rebuilt.search({'id':'g'},'s',dict(query='白龙爱喝茶'),threading.Event())['items']


async def wire(port,token,sid,eid):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},timeout=20,trust_env=False) as http:
        async with streamable_http_client(f'http://127.0.0.1:{port}/mcp',http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize();names={t.name for t in (await client.list_tools()).tools}
                assert {'remember_chat_memory','search_chat_memories','read_chat_memory'}<=names
                args=dict(session_id=sid,content='小蓝喜欢骑自行车',keywords=['小蓝','自行车'],importance=4,source_event_ids=[eid])
                saved=value(await client.call_tool('remember_chat_memory',args))
                assert saved['saved'] and not saved['authorizes_actions'] and saved['revision']==1,saved
                same=value(await client.call_tool('remember_chat_memory',args))
                assert same['deduplicated'] and same['memory_id']==saved['memory_id']
                found=value(await client.call_tool('search_chat_memories',dict(session_id=sid,query='小蓝喜欢自行车')))
                assert [x['memory_id'] for x in found['items']]==[saved['memory_id']]
                short=value(await client.call_tool('search_chat_memories',dict(session_id=sid,query='小蓝')))
                assert short['items'][0]['memory_id']==saved['memory_id']
                detailed=value(await client.call_tool('read_chat_memory',dict(session_id=sid,memory_id=saved['memory_id'],source_index=0,source_limit=4)))
                assert detailed['source']['text']=='我喜欢骑' and detailed['source']['next_offset']==4,detailed
                return saved['memory_id'],found['snapshot_max_id']


def main():
    check_no_fts_migration()
    events=Events();server=ThreadingHTTPServer(('127.0.0.1',0),OneBot)
    threading.Thread(target=server.serve_forever,daemon=True).start();OneBot.sent=[]
    try:
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',isolated_env(),clear=True):
            root=Path(tmp)
            os.environ['TEMP']=str(root);os.environ['TMP']=str(root)
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{server.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                app=FastAPI();service=install_mcp_routes(app,Store(root/'chats.sqlite3'));access=service.access;chat=service.tools.chat
                with TestClient(app):
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    access.configure(True,port);service.start();invalidate_availability()
                    scope=dict(platforms=['qq'],conversations=[['qq','111:group:222'],['qq','111:group:223'],['qq','111:group:224']],chat=True,send=True)
                    a=access.create(dict(name='memory-a',**scope));b=access.create(dict(name='memory-b',**scope))
                    grant=access.authorize(a['token'],source=False);other=access.authorize(b['token'],source=False);cancel=threading.Event()
                    def start(g,cid,key):return chat.start(g,dict(conversation_id=cid,persona='自然聊天',idempotency_key=key),cancel)['session']['id']
                    sid=start(grant,'111:group:222','memory-a-one');sid_other_group=start(grant,'111:group:223','memory-a-two')
                    sid_other_grant=start(other,'111:group:224','memory-b-one')
                    eventually(lambda:chat.receiver.status()['state']=='connected')
                    chat.receive(dict(post_type='message',message_type='group',self_id=111,group_id=222,user_id=333,message_id=123,
                                      time=time.time(),message=[dict(type='text',data={'text':'我喜欢骑自行车，尤其是蓝色的。'})]))
                    packet=chat.wait(grant,dict(session_id=sid,timeout_seconds=0),cancel,allow_learning=False)
                    eid=packet['messages'][0]['id']
                    memory_id,snapshot=asyncio.run(wire(port,a['token'],sid,eid))
                    def call(name,**args):return value(service.tools.call(a['token'],name,args,cancel))
                    assert call('get_chat_session',session_id=sid,view='setup')['remembered_facts']==1
                    assert not call('search_chat_memories',session_id=sid_other_group,query='小蓝')['items']
                    denied=service.tools.call(b['token'],'read_chat_memory',dict(session_id=sid_other_grant,memory_id=memory_id),cancel)
                    assert denied.isError or value(denied).get('error')
                    missing=service.tools.call(a['token'],'remember_chat_memory',dict(session_id=sid,content='不应保存',source_event_ids=[999999999]),cancel)
                    assert missing.isError or value(missing).get('error')
                    observed=chat.memory.evidence(sid,[eid]);changed=[dict(observed[0],text='已变化')]
                    with patch.object(chat.memory,'evidence',side_effect=[observed,changed]):
                        raced=service.tools.call(a['token'],'remember_chat_memory',dict(session_id=sid,content='竞争期间不保存',source_event_ids=[eid]),cancel)
                    assert raced.isError or value(raced).get('error')
                    assert not call('search_chat_memories',session_id=sid,query='竞争期间不保存')['items']
                    with access.connect() as db:db.execute("UPDATE chat_inbox SET payload=json_set(payload,'$.recalled',true) WHERE id=?",(eid,))
                    recalled=service.tools.call(a['token'],'remember_chat_memory',dict(session_id=sid,content='撤回后不保存',source_event_ids=[eid]),cancel)
                    assert recalled.isError or value(recalled).get('error')
                    with access.connect() as db:db.execute("UPDATE chat_inbox SET payload=json_set(payload,'$.recalled',false) WHERE id=?",(eid,))
                    saved=call('remember_chat_memory',session_id=sid,content='小蓝喜欢登山',memory_id=memory_id,expected_revision=1,keywords=['山'])
                    assert saved['revision']==2
                    replay=call('remember_chat_memory',session_id=sid,content='小蓝喜欢登山',memory_id=memory_id,expected_revision=1,keywords=['山'])
                    assert replay['revision']==2 and replay['deduplicated']
                    sourced=call('remember_chat_memory',session_id=sid,content='当时原话',source_event_ids=[eid])
                    stale=service.tools.call(a['token'],'remember_chat_memory',dict(session_id=sid,content='旧版本',memory_id=memory_id,expected_revision=1),cancel)
                    assert stale.isError or value(stale).get('error')
                    assert call('search_chat_memories',session_id=sid,query='喜欢登山')['items'][0]['memory_id']==memory_id
                    second=call('remember_chat_memory',session_id=sid,content='另一个长期事实')
                    frozen=call('search_chat_memories',session_id=sid,snapshot_max_id=snapshot)
                    assert second['memory_id'] not in {x['memory_id'] for x in frozen['items']}
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        duplicates=list(pool.map(lambda _:call('remember_chat_memory',session_id=sid,content='并发只保存一次'),range(8)))
                    assert len({item['memory_id'] for item in duplicates})==1
                    assert sum(not item['deduplicated'] for item in duplicates)==1
                    needle=call('remember_chat_memory',session_id=sid,content='独一无二的火星柚子茶',keywords=['火星柚子'])
                    with closing(chat.memory.connect()) as db:
                        with db:db.executemany('''INSERT INTO chat_memories(memory_id,grant_id,conversation_id,content,keywords,importance,evidence,fingerprint,created,updated)
                            VALUES(?,?,?,?,?,?,?,?,?,?)''',[(f'{i:032x}',grant['id'],'111:group:222',f'长期记录第{i}条','[]',2,'[]',f'{i:064x}',1,1) for i in range(1,5001)])
                    found=call('search_chat_memories',session_id=sid,query='火星柚子茶')
                    assert found['items'][0]['memory_id']==needle['memory_id']
                    with closing(chat.memory.connect()) as db:assert db.execute('SELECT count(*) FROM chat_memories').fetchone()[0]>=5000
                    # Hosts lacking trigram can still use bounded fallback;
                    # when FTS becomes available, rebuild from retained rows.
                    chat.memory.fts_available=False
                    assert call('search_chat_memories',session_id=sid,query='火星柚子茶')['items'][0]['memory_id']==needle['memory_id']
                    chat.memory.fts_available=True
                    with closing(chat.memory.connect()) as db:
                        with db:
                            db.execute("INSERT INTO chat_memory_fts(chat_memory_fts) VALUES('delete-all')")
                            db.execute("INSERT OR REPLACE INTO chat_memory_meta VALUES('fts_needs_rebuild','1')")
                    from chatlocal.mcp_chat_memory import ChatMemory
                    rebuilt=ChatMemory(chat);assert rebuilt.search(grant,sid,dict(query='火星柚子茶'),cancel)['items'][0]['memory_id']==needle['memory_id']
                    # A new session for the same authorized conversation finds
                    # the same durable memory even after old inbox retention.
                    chat.stop(sid,grant['id'])
                    same_group_other_grant=start(other,'111:group:222','memory-b-same-group')
                    denied=service.tools.call(b['token'],'read_chat_memory',dict(session_id=same_group_other_grant,memory_id=memory_id),cancel)
                    assert denied.isError or value(denied).get('error')
                    chat.stop(same_group_other_grant,other['id']);new_sid=start(grant,'111:group:222','memory-a-reopen')
                    restored=call('read_chat_memory',session_id=new_sid,memory_id=memory_id)
                    assert restored['content']=='小蓝喜欢登山' and not restored['authorizes_actions']
                    preserved=call('read_chat_memory',session_id=new_sid,memory_id=sourced['memory_id'],source_index=0)
                    assert preserved['source']['text']=='我喜欢骑自行车，尤其是蓝色的。'
                    reopened=ChatMemory(chat);assert reopened.search(grant,new_sid,dict(query='登山'),cancel)['items']
                    assert not OneBot.sent
                    print('PASS: scoped durable SQLite memory, Chinese retrieval, pagination snapshot, evidence, dedup, revision, reopen and isolation; zero model/real QQ writes')
    finally:events.close();server.shutdown();server.server_close()


if __name__=='__main__':main()
