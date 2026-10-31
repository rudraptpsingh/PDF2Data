"""The optional MCP surface uses the same CLI pipeline and verifies exported files."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import pytest
from test_legal_l1 import source_fixture
from local_vector_batch.mcp_server import Bridge


def test_bridge_runs_cli_and_scopes_source_reads(tmp_path):
    source = source_fixture(tmp_path)
    bridge = Bridge(tmp_path / 'workspace', [tmp_path])
    try:
        run = bridge.parse(source['path'], ocr='off')
        bridge.workspace.executor.shutdown(wait=True)
        state = bridge.status(run['run_id'])
        assert state['status'] in ('processed', 'processed_with_flags')
        assert all(s['status'] == 'completed' for s in state['stages'])
        assert state['document_type'] == 'generic'
        assert state['publication_eligible'] is False
        page = bridge.read(run['run_id'], page=1, max_chars=200000)
        assert page['data']['page']['page'] == 1
        assert page['source_pdf_sha256'] == state['source_pdf_sha256']
        context=bridge.read(run['run_id'],artifact='continuations',page=1,max_chars=200000)
        assert context['data']['source_pdf_sha256']==state['source_pdf_sha256']
        assert page['data']['continuations']==context['data']
        parts=bridge.read(run['run_id'],artifact='document_parts',page=1,max_chars=200000)
        assert all(p['physical_page']==1 for p in parts['data']['printed_page_labels'])
        parts_file=bridge.workspace.directory(run['run_id'])/'readable/document-parts.json'
        parts_file.write_text('{}')
        with pytest.raises(ValueError,match='changed'):
            bridge.read(run['run_id'],artifact='document_parts')
        table_context=bridge.read(run['run_id'],artifact='table_contexts',page=1,max_chars=200000)
        assert isinstance(table_context['data'],list)
        context_file=bridge.workspace.directory(run['run_id'])/'readable/table-contexts.jsonl'
        assert context_file.is_file()
        context_file.write_text('changed')
        with pytest.raises(ValueError,match='changed'):
            bridge.read(run['run_id'],artifact='table_contexts')
        bounded = bridge.read(run['run_id'], page=1, max_chars=1000)
        assert bounded['status'] == 'requires_scope' and 'data' not in bounded
        artifact = bridge.workspace.directory(run['run_id']) / 'readable/document.md'
        artifact.write_text('Changed output')
        with pytest.raises(ValueError, match='changed'):
            bridge.read(run['run_id'], artifact='markdown')
        context_path=artifact.with_name('continuations.json')
        context_path.write_text('{}')
        with pytest.raises(ValueError,match='changed'):
            bridge.read(run['run_id'],artifact='continuations')
    finally:
        bridge.close()


def test_workspace_owner_and_input_boundaries_are_enforced(tmp_path):
    inputs = tmp_path / 'inputs'; inputs.mkdir()
    bridge = Bridge(tmp_path / 'workspace', [inputs])
    try:
        with pytest.raises(ValueError, match='owner'):
            Bridge(tmp_path / 'workspace', [inputs])
        outside = tmp_path / 'outside.pdf'; outside.write_bytes(b'%PDF-1.7\n')
        with pytest.raises(ValueError, match='outside'):
            bridge.parse(outside)
        alias = inputs / 'alias.pdf'
        try: alias.symlink_to(outside)
        except OSError: return  # Windows may not grant symlink creation.
        with pytest.raises(ValueError, match='outside'):
            bridge.parse(alias)
    finally:
        bridge.close()
    reopened = Bridge(tmp_path / 'workspace', [inputs])
    reopened.close()


def test_context_cell_scope_retains_its_header_and_left_row_and_rejects_unknown_cells(tmp_path):
    import fitz
    source=tmp_path/'table.pdf';pdf=fitz.open();page=pdf.new_page(width=600,height=800)
    for y in (50,90,130):page.draw_line((50,y),(350,y))
    for x in (50,200,350):page.draw_line((x,50),(x,130))
    for x,y,text in [(60,75,'Entity'),(210,75,'Amount'),(60,115,'Example Plant'),(210,115,'10')]:
        page.insert_text((x,y),text,fontsize=12)
    pdf.save(source);pdf.close()
    bridge=Bridge(tmp_path/'workspace',[tmp_path])
    try:
        started=bridge.parse(str(source),ocr='off');bridge.workspace.executor.shutdown(wait=True)
        run_id=started['run_id']
        tables=bridge.read(run_id,artifact='table_contexts',page=1,max_chars=200000)['data']
        assert len(tables)==1
        cell=next(c for c in tables[0]['cells']if c['raw_text']=='10')
        scoped=bridge.read(run_id,artifact='table_contexts',table_id=tables[0]['table_id'],cell_id=cell['id'],max_chars=200000)['data'][0]
        assert {c['raw_text']for c in scoped['cells']}=={'10','Amount','Example Plant'}
        assert scoped['requested_cell_id']==cell['id']
        assert bridge.read(run_id,artifact='table_contexts',max_chars=1000)['status']=='requires_scope'
        with pytest.raises(ValueError,match='Unknown'):
            bridge.read(run_id,artifact='table_contexts',cell_id='missing')
    finally:bridge.close()


def test_official_sdk_stdio_roundtrip_runs_real_pdf(tmp_path):
    pytest.importorskip('mcp.server')
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    source = source_fixture(tmp_path)
    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=[
            '-m', 'local_vector_batch.mcp_server', '--workspace', str(tmp_path / 'mcp'),
            '--allow-dir', str(tmp_path)], env={**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1] / 'src')})
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=30) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {'pdf2data_parse', 'pdf2data_status', 'pdf2data_read', 'pdf2data_graph'}
                started = await session.call_tool('pdf2data_parse', dict(source_path=source['path'], ocr='off'))
                assert not started.is_error
                run_id = started.structured_content['run_id']
                deadline = time.monotonic() + 25
                while True:
                    status = await session.call_tool('pdf2data_status', dict(run_id=run_id))
                    state = status.structured_content
                    if state['status'] not in ('queued', 'running'): break
                    if time.monotonic() > deadline: pytest.fail('MCP source run timed out')
                    await asyncio.sleep(.05)
                assert state['status'] in ('processed', 'processed_with_flags'), state
                result = await session.call_tool('pdf2data_read', dict(run_id=run_id, artifact='page', page=1, max_chars=200000))
                assert not result.is_error
                assert result.structured_content['data']['page']['page'] == 1
                context=await session.call_tool('pdf2data_read',dict(run_id=run_id,artifact='continuations',page=1,max_chars=200000))
                assert not context.is_error
                assert context.structured_content['data']['publication_eligible'] is False
                graph = await session.call_tool('pdf2data_graph', dict(run_id=run_id, operation='build'))
                assert not graph.is_error
                assert graph.structured_content['by_kind']['Page'] == 2
                assert graph.structured_content['semantic_review_required'] is True
    asyncio.run(exercise())
