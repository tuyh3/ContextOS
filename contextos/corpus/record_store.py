"""物化记录库(content-hash 缓存)。红线#6: 走 SQLAlchemy 抽象, 非裸 SQLite。

只为省 OCR/物化成本: doc 没变(content_hash 一致)就跳过重物化。
与被砍掉的 embedding 向量增量是两码事, 更简单。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class _Base(DeclarativeBase):
    pass


class MaterializationRecord(_Base):
    __tablename__ = "materialization_record"
    doc_id: Mapped[str] = mapped_column(String, primary_key=True)  # = 源相对路径
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    sidecar_path: Mapped[str] = mapped_column(String, nullable=False)
    ocr_backend: Mapped[str] = mapped_column(String, nullable=False)
    materialized_at: Mapped[str] = mapped_column(String, nullable=False)


class RecordStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        from contextos.storage.migrate import ensure_schema
        ensure_schema(engine, _Base.metadata)   # 建表 + 跨版本附加式补列(见 storage/migrate.py)

    def get(self, doc_id: str) -> MaterializationRecord | None:
        with Session(self._engine) as s:
            return s.get(MaterializationRecord, doc_id)

    def get_hash(self, doc_id: str) -> str | None:
        rec = self.get(doc_id)
        return rec.content_hash if rec else None

    def upsert(
        self, doc_id: str, content_hash: str, sidecar_path: str, ocr_backend: str
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with Session(self._engine) as s:
            rec = s.get(MaterializationRecord, doc_id)
            if rec is None:
                s.add(
                    MaterializationRecord(
                        doc_id=doc_id,
                        content_hash=content_hash,
                        sidecar_path=sidecar_path,
                        ocr_backend=ocr_backend,
                        materialized_at=now,
                    )
                )
            else:
                rec.content_hash = content_hash
                rec.sidecar_path = sidecar_path
                rec.ocr_backend = ocr_backend
                rec.materialized_at = now
            s.commit()

    def all_doc_ids(self) -> set[str]:
        with Session(self._engine) as s:
            return set(s.scalars(select(MaterializationRecord.doc_id)).all())

    def delete(self, doc_id: str) -> None:
        with Session(self._engine) as s:
            rec = s.get(MaterializationRecord, doc_id)
            if rec is not None:
                s.delete(rec)
                s.commit()
