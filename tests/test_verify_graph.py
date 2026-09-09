from roadmapify import verify


def test_qualified_graph_symbol_is_weak_context_not_completion(tmp_path):
    graph = {"nodes": [{"id": "node1", "qualified_name": "module.func"}], "links": []}
    result = verify.resolve_spec("symbol:module.func", tmp_path, code_graph=graph)
    assert result.verdict == "present" and result.weak
    assert "freshness unknown" in result.detail
    assert verify.resolve_spec("symbol:func", tmp_path, code_graph=graph).verdict == "unverifiable"
