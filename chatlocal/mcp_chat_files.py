"""Scoped, streaming original QQ group files; never parses or executes bytes."""
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import threading
import time
import uuid
from urllib.parse import urljoin

from .native_file_transfer import checked_directory, workspace

MAX_FILE_BYTES = 1024 * 1024 * 1024
FILE_TOOLS = {'list_chat_files', 'download_chat_file'}
SLOTS = threading.BoundedSemaphore(2)


def file_limit():
    from .config import ROOT
    from dotenv import dotenv_values
    value = os.environ.get('TULPA_CHAT_FILE_MAX_BYTES') or dotenv_values(ROOT/'.env').get('TULPA_CHAT_FILE_MAX_BYTES') or str(MAX_FILE_BYTES)
    try: limit = int(value)
    except (ValueError, TypeError): raise ValueError('原文件资源上限配置无效。') from None
    if not 1 <= limit <= MAX_FILE_BYTES: raise ValueError('原文件上限须介于 1 字节和 1 GiB。')
    return limit


def stream_download(url, target, cancel, limit, expected, check):
    """256 KiB chunks, public pinned addresses, exact declared length and SHA256."""
    from .mcp_chat_media import remote_target, public_addresses
    deadline = time.monotonic()+1200
    last_check = 0
    try:
        for _ in range(5):
            # This URL is obtained only from the verified group's file API.
            parts = remote_target(url, trusted_file=True)
            port = parts.port or (443 if parts.scheme == 'https' else 80)
            records = public_addresses(parts.hostname, port, deadline=deadline)
            check()
            if cancel.is_set() or time.monotonic() >= deadline: raise ValueError('原文件下载已取消或超时。')
            family, kind, proto, _, address = records[0]
            sock = socket.socket(family, kind, proto)
            conn = http.client.HTTPConnection(parts.hostname, port, timeout=10)
            try:
                sock.settimeout(min(10, max(.1, deadline-time.monotonic())))
                sock.connect(address)
                if parts.scheme == 'https': sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
                conn.sock = sock
                path = parts.path or '/'
                if parts.query: path += '?'+parts.query
                conn.request('GET', path, headers={'User-Agent':'Tulpa/0.5', 'Accept':'application/octet-stream', 'Accept-Encoding':'identity'})
                response = conn.getresponse()
                if response.status in (301,302,303,307,308):
                    url = urljoin(url, response.getheader('Location') or '')
                    continue
                if response.status != 200: raise ValueError('QQ 原文件下载失败或已过期。')
                length = response.getheader('Content-Length')
                if length and (int(length) > limit or int(length) != expected): raise ValueError('原文件长度与授权目录不一致或超过资源上限。')
                digest, size = hashlib.sha256(), 0
                with target.open('xb') as out:
                    while True:
                        if cancel.is_set() or time.monotonic() >= deadline: raise ValueError('原文件下载已取消或超时。')
                        if time.monotonic()-last_check > 5:
                            check()
                            last_check = time.monotonic()
                        chunk = response.read1(256*1024)
                        if not chunk: break
                        size += len(chunk)
                        if size > limit or size > expected: raise ValueError('原文件超过资源上限或声明长度。')
                        out.write(chunk)
                        digest.update(chunk)
                if size != expected: raise ValueError('原文件下载不完整，未保存。')
                check()
                return size, digest.hexdigest()
            finally:
                conn.close()
                sock.close()
        raise ValueError('原文件重定向次数过多。')
    except (OSError, http.client.HTTPException):
        raise ValueError('原文件下载连接失败；未保存不完整文件。') from None


def schemas(tool):
    sid = dict(session_id=dict(type='string', minLength=32, maxLength=32))
    return [
        tool('list_chat_files', '列出当前授权实时会话的 QQ 群文件原始元数据；返回 file_handle、真实 file_id、原名、大小和上传时间。按授权日期过滤，不解析文件。',
             dict(**sid, offset=dict(type='integer', minimum=0, default=0)), ['session_id']),
        tool('download_chat_file', '将授权群文件原始字节流式保存至本机工作区 downloads/qq-files，返回绝对路径、原名、大小、SHA256及来源。默认最多1 GiB，可由本机配置调低。用 file_handle 或 event_id 和 file_index 定位；不接受指定路径/URL，不解析或执行附件。',
             dict(**sid, file_handle=dict(type='string', minLength=32, maxLength=32), event_id=dict(type='integer', minimum=1),
                  file_index=dict(type='integer', minimum=0, maximum=99, default=0)), ['session_id']),
    ]


def descriptor(data):
    if not isinstance(data, dict): return None
    fid = str(data.get('file_id') or data.get('id') or '')
    name = str(data.get('file_name') or data.get('filename') or data.get('name') or '')
    size = data.get('file_size', data.get('size'))
    if not fid or len(fid) > 300 or not name or len(name) > 300 or type(size) is not int or size < 0: return None
    return dict(file_id=fid, filename=name, size=size, busid=data.get('busid', 102))


def in_date(access, grant, stamp):
    plan = access.plan(grant)
    if type(stamp) not in (int, float) or stamp <= 0: return plan.start is None and plan.end is None
    return (plan.start is None or stamp*1000 >= plan.start) and (plan.end is None or stamp*1000 < plan.end)


class ChatFiles:
    def __init__(self, media):
        self.media, self.chat, self.access = media, media.chat, media.access
        with self.access.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS chat_file_refs(handle TEXT PRIMARY KEY, session_id TEXT NOT NULL, descriptor TEXT NOT NULL, updated REAL NOT NULL)')

    def guard(self, grant, sid, cancel):
        current, row = self.chat.validate(sid, grant['id'])
        if not current['scope'].get('chat_files'): raise ValueError('此连接未授权原文件下载。')
        if row['conversation_id'].split(':')[1] != 'group': raise ValueError('原生群文件目录仅支持授权群会话。')
        if cancel.is_set(): raise ValueError('文件读取已取消。')
        self.media.client(row)
        return current, row

    def remember(self, row, item):
        handle = hashlib.sha256((row['id']+'\0'+json.dumps(item, sort_keys=True)).encode()).hexdigest()[:32]
        with self.access.connect() as db:
            db.execute('INSERT OR REPLACE INTO chat_file_refs VALUES(?,?,?,?)', (handle,row['id'],json.dumps(item),time.time()))
            db.execute('DELETE FROM chat_file_refs WHERE rowid IN (SELECT rowid FROM chat_file_refs ORDER BY updated DESC LIMIT -1 OFFSET 10000)')
        return dict(file_handle=handle, **item)

    def catalog(self, grant, row, cancel):
        client = self.media.client(row)
        group = int(row['conversation_id'].split(':')[-1])
        folders, visited, items, seen = [''], set(), [], set()
        start = time.monotonic()
        while folders:
            self.guard(grant, row['id'], cancel)
            folder = folders.pop(0)
            if folder in visited: raise ValueError('群文件目录存在循环。')
            if len(visited) >= 300 or time.monotonic()-start > 90: raise ValueError('群文件目录超过预算。')
            visited.add(folder)
            args = dict(group_id=group, file_count=10000)
            if folder: args['folder_id'] = folder
            result = client.call('get_group_files_by_folder' if folder else 'get_group_root_files', args)
            if not isinstance(result, dict) or not isinstance(result.get('files'), list) or not isinstance(result.get('folders'), list): raise ValueError('群文件目录格式不兼容。')
            for entry in result['files']:
                item = descriptor(entry)
                if not item or not in_date(self.access, grant, entry.get('upload_time')) or item['file_id'] in seen: continue
                seen.add(item['file_id'])
                item.update(upload_time=entry.get('upload_time'), source='qq_group_file', conversation_id=row['conversation_id'])
                items.append(item)
                if len(items) > 10000: raise ValueError('群文件目录过大。')
            for entry in result['folders']:
                if not isinstance(entry, dict) or not isinstance(entry.get('folder_id'), str) or not entry['folder_id']: raise ValueError('群文件夹编号无效。')
                folders.append(entry['folder_id'])
        return items

    def event_file(self, row, args):
        with self.access.connect() as db:
            event = db.execute('SELECT payload,attachments FROM chat_inbox WHERE id=? AND session_id=?', (args['event_id'],row['id'])).fetchone()
        if not event: raise ValueError('文件事件不属于当前会话或已经过期。')
        payload = json.loads(event['payload'])
        if payload.get('recalled'): raise ValueError('原文件消息已撤回。')
        idx = args.get('file_index', 0)
        files = [r for r in json.loads(event['attachments']) if r.get('kind') == 'file']
        observed = descriptor(files[idx]) if idx < len(files) else None
        # New upstream attachments intentionally omit size; recover it from the
        # verified native message, also accepting pre-upgrade cached events.
        if not observed:
            mid = payload['onebot_message_id']
            if not str(mid).lstrip('-').isascii() or not str(mid).lstrip('-').isdigit(): raise ValueError('事件没有真实 QQ 消息编号。')
            msg = self.media.client(row).call('get_msg', {'message_id':int(mid)})
            account, _, peer = row['conversation_id'].split(':')
            sender = msg.get('sender') if isinstance(msg, dict) and isinstance(msg.get('sender'), dict) else {}
            if (not isinstance(msg, dict) or str(msg.get('message_id')) != str(mid) or msg.get('message_type') != 'group' or
                    str(msg.get('group_id')) != peer or ('self_id' in msg and str(msg['self_id']) != account) or
                    str(msg.get('user_id', sender.get('user_id'))) != payload['sender_id'] or
                    any(msg.get(key) for key in ('recalled','is_recalled','deleted','is_deleted')) or
                    type(msg.get('time')) not in (int,float) or abs(msg['time']*1000-payload['timestamp']) > 2000):
                raise ValueError('QQ 原消息与授权实时事件身份不一致。')
            parts = [s.get('data', {}) for s in msg.get('message', []) if isinstance(s, dict) and s.get('type') == 'file']
            observed = descriptor(parts[idx]) if idx < len(parts) else None
        if not observed: raise ValueError('该事件没有可定位的原文件。')
        return dict(observed, upload_time=payload['timestamp']/1000, source='qq_live_file', conversation_id=row['conversation_id'],
                    event_id=args['event_id'], onebot_message_id=payload['onebot_message_id'])

    def call(self, grant, name, args, cancel):
        if not SLOTS.acquire(blocking=False): raise ValueError('原文件下载通道繁忙。')
        try: return self._call(grant, name, args, cancel)
        finally: SLOTS.release()

    def _call(self, grant, name, args, cancel):
        grant, row = self.guard(grant, args['session_id'], cancel)
        if name == 'list_chat_files':
            items = self.catalog(grant, row, cancel)
            offset = args.get('offset', 0)
            rows = [self.remember(row, item) for item in items[offset:offset+50]]
            self.guard(grant, row['id'], cancel)
            return dict(items=rows, has_more=offset+len(rows)<len(items), next_offset=offset+len(rows), source='current_qq_group_file_directory', date_filtered=True), []
        if name != 'download_chat_file': raise ValueError('未知原文件操作。')
        if bool(args.get('file_handle')) == bool(args.get('event_id')): raise ValueError('只选择 file_handle 或 event_id 一种定位方式。')
        if args.get('event_id'):
            item = self.event_file(row, args)
        else:
            with self.access.connect() as db:
                ref = db.execute('SELECT descriptor FROM chat_file_refs WHERE handle=? AND session_id=?', (args['file_handle'],row['id'])).fetchone()
            if not ref: raise ValueError('文件编号不属于当前会话，请先 list_chat_files。')
            item = json.loads(ref['descriptor'])
        if not in_date(self.access, grant, item.get('upload_time')): raise ValueError('文件不在授权日期内。')
        limit = file_limit()
        if item['size'] > limit: raise ValueError('原文件超过本机配置的单文件资源上限。')
        actual = next((entry for entry in self.catalog(grant,row,cancel) if entry['file_id'] == item['file_id']), None)
        if not actual: raise ValueError('文件已删除、过期或不在当前授权目录内。')
        if any(actual[key] != item[key] for key in ('size','filename','busid')): raise ValueError('文件元数据已变化，请重新查询。')
        result = self.media.client(row).call('get_group_file_url', dict(group_id=int(row['conversation_id'].split(':')[-1]),file_id=item['file_id'],busid=actual['busid']))
        url = result.get('url') if isinstance(result, dict) else None
        if not isinstance(url, str): raise ValueError('OneBot 未提供原文件下载地址。')
        self.guard(grant, row['id'], cancel)
        destination = checked_directory(workspace(self.access.path.parent)/'downloads'/'qq-files', create=True)
        safe = re.sub(r'[^\w. -]', '_', item['filename']).strip(' .')[:100] or 'attachment.bin'
        temp = destination/(uuid.uuid4().hex+'.partial')
        def check():
            self.guard(grant,row['id'],cancel)
            if args.get('event_id') and self.event_file(row,args) != item: raise ValueError('文件所属实时消息已变化。')
        try:
            size, digest = stream_download(url,temp,cancel,limit,item['size'],check)
            target = destination/(digest[:16]+'-'+safe)
            check()
            checked_directory(destination)
            os.replace(temp,target)
            check()
        finally: temp.unlink(missing_ok=True)
        return dict(local_path=str(target.resolve()), original_filename=item['filename'], size=size, sha256=digest, source=item,
                    downloaded=True, parsed=False, streamed=True, resource_limit_bytes=limit,
                    note='原始文件已保存；由宿主使用本机文件工具读取分析，不执行附件。'), []
