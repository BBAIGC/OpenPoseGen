"""Interruptible and time-bounded ownership of the DLSS native runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


def wait_worker(process,response,check_cancel,timeout=300):
    start=time.monotonic()
    try:
        while True:
            check_cancel()
            if response.is_file():
                result=json.loads(response.read_text(encoding='utf-8'))
                if result.get('status')!='completed':raise RuntimeError('DLSS5处理失败：'+result.get('error','未知错误'))
                return result
            if process.poll() is not None:raise RuntimeError('DLSS5子进程提前退出，请查看同目录日志。')
            if time.monotonic()-start>timeout:raise RuntimeError(f'DLSS5超过{timeout}秒，处理进程已结束；原图与已保存结果保留。')
            time.sleep(.15)
    finally:
        if process.poll() is None:
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


def run_dlss(source,output,kwargs,plugin,check_cancel):
    import folder_paths
    directory=Path(output).parent/'workers'/uuid.uuid4().hex;directory.mkdir(parents=True,exist_ok=True)
    response=directory/'result.json';request=directory/'request.json'
    request.write_text(json.dumps({'source':str(source),'output':str(output),'kwargs':kwargs,'plugin':str(plugin),
        'core':str(Path(folder_paths.__file__).parent),'models':str(folder_paths.models_dir),'response':str(response)},ensure_ascii=False),encoding='utf-8')
    with (directory/'worker.log').open('wb') as log:
        process=subprocess.Popen([sys.executable,'-s',str(Path(__file__).with_name('dlss_worker.py')),str(request)],stdout=log,stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return wait_worker(process,response,check_cancel)
