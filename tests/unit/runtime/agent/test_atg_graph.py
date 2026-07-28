"""ATG graph data model — pure-logic tests (no LLM, no DB).

Validates TaskNode/TaskDAG construction, validation (cycles, dangling deps,
placeholder refs), topological wave scheduling, and RefinementHistory
lineage tracking.
"""
from __future__ import annotations

from app.runtime.agent.atg.graph import (
    MAX_NODES,
    PLACEHOLDER_RE,
    RefinementHistory,
    TaskDAG,
    TaskNode,
    find_placeholders,
    parse_placeholder,
)


def test_parse_placeholder_splits_on_last_dot() -> None:
    assert parse_placeholder("n1.result") == ("n1", "result")
    assert parse_placeholder("n1.2.result") == ("n1.2", "result")
    assert parse_placeholder("no_dot") is None
    assert parse_placeholder(".key") is None


def test_find_placeholders_in_nested_structures() -> None:
    template = {"path": "${n1.result}", "opts": ["${n2.result}", "literal"]}
    refs = find_placeholders(template)
    assert "n1.result" in refs
    assert "n2.result" in refs
    assert len(refs) == 2


def test_dag_validates_clean_graph() -> None:
    dag = TaskDAG("read and summarise")
    dag.add_node(TaskNode(id="n1", goal="read file", tool="read_file",
                          args_template={"path": "a.py"}, outputs=["result"]))
    dag.add_node(TaskNode(id="n2", goal="summarise", tool="web_search",
                          args_template={"query": "${n1.result}"},
                          outputs=["result"], deps=["n1"]))
    assert dag.validate() == []
    assert dag.is_executable()


def test_dag_detects_cycle() -> None:
    dag = TaskDAG("cyclic")
    dag.add_node(TaskNode(id="n1", goal="a", deps=["n2"]))
    dag.add_node(TaskNode(id="n2", goal="b", deps=["n1"]))
    errors = dag.validate()
    assert any("cycle" in e for e in errors)


def test_dag_detects_dangling_dep() -> None:
    dag = TaskDAG("dangling")
    dag.add_node(TaskNode(id="n1", goal="a", deps=["ghost"]))
    errors = dag.validate()
    assert any("unknown node 'ghost'" in e for e in errors)


def test_dag_detects_self_dependency() -> None:
    dag = TaskDAG("self")
    dag.add_node(TaskNode(id="n1", goal="a", deps=["n1"]))
    errors = dag.validate()
    assert any("depends on itself" in e for e in errors)


def test_dag_detects_placeholder_without_dep() -> None:
    dag = TaskDAG("bad placeholder")
    dag.add_node(TaskNode(id="n1", goal="read", tool="read_file",
                          args_template={"path": "a.py"}, outputs=["result"]))
    dag.add_node(TaskNode(id="n2", goal="use", tool="bash",
                          args_template={"command": "${n1.result}"},
                          outputs=["result"]))
    errors = dag.validate()
    assert any("not in deps" in e for e in errors)


def test_waves_topological_order() -> None:
    dag = TaskDAG("three waves")
    dag.add_node(TaskNode(id="n1", goal="a", outputs=["result"]))
    dag.add_node(TaskNode(id="n2", goal="b", outputs=["result"], deps=["n1"]))
    dag.add_node(TaskNode(id="n3", goal="c", outputs=["result"], deps=["n1"]))
    dag.add_node(TaskNode(id="n4", goal="d", outputs=["result"],
                          deps=["n2", "n3"]))
    waves = dag.waves()
    assert waves[0] == ["n1"]
    assert set(waves[1]) == {"n2", "n3"}
    assert waves[2] == ["n4"]


def test_waves_skip_terminal_nodes() -> None:
    dag = TaskDAG("resume")
    dag.add_node(TaskNode(id="n1", goal="done", status="done",
                          outputs=["result"], record={"output_excerpt": "ok"}))
    dag.add_node(TaskNode(id="n2", goal="pending", outputs=["result"],
                          deps=["n1"]))
    waves = dag.waves()
    assert waves == [["n2"]]


def test_refinement_history_lineage() -> None:
    dag1 = TaskDAG("root")
    dag1.add_node(TaskNode(id="n1", goal="coarse", tool=None, outputs=["result"]))
    hist = RefinementHistory()
    hist.record(None, dag1)

    dag2 = TaskDAG.from_dict(dag1.to_dict())
    dag2.add_node(TaskNode(id="n1.1", goal="refined", tool="read_file",
                           args_template={"path": "a.py"}, outputs=["result"],
                           parent_id="n1"))
    hist.record("n1", dag2)

    assert len(hist.entries) == 2
    pm = hist.parent_map()
    assert pm.get("n1.1") == "n1"
    chain = hist.ancestor_chain("n1.1")
    assert "n1" in chain


def test_dag_round_trips_through_json() -> None:
    dag = TaskDAG("round trip")
    dag.add_node(TaskNode(id="n1", goal="read", tool="read_file",
                          args_template={"path": "a.py"}, outputs=["result"],
                          status="done", attempts=1,
                          record={"output_excerpt": "content"}))
    d = dag.to_dict()
    restored = TaskDAG.from_dict(d)
    assert restored.root_goal == "round trip"
    assert restored.nodes["n1"].tool == "read_file"
    assert restored.nodes["n1"].status == "done"
    assert restored.nodes["n1"].record["output_excerpt"] == "content"


def test_node_record_result_truncates_long_output() -> None:
    node = TaskNode(id="n1", goal="big output")
    big = "x" * 5000
    node.record_result(output=big)
    excerpt = node.record["output_excerpt"]
    assert "truncated" in excerpt
    assert len(excerpt) < len(big)
