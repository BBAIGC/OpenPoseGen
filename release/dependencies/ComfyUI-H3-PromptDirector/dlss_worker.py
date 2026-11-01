"""One DLSS still per disposable process; never shuts NGX down in Comfy."""
import importlib
import json
import os
from pathlib import Path
import sys
import types


def run(request):
    import numpy as np
    import torch
    from PIL import Image
    sys.path.insert(0,request['core'])
    import folder_paths
    folder_paths.models_dir=request['models']
    package=types.ModuleType('codex_isolated_dlss');package.__path__=[request['plugin']];sys.modules[package.__name__]=package
    module=importlib.import_module(package.__name__+'.nodes')
    with Image.open(request['source']) as image:
        pixels=torch.from_numpy(np.array(image.convert('RGB'),dtype=np.float32)/255)[None]
    tensor=module.RH_DLSS5Enhance().enhance(image=pixels,**request['kwargs'])[0]
    target=Path(request['output']);temp=target.with_suffix('.partial.png')
    Image.fromarray((tensor[0,:,:,:3].detach().cpu().clamp(0,1).numpy()*255).round().astype('uint8')).save(temp)
    os.replace(temp,target)


if __name__=='__main__':
    request=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    response=Path(request['response'])
    try:run(request);result={'status':'completed'};code=0
    except BaseException as error:result={'status':'failed','error':str(error)};code=1
    temp=response.with_suffix('.tmp');temp.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8');os.replace(temp,response)
    # Skip Python/NGX finalizers. The parent also bounds process teardown.
    os._exit(code)
