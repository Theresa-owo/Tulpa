"""Persistent local send fence for the external chat harness; no inference."""
import json
import re
import time
from .platform_lock import file_lock
from .snowluma_secure import atomic_bytes


def state(access):
    path=access.path.parent/'mcp-chat-runtime.json'
    if not path.exists():return dict(paused=False,session_ids=[],reason='')
    value=json.loads(path.read_text('utf-8-sig'))
    if (not isinstance(value,dict) or type(value.get('paused')) is not bool or not isinstance(value.get('session_ids'),list)
        or len(value['session_ids'])>8 or any(not isinstance(s,str) or not re.fullmatch('[a-f0-9]{32}',s) for s in value['session_ids'])):
        raise ValueError('聊天运行器控制配置无效。')
    return value


def blocked(access,sid):
    try:current=state(access)
    except (OSError,ValueError):return True
    return sid in current['session_ids'] and (current['paused'] or current.get('lease_until',0)<time.time())


def configure(access,body):
    if not isinstance(body,dict) or set(body)-{'paused','session_ids','reason','lease_id','lease_seconds','renew'} or type(body.get('paused')) is not bool:raise ValueError('聊天运行器设置无效。')
    ids=body.get('session_ids');reason=body.get('reason','')
    lease=body.get('lease_id');seconds=body.get('lease_seconds',20);renew=body.get('renew',False)
    if (not isinstance(ids,list) or not 1<=len(ids)<=8 or len(set(ids))!=len(ids)
        or any(not isinstance(s,str) or not re.fullmatch('[a-f0-9]{32}',s) for s in ids)
        or not isinstance(reason,str) or len(reason)>160):raise ValueError('聊天运行器范围无效。')
    if not isinstance(lease,str) or not re.fullmatch('[a-f0-9]{32}',lease) or type(seconds) is not int or not 5<=seconds<=60 or type(renew) is not bool:raise ValueError('聊天运行器租约无效。')
    with access.connect() as db:
        rows=db.execute('SELECT id FROM chat_sessions WHERE id IN ('+','.join('?' for _ in ids)+')',ids).fetchall()
    if len(rows)!=len(ids):raise ValueError('会话不存在，未改变运行器状态。')
    with file_lock(access.path.parent/'mcp-chat-runtime.lock',blocking=True):
        old=state(access)
        if not (body['paused'] and reason=='initializing') and old.get('lease_id')!=lease:raise ValueError('运行器租约已被替换。')
        if renew and (old['paused'] or old['session_ids']!=ids):raise ValueError('已暂停，不能用续租重新开放发送。')
        value=dict(paused=body['paused'],session_ids=ids,reason=reason,lease_id=lease,lease_until=0 if body['paused'] else time.time()+seconds)
        atomic_bytes(access.path.parent/'mcp-chat-runtime.json',json.dumps(value).encode())
    return value
