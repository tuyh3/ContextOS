def test_window_passage_basic():
    from contextos.recall.windowing import window_passage
    lines = [f"line{i}" for i in range(20)]
    # 命中第 10 行(1-based), radius=2 -> line8..line12(1-based 9..13 => idx 8..12)
    p = window_passage(lines, hit_lineno=10, radius=2)
    assert "line9" in p and "line10" in p and "line11" in p
    assert "line0" not in p


def test_window_passage_clamps_edges():
    from contextos.recall.windowing import window_passage
    lines = ["a", "b", "c"]
    p = window_passage(lines, hit_lineno=1, radius=5)
    assert p == "a\nb\nc"


def test_window_passage_empty():
    from contextos.recall.windowing import window_passage
    assert window_passage([], hit_lineno=1, radius=3) == ""
