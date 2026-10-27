"""Read-only native handoff tools; watch may CLOSE an existing scoped lease.

No command starts a model, creates a QQ session, opens sending, or changes roles.
The parent coordinates actual native agents with its own collaboration tools.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chatlocal.native_context import ContextBoundary,checkpoint,evidence_page,verify_replacement,encoded,read_checkpoint
from chatlocal.snowluma_secure import atomic_bytes


def shared_log(path):
    if os.name!='nt':return open(path,'rb')
    import msvcrt
    from ctypes import wintypes
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    api.CreateFileW.restype=wintypes.HANDLE
    handle=api.CreateFileW(str(Path(path).resolve()),0x80000000,7,None,3,0,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    return os.fdopen(msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY),'rb')


def close_lease(root,scope,expected_lease):
    from chatlocal.cli_runtime import Client
    current=json.loads((Path(root)/'data/mcp-chat-runtime.json').read_bytes())
    if set(current['session_ids'])!=set(scope) or current['lease_id']!=expected_lease:
        raise ValueError('Current lease changed; do not stop another task/generation')
    return Client(Path(root),timeout=5).request('PUT','/api/mcp/chat-runtime',dict(
        paused=True,session_ids=current['session_ids'],lease_id=current['lease_id'],reason='native_context_boundary'))


def observe(args):
    boundary=ContextBoundary(args.thread_id,previous_total=args.previous_total,
        total_limit=args.total_limit,context_limit=args.context_limit)
    scope=lease=None
    if args.command=='watch' and args.control_root:
        scope=[s['id'] for s in json.loads(Path(args.bootstrap).read_bytes())['sessions']]
        current=json.loads((Path(args.control_root)/'data/mcp-chat-runtime.json').read_bytes())
        if set(current['session_ids'])!=set(scope):raise ValueError('Watch scope does not own current lease')
        lease=current['lease_id']
    try:
        with shared_log(args.rollout) as stream:
            while True:
                status=boundary.feed(stream.read());status['updated_at']=time.time()
                if args.command=='watch' and status['should_stop']:
                    if lease:
                        close_lease(args.control_root,scope,lease);status['sending_closed']=True
                # The parent must await/interrupt ONLY this native agent before
                # checkpointing; a log event alone is not a live agent check.
                    status['parent_action']='await_old_native_exit_then_checkpoint_and_spawn_fresh_without_history'
                if args.state:atomic_bytes(Path(args.state),encoded(status))
                if args.command!='watch' or status['should_stop']:
                    print(json.dumps(status));return
                time.sleep(.5)
    except Exception:
        if lease:close_lease(args.control_root,scope,lease)
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    for name in ('status','watch'):
        q=sub.add_parser(name);q.add_argument('--rollout',required=True);q.add_argument('--thread-id',required=True)
        q.add_argument('--previous-total',type=int,default=0);q.add_argument('--total-limit',type=int,default=300000000)
        q.add_argument('--context-limit',type=int,default=90000);q.add_argument('--state')
        q.add_argument('--control-root');q.add_argument('--bootstrap')
    q=sub.add_parser('checkpoint');q.add_argument('--folder',required=True);q.add_argument('--bootstrap',required=True)
    q.add_argument('--sources',required=True,help='JSON file containing [{thread_id,path}, ...]');q.add_argument('--notes')
    q.add_argument('--previous-checkpoint')
    q=sub.add_parser('render');q.add_argument('--folder',required=True)
    q=sub.add_parser('evidence');q.add_argument('--folder',required=True);q.add_argument('--offset',type=int,default=0)
    q.add_argument('--limit',type=int,default=4);q.add_argument('--query')
    q=sub.add_parser('verify');q.add_argument('--folder',required=True);q.add_argument('--thread-id',required=True)
    q.add_argument('--rollout',required=True);q.add_argument('--static-versions',required=True,nargs='+')
    q.add_argument('--context-limit',type=int,default=90000)
    q.add_argument('--current-bootstrap',required=True,help='Fresh read-only __bootstrap result; validate owner/persona/session identity')
    a=p.parse_args()
    if a.command in ('status','watch'):
        if a.control_root and not a.bootstrap:p.error('--control-root requires --bootstrap for exact original scope')
        observe(a)
    elif a.command=='checkpoint':
        value=checkpoint(a.folder,json.loads(Path(a.bootstrap).read_bytes()),json.loads(Path(a.sources).read_bytes()),
                         notes=json.loads(Path(a.notes).read_bytes()) if a.notes else None,previous_checkpoint=a.previous_checkpoint)
        print(json.dumps(dict(checkpoint=str(Path(a.folder).resolve()),**value)))
    elif a.command=='render':print(encoded(read_checkpoint(a.folder)[1]).decode())
    elif a.command=='evidence':print(json.dumps(evidence_page(a.folder,offset=a.offset,limit=a.limit,query=a.query),ensure_ascii=False))
    else:print(json.dumps(verify_replacement(a.folder,a.thread_id,a.rollout,a.static_versions,context_limit=a.context_limit,
                                          current_bootstrap=json.loads(Path(a.current_bootstrap).read_bytes()))))


if __name__=='__main__':main()
