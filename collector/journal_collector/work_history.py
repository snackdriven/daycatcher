"""Private day queue. No bodies, titles, source identifiers or remote writes."""
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from .cli import atomic_json
from .narrative_evidence import eligible_message, _instant, _revision, _hash, day_window


def day_revisions(db,day):
    """Private metadata index across every page, including excluded removals."""
    since,until=day_window(day);index={}
    connection=sqlite3.connect(Path(db).resolve(strict=True).as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON');connection.execute('BEGIN')
        for row in connection.execute('SELECT m.* FROM messages m WHERE NOT EXISTS '
                '(SELECT 1 FROM exclusions x WHERE x.message_key=m.message_key) ORDER BY m.message_key'):
            if eligible_message(row) not in {'codex-local','claude-code-local'}:continue
            if since<=_instant(row['source_time'])<until:
                index[_hash(['narrative-evidence-v1',row['source'],row['native_id']])]=_revision(row)
    finally:connection.close()
    return index


def discover_days(db):
    """Verified original instants only; exclusions remain authoritative."""
    groups={}
    connection=sqlite3.connect(Path(db).resolve(strict=True).as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON');connection.execute('BEGIN')
        for row in connection.execute('SELECT m.* FROM messages m WHERE NOT EXISTS '
                '(SELECT 1 FROM exclusions x WHERE x.message_key=m.message_key) ORDER BY m.message_key'):
            if eligible_message(row) not in {'codex-local','claude-code-local'}:continue
            instant=_instant(row['source_time']).astimezone(ZoneInfo('America/Chicago'))
            day=(instant.date()-(timedelta(days=1) if instant.hour<3 else timedelta())).isoformat()
            groups.setdefault(day,hashlib.sha256()).update(_revision(row).encode('ascii'))
    finally:connection.close()
    return {day:digest.hexdigest() for day,digest in groups.items()}


class HistoryQueue:
    def __init__(self,path):
        self.path=Path(path)
        if self.path.is_symlink():raise ValueError('history_binding')
        self.value=json.loads(self.path.read_text()) if self.path.exists() else {'version':1,'turn':0,'days':{}}
        if (type(self.value) is not dict or set(self.value)!={'version','turn','days'}
                or self.value['version']!=1 or type(self.value['days']) is not dict
                or type(self.value['turn']) is not int):raise ValueError('history_queue_review_required')
        for day,item in self.value['days'].items():
            day_window(day)
            if (type(item) is not dict or set(item)!={'fingerprint','state','turn','oversize','capacity'}
                    or item['state'] not in {'pending','settled','settled_with_gaps','review_required'}
                    or type(item['fingerprint']) is not str
                    or any(type(item[k]) is not int or item[k]<0 for k in ('turn','oversize','capacity'))):
                raise ValueError('history_queue_review_required')

    def save(self):
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if self.path.parent.is_symlink() or self.path.with_name(self.path.name+'.tmp').is_symlink():
            raise ValueError('history_binding')
        atomic_json(self.path,self.value)
        self.path.chmod(0o600)

    def discover(self,days):
        days=dict(days)
        for day in self.value['days']:
            if day not in days:days[day]='empty'
        for day,fingerprint in days.items():
            day_window(day)
            old=self.value['days'].get(day)
            if old is None or old['fingerprint']!=fingerprint:
                self.value['days'][day]={'fingerprint':fingerprint,'state':'pending',
                    'turn':old['turn'] if old else 0,'oversize':0,'capacity':0}
        # Removed or excluded evidence is reviewed through execute; never erase receipts.
        self.save()

    def next_day(self,exclude=()):
        candidates=[(item['turn'],day) for day,item in self.value['days'].items()
                    if item['state']=='pending' and day not in exclude]
        return min(candidates)[1] if candidates else None

    def ack(self,day,result):
        item=self.value['days'][day];self.value['turn']+=1;item['turn']=self.value['turn']
        item['oversize']=max(item['oversize'],result.get('oversize_omitted',0))
        item['capacity']=max(item['capacity'],result.get('capacity_omitted',0))
        state=result.get('state')
        if state=='idle':item['state']='settled_with_gaps' if item['oversize'] or item['capacity'] else 'settled'
        elif state=='review_required':item['state']='review_required'
        self.save()

    def summary(self):
        return {'discovered':len(self.value['days']),**{state:sum(item['state']==state for item in self.value['days'].values())
            for state in ('pending','settled','settled_with_gaps','review_required')},
            'oversize_omitted':sum(item['oversize'] for item in self.value['days'].values()),
            'capacity_omitted':sum(item['capacity'] for item in self.value['days'].values())}
