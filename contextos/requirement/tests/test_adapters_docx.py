from __future__ import annotations

from contextos.requirement.adapters import get_adapter


def test_docx_paragraphs_and_table_linearized(make_docx):
    path = make_docx(
        paragraphs=["新增 Dynamic Charging 批量操作", "需支持 SMS 提醒"],
        table_rows=[["字段", "说明"], ["OFFER_ID", "套餐标识"], ["BULK_LIMIT", "批量上限"]],
    )
    adapter = get_adapter("docx")
    res = adapter(str(path))
    assert "Dynamic Charging" in res.raw_text
    assert "SMS 提醒" in res.raw_text
    # 表格行列线性化:同一行 cell 用 | 连,行内可见
    assert "OFFER_ID | 套餐标识" in res.raw_text
    assert "BULK_LIMIT | 批量上限" in res.raw_text
    assert res.open_questions == []


def test_docx_missing_file_yields_open_question():
    adapter = get_adapter("docx")
    res = adapter("/no/such/file.docx")
    assert res.raw_text == ""
    assert res.open_questions and "解析失败" in res.open_questions[0]


def test_docx_corrupt_file_yields_open_question(tmp_path):
    bad = tmp_path / "broken.docx"
    bad.write_bytes(b"this is not a real docx zip")
    adapter = get_adapter("docx")
    res = adapter(str(bad))
    assert res.raw_text == ""
    assert res.open_questions and "解析失败" in res.open_questions[0]


def test_docx_tracked_insertion_extracted(tmp_path):
    """tracked 插入(w:ins)文本要被抽出(批注澄清常以修订形式存在)。"""
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document()
    p = doc.add_paragraph("基础需求文本")
    # 手工注入一个 w:ins 包 w:r/w:t(模拟 track-changes 插入)
    ins = p._p.makeelement(qn("w:ins"), {})
    run = p._p.makeelement(qn("w:r"), {})
    t = p._p.makeelement(qn("w:t"), {})
    t.text = "审阅补充: Bulk 上限设为 1000"
    run.append(t)
    ins.append(run)
    p._p.append(ins)
    out = tmp_path / "tracked.docx"
    doc.save(out)

    adapter = get_adapter("docx")
    res = adapter(str(out))
    assert "基础需求文本" in res.raw_text
    assert "Bulk 上限设为 1000" in res.raw_text
