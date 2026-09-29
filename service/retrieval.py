import io
import os
import re
from pathlib import Path
from collections import defaultdict
from pypdf import PdfReader
from rank_bm25 import BM25Plus
from sqlalchemy import select
from .storage import Document, Chunk, uid
from .security import document_access

GLOSSARY = [("sulfur sulphur 황 황함유량", "sulphur"), ("탄소집약도 cii carbon intensity", "cii"),
            ("연료 fuel", "fuel"), ("배출량 emission emissions co2", "emission"), ("보고서 report", "report"),
            ("시정 corrective 개선", "corrective"), ("등급 rating", "rating"), ("배출규제해역 eca", "eca")]
STOP = set("what is are the how do a an of for to and our please tell me 알려줘 알려주세요 어떻게 규정 기준 무엇".split())

def tokens(text):
    text = text.lower()
    words = [w for w in re.findall(r"[a-z0-9]+|[가-힣]+", text) if w not in STOP]
    for group, concept in GLOSSARY:
        if any((w in text if re.search(r"[가-힣]", w) else re.search(r"\b" + w + r"\b", text)) for w in group.split()): words += [concept] * 2
    return words

def expanded(text): return text + " " + " ".join(tokens(text))

def pdf_sections(data):
    if len(data) > 10 * 1024 * 1024: raise ValueError("PDF는 10MB 이하여야 합니다.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted: raise ValueError("암호화된 PDF는 지원하지 않습니다.")
        if len(reader.pages) > 250: raise ValueError("PDF는 250페이지 이하여야 합니다.")
        sections = []
        for n, page in enumerate(reader.pages, 1):
            if '/Contents' not in page: continue
            text = page.extract_text(extraction_mode="layout") or ""
            if text.strip(): sections.append({"page": n, "section": f"Page {n}", "text": text})
        if not sections: raise ValueError("텍스트를 추출할 수 없습니다. 스캔 PDF는 OCR 후 등록해 주세요.")
        if sum(len(s["text"]) for s in sections) > 1_000_000: raise ValueError("추출 본문은 100만 자 이하여야 합니다.")
        return sections
    except ValueError: raise
    except Exception as exc: raise ValueError("PDF를 읽지 못했습니다. 파일을 확인해 주세요.") from exc

def chunk_sections(sections):
    result = []
    for s in sections:
        section, block = s.get("section", s.get("heading", "본문")), []
        def flush():
            if block: result.append({"id": uid(), "text": "\n".join(block), "page": s.get("page"), "section": section[:200]})
            block.clear()
        for line in s["text"].splitlines():
            if re.match(r"^(?:Regulation\s+\d+|제\s*\d+\s*조|\d+(?:\.\d+)+\s)", line.strip(), re.I):
                flush(); section = line.strip()[:200]
            if sum(map(len, block)) + len(line) > 1600: flush()
            for start in range(0, max(1, len(line)), 1600):
                block.append(line[start:start + 1600])
                if len(line) > 1600: flush()
        flush()
    return [r for r in result if r["text"].strip()]

class Search:
    def __init__(self, path=None, vector_enabled=None):
        self.path = path or os.getenv("CHROMA_PATH", "data/chroma")
        self.enabled = vector_enabled if vector_enabled is not None else os.getenv("VECTOR_ENABLED", "0") == "1"
        self.collection = None
        self.warning = None

    def vector(self):
        if not self.enabled: return None
        if self.collection is None:
            # The embedding model must be prepared explicitly, never downloaded in a query.
            from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
            embed = ONNXMiniLM_L6_V2()
            if not (Path(embed.DOWNLOAD_PATH) / "onnx" / "model.onnx").exists():
                raise RuntimeError("Run python -m service.manage prepare-vectors before enabling vectors")
            import chromadb
            self.collection = chromadb.PersistentClient(path=self.path).get_or_create_collection(
                "haedap-v1", embedding_function=embed, metadata={"hnsw:space": "cosine"})
        return self.collection

    def index(self, chunks):
        if not self.enabled: return "vector_disabled"
        try:
            collection = self.vector()
            for offset in range(0, len(chunks), 64):
                batch = chunks[offset:offset + 64]
                collection.upsert(ids=[c.id for c in batch], documents=[expanded(c.text) for c in batch],
                                  metadatas=[{"document_id": c.document_id} for c in batch])
            self.warning = None
            return "indexed"
        except Exception:
            self.warning = "VECTOR_UNAVAILABLE_BM25_ONLY"
            return self.warning

    def query(self, db, user, question, limit=5):
        docs = [d for d in db.scalars(select(Document).where(Document.active == True, Document.status == "current")) if document_access(user, d)]
        docmap = {d.id: d for d in docs}
        if not docs: return [], "bm25", []
        chunks = list(db.scalars(select(Chunk).where(Chunk.document_id.in_(docmap))))
        if not chunks: return [], "bm25", []
        corpus = [tokens(c.text + " " + c.section + " " + docmap[c.document_id].title) for c in chunks]
        q = tokens(question)
        if not q: return [], "bm25", []
        scores = BM25Plus(corpus).get_scores(q)
        lexical = sorted([i for i, terms in enumerate(corpus) if set(q) & set(terms)], key=lambda i: scores[i], reverse=True)[:30]
        ranks = defaultdict(float)
        for rank, i in enumerate(lexical): ranks[chunks[i].id] += 1 / (61 + rank)
        method, warnings = "bm25", []
        if self.enabled:
            try:
                collection = self.vector()
                found = collection.query(query_texts=[expanded(question)], n_results=min(30, len(chunks)),
                                         where={"document_id": {"$in": list(docmap)}}, include=["distances"])
                allowed_ids = {c.id for c in chunks}
                for rank, (cid, distance) in enumerate(zip(found["ids"][0], found["distances"][0])):
                    if cid in allowed_ids and distance <= .65: ranks[cid] += 1 / (61 + rank)
                method = "bm25+chroma-rrf"
            except Exception: warnings.append("VECTOR_UNAVAILABLE_BM25_ONLY")
        by_id = {c.id: c for c in chunks}
        results = []
        for cid in sorted(ranks, key=ranks.get, reverse=True)[:limit]:
            c = by_id[cid]; d = docmap[c.document_id]
            results.append({"id": c.id, "document_id": d.id, "text": c.text, "page": c.page, "section": c.section,
                            "title": d.title, "version": d.version, "metadata": d.metadata_json, "score": ranks[cid]})
        return results, method, warnings
