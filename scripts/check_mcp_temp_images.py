"""Exercise temporary image pages and cleanup with isolated loopback fixtures."""
import asyncio
import base64
from http.server import ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from check_fixture_support import isolated_env
from check_mcp_chat import Events,eventually
from check_mcp_chat_media import StickerBot
from chatlocal.mcp_chat_temp_images import cleanup_previews
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.store import Store
from chatlocal.onebot import invalidate_availability
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp import types
from PIL import Image,ImageDraw


def fixture_image(size=(1345,14999),frames=1):
    output=io.BytesIO()
    if frames>1:
        images=[Image.new('RGB',size,(i*43%255,90,160)) for i in range(frames)]
        images[0].save(output,'GIF',save_all=True,append_images=images[1:],duration=100,loop=0)
    else:
        im=Image.new('RGB',size,'white');draw=ImageDraw.Draw(im)
        for y in range(0,size[1],500):draw.rectangle((0,y,size[0]-1,min(y+180,size[1]-1)),fill=(y%255,80,200))
        im.save(output,'JPEG',quality=85)
    return output.getvalue()


def data(result):
    return result.structuredContent or json.loads(next(x.text for x in result.content if x.type=='text'))


async def wire_pages(service,token,sid,eid):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},timeout=30,trust_env=False) as http:
        async with streamable_http_client(service.status()['url'],http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                schema=next(x for x in (await client.list_tools()).tools if x.name=='read_chat_image')
                assert {'temporary','read_id','tile_offset','all_frames'}<=schema.inputSchema['properties'].keys()
                arguments=dict(session_id=sid,event_id=eid,temporary=True);seen=0
                while True:
                    result=await client.call_tool('read_chat_image',arguments);value=data(result)
                    assert not result.isError,value
                    blocks=[x for x in result.content if x.type=='image'];assert blocks
                    for block in blocks:Image.open(io.BytesIO(base64.b64decode(block.data))).verify()
                    seen+=len(blocks)
                    if not value['has_more']:
                        assert value['temporary_deleted'];break
                    arguments=value['next_call']['arguments']
                assert seen>=11


def bridge_checks(folder):
    import chat_bridge
    raw=fixture_image((20,20))
    block=types.ImageContent(type='image',mimeType='image/jpeg',data=base64.b64encode(raw).decode('ascii'))
    result=types.CallToolResult(content=[types.TextContent(type='text',text='{"temporary":true}'),block],
                                structuredContent={'temporary':True})
    with patch.object(chat_bridge,'ROOT',folder):
        value=chat_bridge.payload(result);preview=value['local_images'][0]
        path=Path(preview['path']);assert path.read_bytes()==raw
        Image.open(path).verify()
        unrelated=path.parent/'user-owned.png';unrelated.write_bytes(b'keep')
        fresh=path.parent/('c'*32+'.jpg');fresh.write_bytes(b'fresh')
        old=path.parent/('d'*32+'.jpg');old.write_bytes(b'old');os.utime(old,(time.time()-600,)*2)
        assert cleanup_previews(path.parent)==1 and not old.exists()
        assert fresh.exists() and unrelated.exists() and path.exists()
        try:cleanup_previews(path.parent,['../user-owned'])
        except ValueError:pass
        else:raise AssertionError('Invalid cleanup identifier accepted')
        assert cleanup_previews(path.parent,[preview['preview_id']])==1
        assert not path.exists() and fresh.exists() and unrelated.exists()


def main():
    events=Events();StickerBot.sent=[];StickerBot.uncertain=False
    upstream=ThreadingHTTPServer(('127.0.0.1',0),StickerBot)
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='tulpa-temp-images-') as tmp,patch.dict('os.environ',isolated_env(),clear=True):
            root=Path(tmp);bridge_checks(root)
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            (root/'mcp-efficiency.json').write_text('{"enabled":true,"images":"on_demand"}','utf-8')
            store=Store(root/'chats.sqlite3');app=FastAPI();service=install_mcp_routes(app,store)
            access=service.access;tools=service.tools;cancel=threading.Event()
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root),TestClient(app):
                import socket
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                access.configure(True,port);service.start();invalidate_availability()
                config=dict(name='temporary-fixture',platforms=['qq'],conversations=[['qq','111:group:222']],
                            send=True,chat=True,chat_images=True,chat_sticker_send=True)
                conn=access.create(config);grant=access.authorize(conn['token'],source=False)
                def call(name,**arguments):return tools.call(conn['token'],name,arguments,cancel)
                started=data(call('start_chat_session',conversation_id='111:group:222',persona='fixture',idempotency_key='fixture-session'))
                assert 'session' in started,started
                sid=started['session']['id']
                (root/'mcp-chat-media-policy.json').write_text(json.dumps({'temporary_sessions':[sid]}),'utf-8')
                eventually(lambda:tools.chat.receiver.status()['state']=='connected')
                counter=[100]
                def add():
                    counter[0]+=1;mid=counter[0]
                    events.send(dict(post_type='message',message_type='group',self_id=111,group_id=222,message_id=mid,
                        user_id=333,time=time.time(),message=[dict(type='image',data={'url':'https://gchat.qpic.cn/fixture.jpg','file':'gif1'})]))
                    def lookup():
                        with access.connect() as db:return db.execute('SELECT id FROM chat_inbox WHERE session_id=? AND message_id=?',(sid,str(mid))).fetchone()
                    eventually(lookup);eid=lookup()[0]
                    result=call('wait_chat_messages',session_id=sid,timeout_seconds=0);packet=data(result)
                    assert any(x['id']==eid for x in packet['messages'])
                    return eid,packet['read_through_id'],result
                raw=fixture_image();eid,cursor,incoming=add();downloads=[]
                def fetch(*args,**kwargs):downloads.append(1);return raw
                with patch('chatlocal.mcp_chat_media.download',side_effect=fetch):
                    result=call('read_chat_image',session_id=sid,event_id=eid,max_frames=3)
                    result_data=data(result);assert not result.isError,result_data
                    assert result_data['temporary'] and result_data['dimensions']==[1345,14999]
                    assert result_data['has_more'] and not result_data['temporary_deleted']
                    ident=result_data['next_call']['arguments']['read_id']
                    temporary_root=root/'mcp-chat-temporary-images'
                    active=list(temporary_root.glob('tulpa-*.tmp'));assert len(active)==1
                    orphan=temporary_root/('tulpa-'+'e'*32+'.tmp');orphan.write_bytes(b'orphan')
                    other=temporary_root/'user-owned.tmp';other.write_bytes(b'keep')
                    old=time.time()-600;os.utime(orphan,(old,old));os.utime(active[0],(old,old))
                    tools.chat.media.temporary.cleanup_orphans()
                    assert not orphan.exists() and active[0].exists() and other.exists()
                    bad=call('read_chat_image',session_id=sid,event_id=eid+1,read_id=ident,tile_offset=3,temporary=True)
                    assert bad.isError and ident in tools.chat.media.temporary.jobs
                    tiles=[];images=0
                    while True:
                        value=data(result);tiles.extend(value['tiles'])
                        blocks=[x for x in result.content if x.type=='image'];images+=len(blocks)
                        for block in blocks:Image.open(io.BytesIO(base64.b64decode(block.data))).verify()
                        if not value['has_more']:
                            assert value['temporary_deleted'] and not value.get('cleanup_pending');break
                        result=call('read_chat_image',**value['next_call']['arguments'])
                        assert not result.isError,data(result)
                    assert len(downloads)==1 and images==len(tiles)>=11
                    assert min(x['bounds'][1] for x in tiles)==0 and max(x['bounds'][3] for x in tiles)==14999
                    assert not list((root/'mcp-chat-temporary-images').glob('tulpa-*.tmp'))
                    assert not list((root/'mcp-chat-media').glob('*.bin'))
                    note=data(call('note_chat_sticker',session_id=sid,sticker_id=value['sticker_id'],description='fixture image'))
                    assert note['saved'] and note['resource']=='temporary_source'
                    assert not list((root/'mcp-chat-media').glob('*.bin'))
                    plan=data(call('plan_chat_reply',session_id=sid,batch_id=data(incoming)['input_batch']['id'],
                        action='reply',intent='fixture image',expressions=['sticker'],idempotency_key='temporary-plan'))
                    if plan.get('error_code')=='needs_wait':
                        pending=data(call('wait_chat_messages',**dict(plan['recovery']['next_call']['arguments'],timeout_seconds=3)))
                        plan=data(call('plan_chat_reply',session_id=sid,batch_id=pending['input_batch']['id'],
                            action='reply',intent='fixture image',expressions=['sticker'],idempotency_key='temporary-plan-after-wait'))
                    assert plan['state']=='READY',plan
                    send_args=dict(session_id=sid,plan_id=plan['plan_id'],idempotency_key='temporary-send',
                                   expression_ids=[],bubbles=[dict(kind='sticker',sticker_id=value['sticker_id'])])
                    receipt=data(call('send_chat_reply',**send_args))
                    assert receipt['state']=='SUCCEEDED',receipt
                    assert len(StickerBot.sent)==1 and base64.b64decode(StickerBot.sent[0]['message'][0]['data']['file'][9:])==raw
                    assert data(call('send_chat_reply',**send_args))['state']=='SUCCEEDED' and len(StickerBot.sent)==1
                    assert not list((root/'mcp-chat-media').glob('*.bin'))
                    call('wait_chat_messages',session_id=sid,timeout_seconds=0,acknowledge_through_id=cursor)
                    asyncio.run(wire_pages(service,conn['token'],sid,eid))
                # Automatic delivery must expose both the already delivered
                # pixels and the exact continuation for the remaining tiles.
                (root/'mcp-efficiency.json').write_text('{"enabled":true,"images":"automatic"}','utf-8')
                with patch('chatlocal.mcp_chat_media.download',return_value=raw):
                    eid,cursor,auto=add();packet=data(auto);entry=packet['materials']['images'][0]
                    assert entry['state']=='pixels_partial' and entry['temporary'] and packet['materials']['incomplete']
                    assert len([x for x in auto.content if x.type=='image'])==3
                    assert packet['chat_guidance']['image_reads'][0]==entry['read_call']
                    page=data(call('read_chat_image',**entry['read_call']['arguments']))
                    while page['has_more']:page=data(call('read_chat_image',**page['next_call']['arguments']))
                    assert page['temporary_deleted']
                    call('wait_chat_messages',session_id=sid,timeout_seconds=0,acknowledge_through_id=cursor)
                (root/'mcp-efficiency.json').write_text('{"enabled":true,"images":"on_demand"}','utf-8')
                raw=fixture_image((80,60),5);eid,cursor,_=add()
                with patch('chatlocal.mcp_chat_media.download',return_value=raw):
                    sample=data(call('read_chat_image',session_id=sid,event_id=eid,max_frames=3))
                    assert sample['frame_indices']==[0,2,4] and sample['frame_sampling']=='first_middle_last'
                    full=call('read_chat_image',session_id=sid,event_id=eid,all_frames=True,max_frames=3)
                    first=data(full);assert first['frame_indices']==[0,1,2] and first['has_more']
                    last=data(call('read_chat_image',**first['next_call']['arguments']))
                    assert last['frame_indices']==[3,4] and last['temporary_deleted']
                    (root/'mcp-chat-media-policy.json').write_text('{}','utf-8')
                    explicit=data(call('read_chat_image',session_id=sid,event_id=eid,temporary=True))
                    noted=data(call('note_chat_sticker',session_id=sid,sticker_id=explicit['sticker_id'],description='explicit temporary image'))
                    assert noted['saved'] and noted['resource']=='temporary_source'
                    assert not list((root/'mcp-chat-media').glob('*.bin'))
                    (root/'mcp-chat-media-policy.json').write_text(json.dumps({'temporary_sessions':[sid]}),'utf-8')
                    with patch('chatlocal.mcp_chat_temp_images.TTL',.1):
                        expiring=data(call('read_chat_image',session_id=sid,event_id=eid,all_frames=True,max_frames=1))
                        assert expiring['has_more']
                        eventually(lambda:not tools.chat.media.temporary.jobs)
                        assert not list((root/'mcp-chat-temporary-images').glob('tulpa-*.tmp'))
                    # An abandoned incomplete read is removed by session stop.
                    more=data(call('read_chat_image',session_id=sid,event_id=eid,all_frames=True,max_frames=1))
                    assert more['has_more'] and tools.chat.media.temporary.jobs
                    assert data(call('stop_chat_session',session_id=sid))['event']=='stopped'
                    assert not tools.chat.media.temporary.jobs
                    assert not list((root/'mcp-chat-temporary-images').glob('tulpa-*.tmp'))
                assert len(StickerBot.sent)==1,'Only one deduplicated loopback image send is expected'
        print('PASS: long-image pages and original send, sample/all animation frames, scope, temporary cleanup, explicit notes and bridge previews; loopback only.')
    finally:
        events.close();upstream.shutdown();upstream.server_close()


if __name__=='__main__':main()
