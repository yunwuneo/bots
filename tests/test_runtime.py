import asyncio
import json
import time
from pathlib import Path

import pytest

from neobots.config import Config
from neobots.runtime import Runtime
from neobots.store import Store


def call(name, args, call_id="call_1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class ScriptedRuntime(Runtime):
    def __init__(self, store, config, responses):
        super().__init__(store, config)
        self.responses = iter(responses)
        self.executed = []
        self.requests = []

    async def complete(self, messages):
        self.requests.append(json.loads(json.dumps(messages)))
        return next(self.responses)

    async def computer(self, task, name, args):
        self.executed.append((name, args))
        return {"exit_code": 0, "output": "done"}


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    bot = store.create_bot("Tester", "Complete tests")
    config = Config(data_dir=tmp_path)
    yield store, bot, config
    store.close()


def test_approval_survives_worker_restart_and_runs_exactly_once(setup):
    store, bot, config = setup
    task = store.create_task(bot["id"], "Make a report")

    async def scenario():
        first = ScriptedRuntime(store, config, [{"role": "assistant", "content": None, "tool_calls": [call("run_command", {"command": "printf done"})]}])
        await first.run(task["id"])
        assert store.task(task["id"])["status"] == "awaiting_approval"
        assert first.executed == []
        await first.close()
        store.execute("UPDATE approvals SET decision='approved'")
        second = ScriptedRuntime(store, config, [{"role": "assistant", "content": "Finished"}])
        await second.run(task["id"])
        assert second.executed == [("run_command", {"command": "printf done"})]
        assert second.requests[0][-1]["role"] == "tool"
        assert store.task(task["id"])["status"] == "completed"
        assert len(store.all("SELECT * FROM messages WHERE role='assistant'")) == 1
        await second.close()
    asyncio.run(scenario())


def test_denial_is_returned_to_model_without_execution(setup):
    store, bot, config = setup
    task = store.create_task(bot["id"], "Run command")
    async def scenario():
        runtime = ScriptedRuntime(store, config, [
            {"role": "assistant", "content": None, "tool_calls": [call("run_command", {"command": "echo test"})]},
            {"role": "assistant", "content": "I did not execute the denied command."}])
        await runtime.run(task["id"])
        store.execute("UPDATE approvals SET decision='denied'")
        await runtime.run(task["id"])
        assert not runtime.executed
        assert "denied" in runtime.requests[-1][-1]["content"]
        await runtime.close()
    asyncio.run(scenario())


def test_multiple_calls_do_not_repeat_completed_tool_after_approval(setup):
    store, bot, config = setup
    task = store.create_task(bot["id"], "Read then execute")
    async def scenario():
        runtime = ScriptedRuntime(store, config, [
            {"role":"assistant", "content":None, "tool_calls":[call("read_file", {"path":"in.txt"}, "read"), call("run_command", {"command":"echo done"}, "write")]},
            {"role":"assistant", "content":"Done"}])
        await runtime.run(task["id"])
        assert [name for name, _ in runtime.executed] == ["read_file"]
        store.execute("UPDATE approvals SET decision='approved'")
        await runtime.run(task["id"])
        assert [name for name, _ in runtime.executed] == ["read_file", "run_command"]
        await runtime.close()
    asyncio.run(scenario())


def test_interrupted_side_effect_is_not_replayed(setup):
    store, bot, config = setup
    task = store.create_task(bot["id"], "Execute")
    store.save_state(task["id"], {"messages":[{"role":"user","content":"Execute"},{"role":"assistant","content":None,"tool_calls":[call("run_command", {"command":"echo side-effect"})]}],"steps":1,"inflight":"call_1"})
    store.status(task["id"], "running")
    store.recover()
    assert store.task(task["id"])["status"] == "interrupted"
    async def scenario():
        runtime=ScriptedRuntime(store,config,[{"role":"assistant","content":"Checking the previous result is required."}])
        runtime.resume(task["id"])
        await runtime.run(task["id"])
        assert not runtime.executed
        assert "outcome is unknown" in runtime.requests[0][-1]["content"]
        await runtime.close()
    asyncio.run(scenario())


def test_delegation_returns_child_result_and_rejects_cycles(setup):
    store, bot, config = setup
    specialist=store.create_bot("Specialist", "Research")
    parent=store.create_task(bot["id"],"Research a topic")
    async def scenario():
        runtime=ScriptedRuntime(store,config,[
            {"role":"assistant","content":None,"tool_calls":[call("delegate", {"bot_id":specialist["id"],"task":"Research"})]},
            {"role":"assistant","content":"Research result"},
            {"role":"assistant","content":"Here is the result from my teammate."}])
        await runtime.run(parent["id"])
        assert store.task(parent["id"])["status"]=="waiting_bot"
        child=store.all("SELECT * FROM tasks WHERE parent_id=?", (parent["id"],))[0]
        result=await runtime.dispatch(store.task(child["id"]),call("delegate",{}),"delegate",{"bot_id":bot["id"],"task":"Cycle"})
        assert "cycle" in result["error"]
        await runtime.run(child["id"])
        await runtime.run(parent["id"])
        assert store.task(parent["id"])["status"]=="completed"
        assert "Research result" in runtime.requests[-1][-1]["content"]
        assert len(store.all("SELECT * FROM tasks"))==2
        await runtime.close()
    asyncio.run(scenario())


def test_scheduler_coalesces_missed_runs_and_prevents_overlap(setup):
    store, bot, config = setup
    now=time.time()
    store.execute("INSERT INTO routines(id,bot_id,name,prompt,interval_minutes,next_run,created) VALUES('routine',?,'Daily','Research',5,?,?)",(bot["id"],now-9000,now))
    async def scenario():
        runtime=ScriptedRuntime(store,config,[])
        runtime.schedule()
        runtime.schedule()
        assert len(store.all("SELECT * FROM tasks"))==1
        store.execute("UPDATE routines SET next_run=?",(now-1,))
        runtime.schedule()
        assert len(store.all("SELECT * FROM tasks"))==1
        task=store.all("SELECT * FROM tasks")[0]
        store.status(task["id"],"completed")
        store.execute("UPDATE routines SET next_run=?",(now-1,))
        runtime.schedule()
        assert len(store.all("SELECT * FROM tasks"))==2
        await runtime.close()
    asyncio.run(scenario())


def test_approval_cannot_be_reused_with_different_arguments(setup):
    store, bot, config=setup
    task=store.create_task(bot["id"],"Execute")
    store.execute("INSERT INTO approvals VALUES('a',?,'same','run_command',?,'approved',?)",(task["id"],json.dumps({"command":"echo safe"}),time.time()))
    async def scenario():
        runtime=ScriptedRuntime(store,config,[])
        with pytest.raises(RuntimeError,match="审批参数"):
            runtime.approve(task,call("run_command",{},"same"),"run_command",{"command":"echo changed"})
        await runtime.close()
    asyncio.run(scenario())


def test_completed_model_response_recovers_without_another_model_call(setup):
    store, bot, config=setup
    task=store.create_task(bot["id"],"Finish")
    store.save_state(task["id"],{"messages":[{"role":"user","content":"Finish"},{"role":"assistant","content":"Already finished"}],"steps":1})
    async def scenario():
        runtime=ScriptedRuntime(store,config,[])
        await runtime.run(task["id"])
        assert store.task(task["id"])["result"]=="Already finished"
        assert not runtime.requests
        await runtime.close()
    asyncio.run(scenario())
