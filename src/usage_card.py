#!/usr/bin/env python3
"""Local AI usage logs → aggregate JSON and GitHub-compatible SVGs (stdlib only)."""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
import socket
import sys
from zoneinfo import ZoneInfo

FIELDS = ('input', 'output', 'cache_read', 'cache_write')
DEVICE_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')


def count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def date_of(timestamp, tz):
    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timestamp must include a timezone')
    return dt.astimezone(tz).date().isoformat()


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def tokens(usage, tool):
    cached = count(usage.get('cached_input_tokens' if tool == 'Codex' else 'cache_read_input_tokens'))
    written = count(usage.get('cache_write_input_tokens' if tool == 'Codex' else 'cache_creation_input_tokens'))
    incoming = count(usage.get('input_tokens'))
    # Codex input includes cached input; Claude input excludes cache reads/writes.
    return {'input': max(0, incoming - cached - written) if tool == 'Codex' else incoming,
            'output': count(usage.get('output_tokens')), 'cache_read': cached, 'cache_write': written}


def read_events(path, warnings):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    yield item
            except (ValueError, UnicodeError):
                warnings['malformed_lines'] += 1


def collect(roots, tz, warnings):
    records = {}
    for tool, directories in roots.items():
        files = sorted({p for root in directories if root.exists() for p in root.rglob('*.jsonl')})
        warnings[tool + '_files'] = len(files)
        for path in files:
            session, model = path.stem, 'unknown'
            previous = {key: 0 for key in FIELDS}
            try:
                for event in read_events(path, warnings):
                    payload = event.get('payload') or {}
                    if not isinstance(payload, dict):
                        continue
                    if tool == 'Codex':
                        if event.get('type') == 'session_meta':
                            session = payload.get('id') or payload.get('session_id') or session
                        if event.get('type') == 'turn_context':
                            model = payload.get('model') or model
                        if event.get('type') != 'event_msg' or payload.get('type') != 'token_count':
                            continue
                        info = payload.get('info') or {}
                        usage = info.get('total_token_usage')
                        if not isinstance(usage, dict):
                            continue
                        current = tokens(usage, tool)
                        # Difference raw cumulative input, then remove cache from that delta.
                        current['input'] = count(usage.get('input_tokens'))
                        if any(current[key] < previous[key] for key in FIELDS):
                            warnings['codex_counter_decreases'] += 1
                        delta = {key: max(0, current[key] - previous[key]) for key in FIELDS}
                        previous = {key: max(previous[key], current[key]) for key in FIELDS}
                        delta['input'] = max(0, delta['input'] - delta['cache_read'] - delta['cache_write'])
                        if not sum(delta.values()):
                            continue
                        key = identity(tool, session, current)
                        values = delta
                    else:
                        msg = event.get('message') or {}
                        if event.get('type') != 'assistant' or not isinstance(msg.get('usage'), dict):
                            continue
                        message_id = msg.get('id') or event.get('uuid')
                        if not message_id:
                            warnings['missing_identifiers'] += 1
                            continue
                        key = identity(tool, event.get('sessionId', path.stem), event.get('requestId'), message_id)
                        model = msg.get('model') or 'unknown'
                        values = tokens(msg['usage'], tool)
                    try:
                        day = date_of(event.get('timestamp', ''), tz)
                    except (ValueError, TypeError, AttributeError):
                        warnings['invalid_timestamps'] += 1
                        continue
                    record = {'date': day, 'tool': tool, 'model': str(model), **values}
                    if key in records:
                        record = {**records[key], **{k: max(records[key][k], record[k]) for k in FIELDS}}
                    records[key] = record
            except (OSError, UnicodeError):
                # A partial collection must not silently replace the persisted ledger.
                raise RuntimeError(f'Could not read a {tool} log file') from None
    return records


def collect_gemini(root, tz, warnings):
    records = {}
    files = sorted(set(root.glob('*/chats/session-*.json')) | set(root.glob('*/chats/session-*.jsonl')))
    warnings['Gemini CLI_files'] = len(files)
    for path in files:
        raw = path.read_text(encoding='utf-8')
        try:
            items = [json.loads(raw)]
        except ValueError:
            items = list(read_events(path, warnings))
        session = path.stem
        for item in items:
            session = item.get('sessionId', session)
            messages = item.get('messages', item.get('$set', {}).get('messages', [item]))
            for msg in messages:
                usage = msg.get('tokens')
                if msg.get('type') != 'gemini' or not isinstance(usage, dict) or not msg.get('id'):
                    continue
                try:
                    day = date_of(msg['timestamp'], tz)
                except (ValueError, KeyError, TypeError):
                    warnings['invalid_timestamps'] += 1
                    continue
                cached = count(usage.get('cached'))
                record = {'date': day, 'tool': 'Gemini CLI', 'model': str(msg.get('model') or 'unknown'),
                          'input': max(0, count(usage.get('input')) - cached) + count(usage.get('tool')),
                          'output': count(usage.get('output')) + count(usage.get('thoughts')),
                          'cache_read': cached, 'cache_write': 0}
                key = identity('Gemini CLI', session, msg['id'])
                records = merge(records, {key: record})
    return records


def import_records(paths, tz):
    """Strict tool-independent format. Counts must be disjoint, not cumulative."""
    records = {}
    for path in paths:
        document = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(document, dict) or document.get('version') != 1 or not isinstance(document.get('records'), list):
            raise ValueError('Import requires version: 1 and records: []')
        for row in document['records']:
            if not isinstance(row, dict):
                raise ValueError('Each imported record must be an object')
            for key in ('source', 'id', 'tool', 'model', 'timestamp'):
                if not isinstance(row.get(key), str) or not row[key].strip():
                    raise ValueError(f'Import requires nonempty {key}')
            for key in FIELDS:
                if key not in row or type(row[key]) is not int or row[key] < 0:
                    raise ValueError(f'Import requires a nonnegative integer {key}')
            item = {key: row[key] for key in ('tool', 'model', *FIELDS)}
            item['date'] = date_of(row['timestamp'], tz)
            key = identity('import', row['source'], row['tool'], row['id'])
            if key in records and any(records[key][k] != item[k] for k in ('date','tool','model')):
                raise ValueError('Conflicting metadata for an imported record ID')
            records = merge(records, {key: item})
    return records


def merge(old, new):
    merged = dict(old)
    for key, item in new.items():
        prev = old.get(key)
        merged[key] = item if prev is None else {**prev, **{k: max(prev[k], item[k]) for k in FIELDS}}
    return merged


def device_name(value=None):
    """Return a stable, filename-safe device identifier."""
    candidate = value or os.getenv('USAGE_CARD_DEVICE') or socket.gethostname().split('.')[0]
    if not DEVICE_PATTERN.fullmatch(candidate):
        raise ValueError('Device must use 1-64 letters, numbers, underscores, or hyphens')
    return candidate


def read_ledger(path, timezone_name, tools):
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('version') not in (1, 2):
        raise ValueError(f'Unsupported ledger version: {path}')
    if state.get('timezone') != timezone_name or state.get('tools') != tools:
        raise ValueError(f'Ledger settings differ: {path}')
    records = state.get('records')
    if not isinstance(records, dict):
        raise ValueError(f'Ledger records must be an object: {path}')
    return records


def aggregate_ledgers(paths, timezone_name, tools):
    """Merge device ledgers; identical request hashes are counted once globally."""
    records = {}
    for path in sorted(set(paths)):
        if path.exists():
            records = merge(records, read_ledger(path, timezone_name, tools))
    return records


def summarize(records, tz, now):
    daily, tools, models = {}, {}, {}
    totals = {k: 0 for k in FIELDS}
    for item in records.values():
        for mapping, key in ((daily, item['date']), (tools, item['tool']), (models, item['model'])):
            target = mapping.setdefault(key, {k: 0 for k in FIELDS})
            for k in FIELDS:
                target[k] += item[k]
        for k in FIELDS:
            totals[k] += item[k]
    for values in [totals, *daily.values(), *tools.values(), *models.values()]:
        values['total'] = sum(values[k] for k in FIELDS)
    today = now.astimezone(tz).date()
    active = [d for d, v in daily.items() if v['total'] > 0]
    return {'version': 1, 'timezone': str(tz), 'updated_at': now.isoformat(), 'totals': totals,
            'last_30_days': sum(v['total'] for d, v in daily.items() if (today-timedelta(days=29)).isoformat() <= d <= today.isoformat()),
            'active_days': len(active), 'daily': dict(sorted(daily.items())), 'tools': tools, 'models': models}


def compact(n):
    for unit, scale in [('T', 10**12), ('B', 10**9), ('M', 10**6), ('K', 10**3)]:
        if n >= scale:
            return f'{n / scale:.2f}{unit}'
    return str(n)


def render(data, title, theme, today):
    light = theme == 'light'
    bg, panel, fg, muted, border = ('#f4f7fc','#ffffff','#14243b','#536680','#dce5ef') if light else ('#080e1c','#101b2e','#edf5ff','#93a9c6','#22334d')
    cyan, green, violet, amber = ('#007e99','#087f5b','#7946d2','#a86700') if light else ('#57e3ff','#7affba','#bc9aff','#ffc878')
    ramp = ['#e5edf4','#b6ebdc','#6bd9b3','#26af86','#087f5b'] if light else ['#19263c','#164d4b','#187967','#36ba8b','#7affba']
    total = data['totals']['total']
    tools = sorted(data['tools'].items(), key=lambda item: (-item[1]['total'], item[0]))
    height = max(626, 510 + 47 * len(tools))
    values = [data['daily'].get((today-timedelta(days=i)).isoformat(), {}).get('total', 0) for i in range(29,-1,-1)]
    week, previous_week = sum(values[-7:]), sum(values[-14:-7])
    trend = f'{(week/previous_week-1)*100:+.0f}% vs previous 7d' if previous_week else 'No previous-week baseline'
    streak = 0
    cursor = today if values[-1] else today-timedelta(days=1)
    while data['daily'].get(cursor.isoformat(), {}).get('total', 0) > 0:
        streak += 1
        cursor -= timedelta(days=1)
    points = [(475+i*12.5, 217-(v/max(max(values),1))*98) for i,v in enumerate(values)]
    line = ' '.join(f'{x:.1f},{y:.1f}' for x,y in points)
    area = f'475,225 {line} 837.5,225'
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{height}" viewBox="0 0 900 {height}" role="img" aria-labelledby="title desc">',
           f'<title id="title">{escape(title)}</title><desc id="desc">{total:,} tokens. {week:,} in the last seven days. {streak} active-day streak. Activity based on local logs, not a live feed.</desc>',
           f'''<defs>
             <linearGradient id="edge"><stop stop-color="{cyan}"/><stop offset=".5" stop-color="{violet}"/><stop offset="1" stop-color="{green}"/></linearGradient>
             <linearGradient id="wash" x2="1" y2="1"><stop stop-color="{cyan}" stop-opacity=".10"/><stop offset="1" stop-color="{violet}" stop-opacity=".02"/></linearGradient>
             <linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop stop-color="{cyan}" stop-opacity=".28"/><stop offset="1" stop-color="{cyan}" stop-opacity="0"/></linearGradient>
             <clipPath id="frame"><rect x="1" y="1" width="898" height="{height-2}" rx="22"/></clipPath>
           </defs>
           <style>
             @keyframes pulse {{ 0%,100% {{opacity: .9}} 50% {{opacity: .35}} }}
             @keyframes reveal {{
               from {{opacity:0; transform:translateY(10px)}}
               to {{opacity:1; transform:translateY(0)}}
             }}
             @keyframes trace {{
               from {{stroke-dashoffset:1000}}
               to {{stroke-dashoffset:0}}
             }}
             @keyframes lineFade {{from {{opacity:0}} to {{opacity:1}}}}
             @keyframes cellIn {{
               from {{opacity:0; transform:scale(.72)}}
               to {{opacity:1; transform:scale(1)}}
             }}
             .reveal {{
               opacity:0;
               animation:reveal .72s cubic-bezier(.22,.8,.24,1) forwards;
             }}
             .pulse {{animation: pulse 3s ease-in-out infinite}}
             .trace {{
               opacity:0;
               stroke-dasharray:1000;
               stroke-dashoffset:1000;
               animation:trace 1.45s cubic-bezier(.22,.8,.24,1) 360ms forwards,
                         lineFade .3s ease-out 330ms forwards;
             }}
             .cell {{
               opacity:0;
               transform-box:fill-box;
               transform-origin:center;
               animation:cellIn .34s ease-out forwards;
             }}
             @media (prefers-reduced-motion: reduce) {{
               .reveal,.pulse,.trace,.cell {{animation:none}}
               .reveal,.trace,.cell {{opacity:1;transform:none;stroke-dashoffset:0}}
             }}
           </style>''',
           f'<rect x=".5" y=".5" width="899" height="{height-1}" rx="22" fill="{bg}" stroke="{border}"/>',
           f'<g clip-path="url(#frame)"><path d="M0 1H900" stroke="url(#edge)" stroke-width="5"/><rect width="900" height="255" fill="url(#wash)"/></g>',
           '<g font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif">']
    def text(x,y,value,size=12,color=muted,weight=400):
        out.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}">{escape(str(value))}</text>')
    out.append('<g class="reveal" style="animation-delay:40ms">')
    text(30,36,'AI / ACTIVITY MONITOR',10,cyan,700)
    text(30,66,title[:48],23,fg,700)
    out.append(f'<rect x="695" y="27" width="175" height="28" rx="14" fill="{panel}" stroke="{border}"/><circle class="pulse" cx="711" cy="41" r="3.5" fill="{green}"/>')
    text(723,45,'LOCAL LOG SNAPSHOT',9,green,600)
    out.append('</g><g class="reveal" style="animation-delay:120ms">')
    text(30,108,'TOTAL TOKENS PROCESSED',10,muted,600)
    text(26,169,compact(total),64,fg,700)
    text(32,197,'INPUT + OUTPUT + CACHE',10,cyan,600)
    text(32,226,f'{data["active_days"]} active days  /  {len(tools)} tools tracked',12)
    out.append('</g><g class="reveal" style="animation-delay:210ms">')
    text(475,103,'30-DAY TOKEN PULSE',10,cyan,600)
    text(752,103,compact(data['last_30_days']),14,fg,700)
    for y in (125,170,215):
        out.append(f'<path d="M475 {y}H838" stroke="{border}" stroke-dasharray="3 6"/>')
    out.append(f'<polygon points="{area}" fill="url(#area)"/><polyline class="trace" points="{line}" fill="none" stroke="{cyan}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>')
    x,y=points[-1]
    out.append(f'<circle class="pulse" cx="{x}" cy="{y}" r="7" fill="{cyan}" opacity=".2"/><circle cx="{x}" cy="{y}" r="3" fill="{cyan}"/>')
    text(475,243,(today-timedelta(days=29)).strftime('%b %d'),9)
    text(810,243,'TODAY',9)
    out.append('</g><g class="reveal" style="animation-delay:330ms">')
    for x,label,value,note,color in [(30,'LAST 7 DAYS',compact(week),trend,cyan),(316,'CURRENT STREAK',f'{streak} DAYS','Through today or yesterday',green),(602,'TODAY',compact(values[-1]),'Recorded in your local timezone',violet)]:
        out.append(f'<rect x="{x}" y="268" width="268" height="91" rx="12" fill="{panel}" stroke="{border}"/><path d="M{x+16} 287h16" stroke="{color}" stroke-width="3" stroke-linecap="round"/>')
        text(x+41,290,label,9,muted,600)
        text(x+16,324,value,26,color,700)
        text(x+16,344,note,10)
    out.append('</g><g class="reveal" style="animation-delay:460ms">')
    text(30,389,'TOKEN FLOW',10,muted,700)
    x=30
    colors=[cyan,violet,green,amber]
    for i,k in enumerate(FIELDS):
        width=840*data['totals'][k]/total if total else 0
        out.append(f'<rect x="{x:.2f}" y="401" width="{width:.2f}" height="7" fill="{colors[i]}"/>')
        x+=width
        text(30+i*210,431,f'{k.replace("_"," ").title()}  {compact(data["totals"][k])}',11,colors[i])
    out.append('</g><g class="reveal" style="animation-delay:580ms">')
    text(30,467,'CONSISTENCY MAP',10,muted,700)
    text(371,467,'26 WEEKS',9)
    start=today-timedelta(days=(today.weekday()+1)%7)-timedelta(weeks=25)
    max_day=max([v['total'] for d,v in data['daily'].items() if start.isoformat() <= d <= today.isoformat()] or [1]) or 1
    for w in range(26):
        for d in range(7):
            day=start+timedelta(weeks=w,days=d)
            if day>today: continue
            n=data['daily'].get(day.isoformat(),{}).get('total',0)
            level=0 if not n else min(4,max(1,int((n/max_day)**.5*4)))
            stroke=f' stroke="{cyan}" stroke-width="1"' if day==today else ''
            out.append(f'<rect class="cell" style="animation-delay:{650+w*18+d*4}ms" x="{30+w*16}" y="{481+d*13}" width="12" height="9" rx="2" fill="{ramp[level]}"{stroke}><title>{day.isoformat()}: {n:,} tokens</title></rect>')
    out.append('</g><g class="reveal" style="animation-delay:690ms">')
    text(486,467,'TOOL MOMENTUM',10,muted,700)
    text(798,467,'ALL TIME',9)
    for i,(name,usage) in enumerate(tools):
        y=492+i*47
        color=colors[i%4]
        text(486,y,name[:28],12,fg,600)
        text(781,y,compact(usage['total']),12,color,700)
        out.append(f'<rect x="486" y="{y+10}" width="354" height="5" rx="2" fill="{border}"/><rect x="486" y="{y+10}" width="{354*usage["total"]/total if total else 0:.2f}" height="5" rx="2" fill="{color}"/>')
    out.append('</g><g class="reveal" style="animation-delay:820ms">')
    out.append(f'<path d="M30 {height-39}H870" stroke="{border}"/>')
    text(30,height-17,f'{data["timezone"]} / {data["updated_at"][:10]} / Local log snapshot',9)
    text(602,height-17,'CACHE INCLUDED · NO BILLING ESTIMATE',9)
    out.append('</g></g></svg>')
    return '\n'.join(out)


VARIANTS = {'full': (846, 225), 'compact': (846, 195), 'half': (423, 195),
            'grass': (423, 195), 'half-grass': (423, 335)}


def render_variant(data, title, theme, today, variant):
    """Compact compositions derived from the activity dashboard, not scaled cards."""
    width, height = VARIANTS[variant]
    light = theme == 'light'
    bg, panel, fg, muted, border = ('#f4f7fc','#e7eef8','#14243b','#536680','#d3dfed') if light else ('#080e1c','#111e33','#edf5ff','#93a9c6','#243653')
    cyan, green, violet = ('#007e99','#087f5b','#7946d2') if light else ('#57e3ff','#7affba','#bc9aff')
    ramp = ['#e0e8f2','#b6ebdc','#6bd9b3','#26af86','#087f5b'] if light else ['#19263c','#164d4b','#187967','#36ba8b','#7affba']
    values=[data['daily'].get((today-timedelta(days=i)).isoformat(),{}).get('total',0) for i in range(29,-1,-1)]
    total=data['totals']['total']
    week=sum(values[-7:])
    tools=sorted(data['tools'].items(),key=lambda item:(-item[1]['total'],item[0]))
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
         f'<title id="title">{escape(title)} — {variant}</title><desc id="desc">{total:,} total tokens; {data["active_days"]} active days. Local usage snapshot.</desc>',
         f'''<defs><linearGradient id="edge"><stop stop-color="{cyan}"/><stop offset="1" stop-color="{violet}"/></linearGradient>
         <linearGradient id="wash" x2="1" y2="1"><stop stop-color="{cyan}" stop-opacity=".13"/><stop offset="1" stop-color="{violet}" stop-opacity=".02"/></linearGradient>
         <linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop stop-color="{cyan}" stop-opacity=".25"/><stop offset="1" stop-color="{cyan}" stop-opacity="0"/></linearGradient>
         <clipPath id="frame"><rect x="1" y="1" width="{width-2}" height="{height-2}" rx="19"/></clipPath></defs>
         <style>
           @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.35}}}}
           @keyframes reveal{{from{{opacity:0;transform:translateY(7px)}}to{{opacity:1;transform:translateY(0)}}}}
           @keyframes trace{{from{{stroke-dashoffset:1000}}to{{stroke-dashoffset:0}}}}
           @keyframes lineFade{{from{{opacity:0}}to{{opacity:1}}}}
           @keyframes cellIn{{from{{opacity:0;transform:scale(.75)}}to{{opacity:1;transform:scale(1)}}}}
           .reveal{{opacity:0;animation:reveal .68s cubic-bezier(.22,.8,.24,1) forwards}}
           .pulse{{animation:pulse 3s ease-in-out infinite}}
           .trace{{opacity:0;stroke-dasharray:1000;stroke-dashoffset:1000;animation:trace 1.25s cubic-bezier(.22,.8,.24,1) 300ms forwards,lineFade .3s ease-out 270ms forwards}}
           .cell{{opacity:0;transform-box:fill-box;transform-origin:center;animation:cellIn .3s ease-out forwards}}
           @media(prefers-reduced-motion:reduce){{.reveal,.pulse,.trace,.cell{{animation:none}}.reveal,.trace,.cell{{opacity:1;transform:none;stroke-dashoffset:0}}}}
         </style>''',
         f'<rect x=".5" y=".5" width="{width-1}" height="{height-1}" rx="19" fill="{bg}" stroke="{border}"/>',
         f'<g clip-path="url(#frame)"><rect width="{width}" height="{height}" fill="url(#wash)"/><path d="M0 1H{width}" stroke="url(#edge)" stroke-width="4"/></g>',
         '<g font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif">']
    def text(x,y,value,size=11,color=muted,weight=400,anchor='start'):
        out.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{escape(str(value))}</text>')
    def box(x,y,w,h):
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{panel}"/>')
    def spark(x,y,w,h):
        peak=max(max(values),1)
        points=[(x+i*w/29,y+h-v/peak*h) for i,v in enumerate(values)]
        line=' '.join(f'{a:.1f},{b:.1f}' for a,b in points)
        for frac in [.25,.75]:
            out.append(f'<path d="M{x} {y+h*frac}h{w}" stroke="{border}" stroke-dasharray="2 5"/>')
        out.append(f'<polygon points="{x},{y+h} {line} {x+w},{y+h}" fill="url(#area)"/><polyline class="trace" points="{line}" fill="none" stroke="{cyan}" stroke-width="2" stroke-linejoin="round"/>')
        a,b=points[-1]
        out.append(f'<circle class="pulse" cx="{a}" cy="{b}" r="5" fill="{cyan}" opacity=".3"/><circle cx="{a}" cy="{b}" r="2.5" fill="{cyan}"/>')
    def heatmap(x,y,w,h):
        start=today-timedelta(days=(today.weekday()+1)%7)-timedelta(weeks=25)
        peak=max([v['total'] for d,v in data['daily'].items() if start.isoformat()<=d<=today.isoformat()] or [1]) or 1
        for col in range(26):
            for row in range(7):
                day=start+timedelta(weeks=col,days=row)
                if day>today:continue
                n=data['daily'].get(day.isoformat(),{}).get('total',0)
                level=0 if not n else min(4,max(1,int((n/peak)**.5*4)))
                out.append(f'<rect class="cell" style="animation-delay:{420+col*16+row*3}ms" x="{x+col*w/26:.2f}" y="{y+row*h/7:.2f}" width="{w/26-2:.2f}" height="{h/7-2:.2f}" rx="1.5" fill="{ramp[level]}"><title>{day}: {n:,} tokens</title></rect>')
    def metric(x,y,label,value,color=cyan):
        text(x,y,label,8,muted,600)
        text(x,y+23,value,21,color,700)
    # Minimal brand line gives each layout space for an expressive main composition.
    out.append('<g class="reveal" style="animation-delay:40ms">')
    out.append(f'<path d="M21 30l5-9-1 6h6l-6 9 1-6z" fill="{cyan}"/>')
    heading=title if len(title)<=27 else title[:26]+'…'
    text(38,32,heading,12,fg,600)
    out.append('</g><g class="reveal" style="animation-delay:140ms">')
    if variant=='full':
        text(24,66,'LIFETIME TOKEN FLOW',9,cyan,600)
        text(20,126,compact(total),58,fg,700)
        text(24,148,f'{data["active_days"]} active days · {len(tools)} tools',11)
        text(321,65,'30-DAY PULSE',9,cyan,600)
        spark(321,79,328,69)
        box(679,54,145,106)
        metric(693,75,'LAST 30 DAYS',compact(data['last_30_days']))
        metric(693,122,'TODAY',compact(values[-1]),violet)
        for i,(label,value,color) in enumerate([('INPUT',data['totals']['input'],cyan),('OUTPUT',data['totals']['output'],violet),('CACHE READ',data['totals']['cache_read'],green),('CACHE WRITE',data['totals']['cache_write'],'#a86700' if light else '#ffc878')]):
            x=24+i*204
            box(x,177,186,30)
            text(x+11,196,label,8)
            text(x+174,197,compact(value),13,color,700,'end')
        text(824,31,'TOKEN ANALYTICS',8,muted,600,'end')
    elif variant=='compact':
        text(24,62,'ALL-TIME / TOKENS',8,cyan,600)
        text(20,111,compact(total),49,fg,700)
        text(24,140,f'{compact(week)} this week',13,green,600)
        text(24,167,f'{data["active_days"]} days of building',10)
        text(266,61,'30-DAY PULSE',8,cyan,600)
        spark(266,76,242,88)
        box(535,49,289,127)
        text(550,68,'BUILDING RHYTHM',8,green,600)
        heatmap(550,80,259,70)
        text(550,165,'26 WEEKS',8)
        text(809,165,f'{len(tools)} TOOLS',8,cyan,600,'end')
        text(824,31,'FLOW + RHYTHM',8,muted,600,'end')
    elif variant=='half':
        text(24,59,'ALL-TIME TOKENS',8,cyan,600)
        text(20,109,compact(total),49,fg,700)
        spark(247,61,149,52)
        box(22,132,379,44)
        text(35,150,'THIS WEEK',8)
        text(35,166,compact(week),14,cyan,700)
        text(165,150,'TODAY',8)
        text(165,166,compact(values[-1]),14,violet,700)
        text(291,150,'ACTIVE DAYS',8)
        text(291,166,data['active_days'],14,green,700)
        text(395,32,'PULSE',8,cyan,600,'end')
    elif variant=='grass':
        text(24,72,str(data['active_days']),35,fg,700)
        text(24,89,'ACTIVE DAYS',8,green,600)
        spark(148,51,249,40)
        text(396,32,'RHYTHM',8,green,600,'end')
        heatmap(24,105,375,61)
        text(24,182,'26 WEEKS OF BUILDING',8)
        text(397,182,'30-DAY PULSE ABOVE',8,cyan,400,'end')
    else:
        text(24,61,'LIFETIME TOKENS',8,cyan,600)
        text(20,109,compact(total),49,fg,700)
        text(396,61,'LAST 30 DAYS',8,muted,400,'end')
        text(396,85,compact(data['last_30_days']),23,cyan,700,'end')
        spark(25,124,371,51)
        for x,label,value,color in [(23,'THIS WEEK',compact(week),cyan),(154,'TODAY',compact(values[-1]),violet),(285,'ACTIVE DAYS',str(data['active_days']),green)]:
            box(x,189,115,47)
            text(x+10,204,label,8)
            text(x+10,223,value,18,color,700)
        text(24,256,'BUILDING RHYTHM / 26 WEEKS',8,green,600)
        heatmap(24,266,375,51)
        text(396,32,'MINI MONITOR',8,cyan,600,'end')
    out.append('</g></g></svg>')
    return '\n'.join(out)


def readme_snippets():
    snippets=[]
    layouts = [('dashboard','ai-usage',900),('combo','ai-usage-combo',900)]
    layouts += [(name,f'ai-usage-{name}',size[0]) for name,size in VARIANTS.items()]
    for variant,stem,width in layouts:
        snippets.append(f'''### {variant}

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./cards/{stem}-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="./cards/{stem}-light.svg">
  <img alt="AI coding usage — {variant}" src="./cards/{stem}-light.svg" width="{width}">
</picture>
''')
    snippets.append('### 반폭 카드 나란히\n\n아래 두 picture 요소를 같은 줄에 배치하세요.\n\n'+''.join(
        f'<picture><source media="(prefers-color-scheme: dark)" srcset="./cards/ai-usage-{variant}-dark.svg"><img alt="AI usage {variant}" src="./cards/ai-usage-{variant}-light.svg" width="49%"></picture> '
        for variant in ['half','grass']))
    return '\n'.join(snippets)+'\n'


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', choices=['all','codex','claude','gemini','imports'], default='all')
    parser.add_argument('--codex-home', type=Path, default=Path(os.getenv('CODEX_HOME', str(Path.home()/'.codex'))))
    parser.add_argument('--claude-home', type=Path, default=Path(os.getenv('CLAUDE_CONFIG_DIR', str(Path.home()/'.claude'))))
    parser.add_argument('--gemini-home', type=Path, default=Path.home()/'.gemini')
    parser.add_argument('--import-json', type=Path, action='append', default=[], help='Repeatable normalized usage JSON input')
    parser.add_argument('--timezone', default='Asia/Seoul')
    parser.add_argument('--title', default='My AI Coding Usage')
    parser.add_argument('--output', type=Path, default=Path('cards'))
    parser.add_argument('--device', help='Stable ID for this computer (or USAGE_CARD_DEVICE)')
    parser.add_argument('--state-dir', type=Path, default=Path('.usage-state/devices'),
                        help='Directory containing one shared ledger per device')
    parser.add_argument('--state', type=Path,
                        help='Legacy single-ledger path; disables multi-device aggregation')
    parser.add_argument('--demo', action='store_true', help='Generate clearly labeled sample cards without reading logs')
    args = parser.parse_args(argv)
    tz, now = ZoneInfo(args.timezone), datetime.now(timezone.utc)
    warnings = defaultdict(int)
    if args.demo:
        records = {}
        for i in range(182):
            if i % 6 == 0:
                continue
            day = (now.astimezone(tz).date()-timedelta(days=i)).isoformat()
            for j,tool in enumerate(['Codex','Claude Code']):
                records[f'{i}-{j}'] = {'date':day,'tool':tool,'model':'sample', 'input':(i*7919)%90000,'output':(i*997)%20000,'cache_read':(i*1543)%80000,'cache_write':j*2500}
        args.title += ' · DEMO'
    else:
        device = device_name(args.device)
        own_state = args.state or args.state_dir/f'{device}.json'
        roots = {}
        if args.tools in ('all','codex'):
            roots['Codex'] = [args.codex_home/'sessions',args.codex_home/'archived_sessions']
        if args.tools in ('all','claude'):
            roots['Claude Code'] = [args.claude_home/'projects']
        old = read_ledger(own_state,args.timezone,args.tools) if own_state.exists() else {}
        # One-time compatibility path for installations created before device ledgers.
        legacy_state = Path('.usage-state/ledger.json')
        if not args.state and args.tools == 'all' and not old and legacy_state.exists():
            old = read_ledger(legacy_state,args.timezone,args.tools)
            warnings['legacy_ledger_migrated'] = 1
        fresh = collect(roots,tz,warnings)
        if args.tools in ('all', 'gemini'):
            fresh = merge(fresh, collect_gemini(args.gemini_home/'tmp', tz, warnings))
        fresh = merge(fresh, import_records(args.import_json, tz))
        if not fresh and not old:
            raise ValueError('No supported token usage found. Check log locations or use --demo.')
        own_records = merge(old,fresh)
        atomic_write(own_state,json.dumps({
            'version':2, 'device':device, 'timezone':args.timezone,
            'tools':args.tools, 'updated_at':now.isoformat(), 'records':own_records
        },ensure_ascii=False,separators=(',',':'))+'\n')
        ledger_paths = [own_state] if args.state else list(args.state_dir.glob('*.json'))
        records = aggregate_ledgers(ledger_paths,args.timezone,args.tools)
        warnings['device_ledgers'] = len(ledger_paths)
    data = summarize(records,tz,now)
    data['demo'] = args.demo
    for theme in ['dark','light']:
        dashboard = render(data,args.title,theme,now.astimezone(tz).date())
        atomic_write(args.output/f'ai-usage-{theme}.svg', dashboard)
        atomic_write(args.output/f'ai-usage-combo-{theme}.svg', dashboard)
        for variant in VARIANTS:
            atomic_write(args.output/f'ai-usage-{variant}-{theme}.svg', render_variant(data,args.title,theme,now.astimezone(tz).date(),variant))
    atomic_write(args.output/'usage-summary.json',json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    atomic_write(args.output/'README-snippet.md',readme_snippets())
    result = {'total_tokens':data['totals']['total'],'active_days':data['active_days'],
              'output':str(args.output),'diagnostics':dict(warnings)}
    if not args.demo:
        result.update({'device':device,'device_ledger':str(own_state)})
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'Error: {exc}',file=sys.stderr)
        sys.exit(1)
