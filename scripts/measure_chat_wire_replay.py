"""Replay delivered reaction histories without printing private chat content.

This estimates one linear wait-state lineage per session. It measures exact
JSON characters in this replay, not provider Token usage or live performance.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chatlocal.mcp_chat_output import reaction_delta


def packets(value,depth=0):
    if depth>16:return
    if isinstance(value,dict):
        if value.get('event') in ('messages','reactions','idle') and 'session_id' in value and 'messages' in value:
            yield value;return
        for key in ('content','contentItems','text','output','results','structuredContent'):
            if key in value:yield from packets(value[key],depth+1)
    elif isinstance(value,list):
        for item in value:yield from packets(item,depth+1)
    elif isinstance(value,str):
        for candidate in (value,value.split('\nOutput:\n',1)[-1]):
            try:parsed=json.loads(candidate)
            except (ValueError,TypeError):continue
            yield from packets(parsed,depth+1);break


def chars(value):return len(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')))


def measure(path):
    seen={};count=original=optimized=old_items=new_items=0
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            try:row=json.loads(line)
            except ValueError:continue
            payload=row.get('payload',{})
            if row.get('type')!='response_item' or payload.get('type') not in ('function_call_output','custom_tool_call_output'):continue
            for packet in packets(payload.get('output')):
                count+=1;sid=packet['session_id'];before=packet.get('chat_guidance',{}).get('reaction_notices',{}).get('items',[])
                after,seen[sid]=reaction_delta(packet,seen.get(sid,{}))
                keep=after.get('chat_guidance',{}).get('reaction_notices',{}).get('items',[])
                original+=chars(before);optimized+=chars(keep);old_items+=len(before);new_items+=len(keep)
    return dict(packets=count,original_reaction_chars=original,optimized_reaction_chars=optimized,
                original_reaction_items=old_items,optimized_reaction_items=new_items,
                model_calls=0,real_qq_writes=0,metric='JSON characters, ideal per-session continuation replay; not Tokens')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('rollout');args=parser.parse_args()
    print(json.dumps(measure(args.rollout)))
