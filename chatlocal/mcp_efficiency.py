"""Operator-owned token policy; never changes grants, models or chat decisions."""
import json
from pathlib import Path

DEFAULTS=dict(enabled=False,images='on_demand',learning_hourly_calls=8,learning_interval_seconds=300)


def policy(access):
    if not getattr(access,'path',None):return dict(DEFAULTS)
    path=Path(access.path).parent/'mcp-efficiency.json'
    if not path.exists():return dict(DEFAULTS)
    value=json.loads(path.read_text('utf-8-sig'))
    if not isinstance(value,dict) or set(value)-set(DEFAULTS) or type(value.get('enabled',False)) is not bool:raise ValueError('Token 配置无效。')
    cfg=DEFAULTS|value
    if cfg['images'] not in ('on_demand','automatic'):raise ValueError('图片交付配置无效。')
    for key,low,high in (('learning_hourly_calls',1,100),('learning_interval_seconds',0,3600)):
        if type(cfg[key]) is not int or not low<=cfg[key]<=high:raise ValueError('学习预算配置无效。')
    return cfg


def wire_result(access,result):
    # Text JSON is the universal MCP fallback; keep all fields and image indexes
    # without serializing the same data again in structuredContent.
    if policy(access)['enabled'] and getattr(result,'structuredContent',None) is not None:
        return result.model_copy(update={'structuredContent':None})
    return result


def compact_dynamic(data):
    """Compact repetitive dynamic hints; never touch source text or failures."""
    result=dict(data)
    for key in ('chat_guidance','chat_prompt'):
        if key not in result:continue
        value=dict(result[key]);p=value.get('request_policy')
        if p:value['request_policy']={k:v for k,v in p.items() if k in ('owner_qq','owner_verification_enabled','identity_rule','non_owner_complex_requests','complexity_reference','prefix_required')}
        for empty in ('image_reads','familiar_stickers'):
            if not value.get(empty):value.pop(empty,None)
        if not any(value.get('reaction_notices',{}).values()):value.pop('reaction_notices',None)
        result[key]=value
    if result.get('results'):result['results']=[compact_dynamic(v) for v in result['results']]
    if result.get('state') in ('SUCCEEDED','SILENT'):
        for key in ('learned_language','expression_selection','quote_guidance','next'):result.pop(key,None)
        if 'bubbles' in result:
            bubbles=[]
            for b in result['bubbles']:
                item={k:v for k,v in b.items() if k in ('index','state')}
                r=b.get('receipt')
                if r:item['receipt']={k:v for k,v in r.items() if k in ('id','state','kind','session_id')}
                bubbles.append(item)
            result['bubbles']=bubbles
    return result
