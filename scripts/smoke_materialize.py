"""手动 smoke: 物化一个真目录, 打印统计。

用法:
  uv run python scripts/smoke_materialize.py <source_dir> <out_dir> [paddle]
默认 backend=fake(快); 末参 'paddle' 用真实 OCR(需 .[ocr])。
"""
from __future__ import annotations

import sys
from pathlib import Path

from contextos.corpus.materialize import materialize_corpus
from contextos.corpus.ocr import make_ocr
from contextos.corpus.record_store import RecordStore
from contextos.profile.schema import OcrConfig, SourceConfig
from contextos.storage.db import make_engine


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    src_dir, out_dir = sys.argv[1], sys.argv[2]
    backend = "paddle" if len(sys.argv) > 3 and sys.argv[3] == "paddle" else "fake"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    store = RecordStore(make_engine(f"sqlite:///{out / 'materialization.db'}"))
    src = SourceConfig(
        type="dir",
        location=src_dir,
        glob=["**/*.md", "**/*.docx", "**/*.png"],
        leakage_exclude_regex=["change-log/"],
    )
    ocr = make_ocr(OcrConfig(backend=backend))
    stats = materialize_corpus(
        sources=[src], materialized_dir=out, store=store, ocr=ocr, backend_name=backend
    )
    print(f"backend={backend} stats={stats}")


if __name__ == "__main__":
    main()
