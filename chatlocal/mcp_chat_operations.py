"""Explicit live local-file sending and mute/unmute, with durable receipts."""
import hashlib
import os
import time
import uuid

from .mcp_actions import dump, _execution
from .message_sender import SendError, SendUncertain
from .onebot import OneBotError, OneBotUncertain
from .native_file_transfer import checked_directory, source_file, workspace

FLAGS = {'send_chat_file':'chat_file_send', 'moderate_chat_member':'chat_moderate'}


def schemas(tool, scope):
    sid = dict(session_id=dict(type='string',minLength=32,maxLength=32))
    key = dict(idempotency_key=dict(type='string',minLength=8,maxLength=80))
    rows = [
        tool('send_chat_file', '向当前会话固定的授权群发送本机工作区文件。仅接受 outgoing-files 或 downloads/qq-files 中的文件名，不接受任意路径/URL。保存原字节 SHA256 和幂等回执；UNKNOWN 不重发。',
             dict(**sid,**key,filename=dict(type='string',minLength=1,maxLength=180),source=dict(type='string',enum=['outgoing','download'],default='outgoing')), ['session_id','filename','idempotency_key']),
        tool('moderate_chat_member', '在当前授权群禁言或解禁指定成员。仅支持 mute/unmute，每次核对登录账号、管理员/群主身份和目标角色。UNKNOWN 不重发。',
             dict(**sid,**key,action=dict(type='string',enum=['mute','unmute']),user_id=dict(type='string',pattern='^[0-9]{1,16}$'),duration_seconds=dict(type='integer',minimum=1,maximum=2592000)), ['session_id','action','user_id','idempotency_key']),
    ]
    return [row for row in rows if scope.get(FLAGS[row['name']])]


class ChatOperations:
    def __init__(self, media):
        self.media, self.chat, self.access, self.actions = media, media.chat, media.access, media.actions

    def guard(self, grant, sid, name, cancel):
        current, row = self.chat.validate(sid,grant['id'])
        if not current['scope'].get(FLAGS[name]): raise ValueError('当前连接未授权该操作。')
        if row['conversation_id'].split(':')[1] != 'group': raise ValueError('原生文件发送和禁言仅支持授权群会话。')
        if cancel.is_set(): raise ValueError('操作已取消。')
        self.media.client(row)
        return current, row

    def call(self, grant, name, args, cancel):
        current, row = self.guard(grant,args['session_id'],name,cancel)
        check = lambda: self.guard(grant,row['id'],name,cancel)
        if name == 'moderate_chat_member':
            if args.get('action') not in ('mute','unmute'): raise ValueError('只支持禁言或解禁。')
            def target(g,cid,kind):
                check()
                if not g['scope'].get('chat_moderate'): raise ValueError('未授权禁言/解禁。')
                return self.chat.live_target(g,cid,kind)
            params = dict(conversation_id=row['conversation_id'],idempotency_key=args['idempotency_key'],action=args['action'],user_id=args['user_id'])
            if args['action'] == 'mute':
                if 'duration_seconds' not in args: raise ValueError('禁言须指定秒数。')
                params['duration_seconds'] = args['duration_seconds']
            elif 'duration_seconds' in args: raise ValueError('解禁不需要 duration_seconds。')
            return self.actions.execute(current,'manage',params,cancel,guard=check,target_resolver=target), []
        root = workspace(self.access.path.parent)
        source = args.get('source','outgoing')
        if source not in ('outgoing','download'): raise ValueError('未知发送文件来源。')
        filename = args['filename']
        src = source_file(root/'outgoing-files' if source == 'outgoing' else root/'downloads'/'qq-files',filename)
        from .mcp_chat_files import file_limit
        limit = file_limit()
        cache = checked_directory(self.access.path.parent/'outgoing-file-cache',create=True)
        staged = cache/(uuid.uuid4().hex+'.bin')
        size, digest, dispatched, state = 0, hashlib.sha256(), False, None
        try:
            with src.open('rb') as inp, staged.open('xb') as out:
                before = os.fstat(inp.fileno())
                if before.st_nlink != 1 or before.st_size > limit: raise ValueError('文件是硬链接或超过资源上限。')
                while True:
                    block = inp.read(256*1024)
                    if not block: break
                    if cancel.is_set(): raise ValueError('文件暂存已取消。')
                    size += len(block)
                    if size > limit: raise ValueError('文件超过资源上限。')
                    digest.update(block)
                    out.write(block)
                after = os.fstat(inp.fileno())
                if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns): raise ValueError('文件正在变化，未发送。')
            sha = digest.hexdigest()
            key = 'file:'+hashlib.sha256((row['id']+'\0'+args['idempotency_key']).encode()).hexdigest()
            signature = hashlib.sha256(dump([row['id'],source,filename,size,sha]).encode()).hexdigest()
            with _execution:
                check()
                self.access.ensure_revision(current)
                with self.access.connect() as db:
                    old = db.execute('SELECT * FROM operations WHERE grant_id=? AND request_key=?',(grant['id'],key)).fetchone()
                if old:
                    if old['signature'] != signature: raise ValueError('同一个幂等编号不能用于不同文件。')
                    return self.actions.get(old['id'],grant['id']), []
                sender = self.actions.sender_factory()
                sender.check_target(self.chat.live_target(current,row['conversation_id']))
                oid, now = uuid.uuid4().hex, time.time()
                with self.access.connect() as db:
                    db.execute('INSERT INTO operations(id,grant_id,request_key,signature,kind,conversation_id,params,target,summary,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (oid,grant['id'],key,signature,name,row['conversation_id'],dump(dict(filename=filename,size=size,sha256=sha)),'{}','发送文件至 '+row['name'],'EXECUTING',now,now))
                    db.execute('INSERT INTO operation_events(operation_id,state,at) VALUES(?,?,?)',(oid,'EXECUTING',now))
                try:
                    check()
                    self.access.ensure_revision(current)
                    checked_directory(cache)
                    dispatched = True
                    receipt = sender.call('send_group_msg',dict(group_id=int(row['conversation_id'].split(':')[-1]),message=[dict(type='file',data=dict(file=staged.resolve().as_uri(),name=filename))]),sending=True,timeout_seconds=1200)
                    mid = receipt.get('message_id') if isinstance(receipt,dict) else None
                    if type(mid) is not int or not mid: raise SendUncertain('未收到确定消息编号。')
                    state, result = 'SUCCEEDED', dict(message_id=str(mid),filename=filename,size=size,sha256=sha)
                except (SendUncertain,OneBotUncertain): state,result = 'UNKNOWN',dict(note='文件发送结果未知，请核对 QQ，禁止换编号重发。')
                except (ValueError,SendError,OneBotError) as exc: state,result = 'FAILED',dict(note=str(exc))
                except Exception: state,result = ('UNKNOWN' if dispatched else 'FAILED'),dict(note='文件发送中断，请核对 QQ。')
                with self.access.connect() as db: self.actions.transition(db,oid,state,result)
                return self.actions.get(oid,grant['id']), []
        finally:
            if state != 'UNKNOWN': staged.unlink(missing_ok=True)
