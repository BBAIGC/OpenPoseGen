"""Bounded replay of live H3 state, independent of WebSocket delivery."""
from collections import OrderedDict
import copy
import threading

_STATES=OrderedDict()
_LOCK=threading.RLock()


def remember(event,partial=False):
    run=event.get('run_id')
    if not run:return
    with _LOCK:
        previous=_STATES.get(run)
        if partial:
            if not previous or previous.get('execution_id')!=event.get('execution_id'):return
            jobs=previous.setdefault('image_jobs',[])
            index=next((i for i,row in enumerate(jobs) if row['id']==event['job']['id']),None)
            if index is not None:jobs[index]=copy.deepcopy(event['job'])
            previous['progress_seq']=event['progress_seq'];previous['image_summary']=copy.deepcopy(event['image_summary'])
            _STATES[run]=previous
        else:_STATES[run]=copy.deepcopy(event)
        _STATES.move_to_end(run)
        while len(_STATES)>8:_STATES.popitem(last=False)


def get_state(run=None,node_id=None,prompt_id=None):
    with _LOCK:
        candidates=[_STATES.get(run)] if run else list(reversed(_STATES.values()))
        for event in candidates:
            if not event:continue
            if node_id is not None and str(event.get('node_id'))!=str(node_id):continue
            if prompt_id and event.get('prompt_id')!=prompt_id:continue
            return copy.deepcopy(event)
    return None


def execution_context(node_id):
    from server import PromptServer
    server=PromptServer.instance
    if server is None or not getattr(server,'prompt_queue',None):return {}
    with server.prompt_queue.mutex:
        running,_=server.prompt_queue.get_current_queue_volatile()
        for item in running:
            prompt=item[2] if isinstance(item,(list,tuple)) else item.get('prompt',{})
            candidates=[str(key) for key,value in prompt.items() if value.get('class_type')=='H3StoryboardStudio']
            owner=str(node_id) if str(node_id) in candidates else candidates[0] if len(candidates)==1 else None
            if owner:return {'prompt_id':item[1] if isinstance(item,(list,tuple)) else item.get('prompt_id'),'node_id':owner}
    return {}
