"""Exact-identity, source-audited PDF registry; never supplies review answers."""
import datetime,hashlib
from pathlib import Path
from kernel.util import read_json,write_json


def register(board,*,mpn,manufacturer,path,source_url,verification_record):
    board=Path(board);pdf=(board/path).resolve();record=(board/verification_record).resolve()
    for p in (pdf,record):
        if not p.is_relative_to(board.resolve()) or not p.is_file():raise ValueError('Verified source must be an existing board artifact')
    if pdf.suffix.lower()!='.pdf':raise ValueError('A PDF source is required')
    target=board/'verified_datasheet_sources.json'
    registry=read_json(target) if target.is_file() else {'version':'material-sources.v1','sources':[]}
    registry['sources']=[r for r in registry['sources'] if (r['mpn'],r['manufacturer'])!=(mpn,manufacturer)]
    registry['sources'].append({'mpn':mpn,'manufacturer':manufacturer,'path':path,'source_url':source_url,
        'sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),'verification_record':verification_record,
        'verification_sha256':hashlib.sha256(record.read_bytes()).hexdigest(),
        'registered_at':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    write_json(target,registry)


def lookup(board,mpn,manufacturer):
    board=Path(board);registry=board/'verified_datasheet_sources.json'
    if not registry.is_file():return None
    matches=[r for r in read_json(registry)['sources'] if r['mpn']==mpn and r['manufacturer']==manufacturer]
    if not matches:return None
    if len(matches)!=1:raise ValueError('Ambiguous exact-identity PDF registration')
    source=matches[0]
    for key,digest in [('path','sha256'),('verification_record','verification_sha256')]:
        p=(board/source[key]).resolve()
        if not p.is_relative_to(board.resolve()) or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=source[digest]:
            raise ValueError('Verified PDF or verification record changed: '+source[key])
    return source
