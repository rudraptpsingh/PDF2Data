import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request,urlopen

import pytest

from local_vector_batch.review_queue import empty_focused_queue,load_item


def test_empty_focus_is_explicit_and_nonempty_wrong_indexes_still_reject(tmp_path):
    manifest=tmp_path/'queue.jsonl';manifest.write_text('')
    item=empty_focused_queue(manifest,0)
    assert item['empty']and item['total']==0 and item['scope']=='focused_queue'
    assert not item['page_quality_approval']and not item['legal_approval']
    with pytest.raises(ValueError):empty_focused_queue(manifest,1)
    manifest.write_text('{}\n')
    assert empty_focused_queue(manifest,0)is None
    with pytest.raises(ValueError):load_item(manifest,1)


def test_actual_local_http_empty_queue_returns200_without_image_or_decision(tmp_path):
    manifest=tmp_path/'queue.jsonl';manifest.write_text('')
    root=Path(__file__).resolve().parents[1]
    with socket.socket()as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env={**os.environ,'PYTHONPATH':str(root/'src'),'PYTHONDONTWRITEBYTECODE':'1'}
    process=subprocess.Popen([sys.executable,'-m','local_vector_batch.cli','serve','--port',str(port),'--review-manifest',str(manifest)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    base=f'http://127.0.0.1:{port}'
    try:
        html=None
        for _ in range(100):
            try:
                with urlopen(base+'/review',timeout=1)as response:html=response.read().decode()
                break
            except OSError:
                if process.poll()is not None:raise AssertionError(process.stderr.read().decode())
                time.sleep(.03)
        assert html is not None
        token=re.search(r"const token='([^']+)'",html).group(1)
        assert 'if(item.empty){showEmpty();return}'in html
        assert "b.disabled=value||!item"in html
        def get(path):
            with urlopen(Request(base+path,headers={'X-Local-Token':token}),timeout=3)as response:return json.loads(response.read())
        assert get('/api/review-item?index=0')['message']=='No unresolved questions in this focused queue'
        assert get('/api/review-group?index=0')['differences']==[]
        for path in ['/api/review-item?index=1','/api/review-image?index=0']:
            with pytest.raises(HTTPError)as error:get(path)
            assert error.value.code==400
        manifest.write_text('{}\n')
        with pytest.raises(HTTPError)as error:get('/api/review-item?index=1')
        assert error.value.code==400
    finally:
        process.terminate();process.wait(timeout=5)
