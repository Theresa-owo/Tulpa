"""Deliver live attention ahead of backlog without ACKing unread older input."""
import json
import time


def install(access):
    with access.connect() as db:
        columns={r['name'] for r in db.execute('PRAGMA table_info(chat_inbox)')}
        if 'completed' not in columns:
            db.execute('ALTER TABLE chat_inbox ADD COLUMN completed INTEGER NOT NULL DEFAULT 0')
            db.execute('UPDATE chat_inbox SET completed=1 WHERE id<=(SELECT cursor FROM chat_sessions WHERE id=chat_inbox.session_id)')
        db.executescript('''
            CREATE INDEX IF NOT EXISTS chat_inbox_completion ON chat_inbox(session_id,completed,id);
            CREATE INDEX IF NOT EXISTS chat_inbox_arrival ON chat_inbox(session_id,at);
            CREATE TABLE IF NOT EXISTS chat_attention_batches(
                session_id TEXT NOT NULL,batch_id TEXT PRIMARY KEY,watermark INTEGER NOT NULL,
                ids TEXT NOT NULL,settled INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL,connected_at REAL);
            CREATE INDEX IF NOT EXISTS chat_attention_watermark ON chat_attention_batches(session_id,watermark);
        ''')
        if 'connected_at' not in {r['name'] for r in db.execute('PRAGMA table_info(chat_attention_batches)')}:
            db.execute('ALTER TABLE chat_attention_batches ADD COLUMN connected_at REAL')


def select(chat,row,limit):
    """A priority page is contiguous from its anchor; the old queue is retained."""
    sid=row['id'];cursor=row['cursor'];now=time.time();connection=chat.receiver.status().get('connected_at')
    with chat.access.connect() as db:
        head=db.execute('SELECT * FROM chat_inbox WHERE session_id=? AND id>? AND completed=0 ORDER BY id LIMIT ?',
                        (sid,cursor,limit+1)).fetchall()
        if not head:return head,dict(priority=False,rank=0,reason='idle',backlog_pending=False)
        mention=db.execute('''SELECT i.id FROM chat_inbox i WHERE session_id=? AND id>? AND completed=0 AND at>=?
            AND coalesce(json_extract(i.payload,'$.is_self'),0)=0 AND coalesce(json_extract(i.payload,'$.recalled'),0)=0
            AND EXISTS(SELECT 1 FROM json_each(i.payload,'$.segments') s
                WHERE json_extract(s.value,'$.type')=? AND CAST(json_extract(s.value,'$.qq') AS TEXT)=?)
            ORDER BY i.id DESC LIMIT 1''',(sid,cursor,now-900,'at',row['conversation_id'].split(':')[0])).fetchone()
        anchor=mention['id'] if mention else None;reason='direct_mention' if mention else 'live_topic'
        rank=4 if mention else 3
        if anchor is None:
            fresh=db.execute('SELECT id FROM chat_inbox WHERE session_id=? AND id>? AND completed=0 AND at>=? ORDER BY id LIMIT 1',
                             (sid,cursor,now-120)).fetchone()
            anchor=fresh['id'] if fresh else None
        # A live head already has normal contiguous delivery. Only skip the
        # old queue when the attention anchor is beyond the current page.
        if anchor is None:return head,dict(priority=False,rank=2,reason='backlog',backlog_pending=False)
        if anchor<=head[min(limit,len(head))-1]['id']:
            return head,dict(priority=False,rank=rank,reason=reason,backlog_pending=False)
        chosen=db.execute('SELECT * FROM chat_inbox WHERE session_id=? AND id>=? AND completed=0 ORDER BY id LIMIT ?',
                          (sid,anchor,limit+1)).fetchall()
        return chosen,dict(priority=True,rank=rank,reason=reason,anchor_event_id=anchor,backlog_pending=True,source_connected_at=connection)


def remember(db,row,batch_id,ids,collection):
    if ids and (collection or {}).get('priority'):
        db.execute('INSERT INTO chat_attention_batches(session_id,batch_id,watermark,ids,created,connected_at) VALUES(?,?,?,?,?,?)',
                   (row['id'],batch_id,max(ids),json.dumps(ids),time.time(),collection.get('source_connected_at')))


def acknowledge(db,row,through):
    """Priority ACK settles exact batch IDs, then advances only contiguous work."""
    sid=row['id'];cursor=row['cursor']
    if through<=cursor:return cursor
    attention=db.execute('SELECT * FROM chat_attention_batches WHERE session_id=? AND watermark=?',(sid,through)).fetchall()
    if attention:
        valid=[]
        for batch in attention:
            terminal=db.execute("SELECT 1 FROM chat_turn_plans WHERE session_id=? AND state IN ('SUCCEEDED','SILENT') AND json_extract(payload,'$.batch_id')=?",
                                (sid,batch['batch_id'])).fetchone()
            if batch['settled'] or terminal:valid.append(batch)
        if not valid:raise ValueError('优先消息尚未完成规划或发送，不能确认；未推进旧消息游标。')
        for batch in valid:
            db.executemany('UPDATE chat_inbox SET completed=1 WHERE session_id=? AND id=? AND delivered=1',
                           [(sid,eid) for eid in json.loads(batch['ids'])])
            db.execute('UPDATE chat_attention_batches SET settled=1 WHERE batch_id=?',(batch['batch_id'],))
    else:
        # Legacy ACKs still work, but an offered priority watermark can never
        # turn an unread hole into processed input.
        db.execute('UPDATE chat_inbox SET completed=1 WHERE session_id=? AND id>? AND id<=? AND delivered=1',(sid,cursor,through))
    return advance(db,row)


def advance(db,row):
    sid=row['id'];cursor=row['cursor']
    missing=db.execute('SELECT min(id) FROM chat_inbox WHERE session_id=? AND id>? AND completed=0',(sid,cursor)).fetchone()[0]
    if missing is None:
        done=db.execute('SELECT max(id) FROM chat_inbox WHERE session_id=? AND completed=1',(sid,)).fetchone()[0]
    else:
        done=db.execute('SELECT max(id) FROM chat_inbox WHERE session_id=? AND id<? AND completed=1',(sid,missing)).fetchone()[0]
    cursor=max(cursor,done or cursor)
    db.execute('DELETE FROM chat_attention_batches WHERE session_id=? AND watermark<=?',(sid,cursor))
    return cursor
