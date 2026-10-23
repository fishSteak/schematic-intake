"""Render source PDF pages for visual evidence with an exact source receipt."""
import hashlib
from pathlib import Path
from kernel.util import read_json, write_json


def render_pages(board, source, pages, output, *, dpi=144):
    import pymupdf
    board=Path(board).resolve();source=(board/source).resolve();output=(board/output).resolve()
    if not source.is_relative_to(board) or not output.is_relative_to(board):
        raise ValueError('Page evidence must remain within the board workspace')
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    records=[]
    with pymupdf.open(source) as doc:
        if not pages or any(not isinstance(page,int) or not 1<=page<=len(doc) for page in pages):
            raise ValueError('Every source page must exist')
        output.mkdir(parents=True,exist_ok=True)
        for page in pages:
            target=output/f'{source.stem}-{source_hash[:12]}-p{page}.png'
            doc[page-1].get_pixmap(dpi=dpi,alpha=False).save(target)
            records.append({'pdf_page':page,'path':target.relative_to(board).as_posix(),
                            'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    if hashlib.sha256(source.read_bytes()).hexdigest()!=source_hash:
        raise RuntimeError('PDF source changed during extraction')
    receipt={'source':source.relative_to(board).as_posix(),'source_sha256':source_hash,
             'renderer':'PyMuPDF '+pymupdf.VersionBind,'dpi':dpi,'pages':records,
             'verification':'source identity and page rendering; visible content requires model review'}
    write_json(output/f'{source.stem}-{source_hash[:12]}-pages.json',receipt)
    return receipt


def for_object(board, rule_id, obj):
    board=Path(board).resolve();records=[]
    folder=board/'review_out/page_evidence'/rule_id/obj
    for path in folder.glob('*-pages.json'):
        receipt=read_json(path)
        for rel,expected in [(receipt['source'],receipt['source_sha256'])]+[(p['path'],p['sha256']) for p in receipt['pages']]:
            source=(board/rel).resolve()
            if not source.is_relative_to(board) or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest()!=expected:
                raise ValueError('Stale or invalid visual source evidence: '+rel)
        records.append(dict(receipt,receipt_path=path.relative_to(board).as_posix()))
    return records
