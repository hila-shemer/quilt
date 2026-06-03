from quilt import keys


def test_id_is_order_insensitive():
    a = keys.merge_point_id("treesha", ["p2", "p1"])
    b = keys.merge_point_id("treesha", ["p1", "p2"])
    assert a == b


def test_id_changes_with_base_and_members():
    a = keys.merge_point_id("tree1", ["p1"])
    assert a != keys.merge_point_id("tree2", ["p1"])
    assert a != keys.merge_point_id("tree1", ["p1", "p2"])
    assert len(a) == 64
