import copy

from jupyterlab_live_content import nb_hash


def _nb(cells, metadata=None):
    return {
        "cells": cells,
        "metadata": metadata or {"kernelspec": {"name": "python3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _code(cid, source, metadata=None):
    return {
        "id": cid,
        "cell_type": "code",
        "source": source,
        "metadata": metadata or {},
        "outputs": [],
        "execution_count": None,
    }


def test_source_hash_ignores_line_endings():
    assert nb_hash.source_hash("a\r\nb\rc") == nb_hash.source_hash("a\nb\nc")


def test_source_hash_accepts_list_or_string():
    assert nb_hash.source_hash(["a\n", "b"]) == nb_hash.source_hash("a\nb")


def test_meta_hash_excludes_volatile_keys():
    a = nb_hash.meta_hash({"tags": ["x"], "collapsed": True, "scrolled": False})
    b = nb_hash.meta_hash({"tags": ["x"]})
    assert a == b


def test_meta_hash_excludes_nested_jupyter_view_state():
    a = nb_hash.meta_hash({"jupyter": {"source_hidden": True}})
    b = nb_hash.meta_hash({})
    assert a == b


def test_meta_hash_detects_real_metadata_change():
    a = nb_hash.meta_hash({"tags": ["x"]})
    b = nb_hash.meta_hash({"tags": ["y"]})
    assert a != b


def test_meta_hash_includes_attachments():
    a = nb_hash.meta_hash({}, {"img.png": {"image/png": "AAAA"}})
    b = nb_hash.meta_hash({}, {"img.png": {"image/png": "BBBB"}})
    assert a != b


def test_outputs_do_not_affect_manifest():
    base = _nb([_code("c1", "print(1)")])
    with_out = copy.deepcopy(base)
    with_out["cells"][0]["outputs"] = [
        {"output_type": "stream", "name": "stdout", "text": "1\n"}
    ]
    with_out["cells"][0]["execution_count"] = 1
    m1 = nb_hash.build_manifest(base)
    m2 = nb_hash.build_manifest(with_out)
    assert m1.cells_by_id["c1"].source_hash == m2.cells_by_id["c1"].source_hash
    assert nb_hash.diff_manifests(m1, m2).is_empty


def test_diff_detects_source_change():
    m1 = nb_hash.build_manifest(_nb([_code("c1", "print(1)"), _code("c2", "x = 2")]))
    m2 = nb_hash.build_manifest(_nb([_code("c1", "print(1)"), _code("c2", "x = 3")]))
    diff = nb_hash.diff_manifests(m1, m2)
    assert diff.changed == ["c2"]
    assert diff.removed == []
    assert not diff.order_changed


def test_diff_detects_insert_delete_and_order():
    m1 = nb_hash.build_manifest(_nb([_code("a", "1"), _code("b", "2"), _code("c", "3")]))
    m2 = nb_hash.build_manifest(_nb([_code("a", "1"), _code("x", "9"), _code("c", "3")]))
    diff = nb_hash.diff_manifests(m1, m2)
    assert "x" in diff.changed  # inserted id shows up as "changed" (new)
    assert diff.removed == ["b"]
    assert diff.order_changed


def test_diff_detects_notebook_metadata_change():
    m1 = nb_hash.build_manifest(_nb([_code("a", "1")], {"kernelspec": {"name": "python3"}}))
    m2 = nb_hash.build_manifest(_nb([_code("a", "1")], {"kernelspec": {"name": "xpython"}}))
    diff = nb_hash.diff_manifests(m1, m2)
    assert diff.nb_meta_changed
    assert diff.changed == []


def test_reorder_only_flags_order_not_content():
    m1 = nb_hash.build_manifest(_nb([_code("a", "1"), _code("b", "2")]))
    m2 = nb_hash.build_manifest(_nb([_code("b", "2"), _code("a", "1")]))
    diff = nb_hash.diff_manifests(m1, m2)
    assert diff.changed == []
    assert diff.removed == []
    assert diff.order_changed
