import asyncio

from term5.brain.scheduler import DAGScheduler
from term5.models import TaskNode, WorkerResult


def test_dag_dependencies():
    async def go():
        sched = DAGScheduler(4)
        nodes = [TaskNode("a", "A"), TaskNode("b", "B"), TaskNode("c", "C", dependencies={"a", "b"})]
        order = []
        async def execute(node, results):
            if node.id == "c":
                assert {"a", "b"} <= results.keys()
            await asyncio.sleep(0.001)
            order.append(node.id)
            return WorkerResult(node.id, "success", node.objective)
        result = await sched.run(nodes, execute)
        assert set(result) == {"a", "b", "c"}
        assert order[-1] == "c"
    asyncio.run(go())
