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
        bounded = bridge.read(run['run_id'], page=1, max_chars=1000)
        assert bounded['status'] == 'requires_scope' and 'data' not in bounded
        artifact = bridge.workspace.directory(run['run_id']) / 'readable/document.md'
        artifact.write_text('Changed output')
        with pytest.raises(ValueError, match='changed'):
            bridge.read(run['run_id'], artifact='markdown')
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
                assert {t.name for t in tools.tools} == {'pdf2data_parse', 'pdf2data_status', 'pdf2data_read'}
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
    asyncio.run(exercise())
