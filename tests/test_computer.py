import asyncio

import pytest

from neobots.computer import Computer, Execution, workspace_path


def test_workspace_rejects_escape_and_symlink(tmp_path):
    workspace=tmp_path/'workspace'
    workspace.mkdir()
    outside=tmp_path/'outside'
    outside.mkdir()
    (workspace/'link').symlink_to(outside)
    for path in ['../outside/file','link/file','/etc/passwd']:
        with pytest.raises(ValueError):
            workspace_path(workspace,path)


def test_artifacts_are_shared_and_persistent(tmp_path):
    async def scenario():
        computer=Computer(tmp_path/'workspace',tmp_path/'profile')
        await computer.execute(Execution(task_id='a'*32,bot_id='b'*32,tool='write_file',arguments={'path':'reports/result.md','content':'Research result'}))
        another=Computer(tmp_path/'workspace',tmp_path/'profile')
        result=await another.execute(Execution(task_id='c'*32,bot_id='d'*32,tool='read_file',arguments={'path':'reports/result.md'}))
        assert result['content']=='Research result'
    asyncio.run(scenario())
