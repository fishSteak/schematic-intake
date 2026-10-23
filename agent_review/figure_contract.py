"""Lossless structural aliases for legacy diagram specs, without circuit inference."""
import copy

def normalize(figure):
    fig=copy.deepcopy(figure);operations=[]
    for page in fig.get('pages') or [fig]:
        if isinstance(page.get('notes'),str):
            page['notes']=[page['notes']];operations.append('notes string → note list')
        for field in ('paths','unknown_direction_paths'):
            paths=page.get(field)
            if not isinstance(paths,list):continue
            converted=[]
            for branch in paths:
                if isinstance(branch,dict) and isinstance(branch.get('path',branch.get('nodes')),list):
                    converted.append(branch.get('path',branch.get('nodes')));operations.append(field+'.path/nodes → token list')
                    description=branch.get('description',branch.get('label'))
                    if description:page.setdefault('_branch_descriptions',[]).append(description)
                else:converted.append(branch)
            page[field]=converted
        markers=[]
        for marker in page.get('markers') or []:
            if isinstance(marker,str):
                page.setdefault('_unanchored_notes',[]).append(marker)
                operations.append('unanchored marker → page note')
                continue
            if isinstance(marker,dict) and not marker.get('title') and marker.get('text'):
                marker['title']=marker['text'];operations.append('marker.text → marker.title')
            if isinstance(marker,dict):
                if not marker.get('on') and marker.get('ref'):
                    marker['on']=marker['ref'];operations.append('marker.ref → marker.on')
                if not marker.get('title') and marker.get('label'):
                    marker['title']=marker['label'];operations.append('marker.label → marker.title')
            markers.append(marker)
        if 'markers' in page:page['markers']=markers
    fig['_structural_normalizations']=list(dict.fromkeys(operations))
    return fig


def validate_shape(figure):
    if not isinstance(figure,dict):return ['figure must be an object']
    pages=figure.get('pages');errors=[]
    if not isinstance(pages,list) or not pages:return ['figure.pages must be a nonempty list']
    if any(not isinstance(page,dict) for page in pages):return ['figure.pages entries must be objects']
    for i,page in enumerate(pages):
        for field in ['paths','markers','through_body']:
            if page.get(field) is not None and not isinstance(page[field],list):errors.append(f'figure.pages[{i}].{field} must be a list')
    if errors:return errors
    fig=normalize(figure);pages=fig['pages']
    for i,page in enumerate(pages):
        if not isinstance(page,dict):errors.append(f'figure.pages[{i}] must be an object');continue
        if type(page.get('pdf_page')) is not int or page['pdf_page']<1:errors.append(f'figure.pages[{i}].pdf_page must be a positive integer')
        for branch in page.get('paths') or []:
            if not isinstance(branch,list) or not branch or any(not isinstance(token,str) or not token for token in branch):
                errors.append(f'figure.pages[{i}].paths must contain token lists or objects with path/nodes token lists')
        for marker in page.get('markers') or []:
            if not isinstance(marker,dict) or not isinstance(marker.get('on'),str) or not marker.get('on'):
                errors.append(f'figure.pages[{i}].markers require an on/ref source anchor; put unanchored text in notes')
    return errors
