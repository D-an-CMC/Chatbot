# src/llm/response_builder.py
# Xu ly va dinh dang phan tra loi cuoi cung (diem so / danh sach lop / thoi khoa bieu)

import logging
import re
from dataclasses import dataclass, field
from typing import List

from config import LOG_FORMAT, LOG_LEVEL
from src.grades.grade_store import GradeRecord

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_think_tags(text: str) -> str:
    """Loai bo chain-of-thought (<think>...</think>) ma mot so model reasoning
    (vd qwen3 tren Groq) chen vao dau cau tra loi."""
    if "<think>" not in text.lower():
        return text
    cleaned = _THINK_TAG_RE.sub("", text)
    # Phong truong hop stream bi cat giua chung, chua co the dong </think>
    cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


@dataclass
class FinalResponse:
    answer_text: str
    citations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def grade_citation_lines(records: List[GradeRecord]) -> List[str]:
    """Dung cho intent tra cuu diem: 1 dong cho moi (nam hoc, lop, mon, hoc ky) khac nhau."""
    seen = set()
    lines: List[str] = []
    for r in records:
        key = (r.school_year, r.class_name, r.subject, r.semester)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"Lớp {r.class_name} - {r.subject} - Học kỳ {r.semester} - Năm học {r.school_year}")
    return lines


def _build_citation_text(citations: List[str]) -> str:
    if not citations:
        return ""
    lines = ["\n---\n📎 Nguồn:"]
    for i, c in enumerate(citations, start=1):
        lines.append(f"[{i}] {c}")
    return "\n".join(lines)


def build_final_response(
    llm_answer: str,
    citations: List[str],
    has_data: bool,
    no_data_warning: str = (
        "Không tìm thấy dữ liệu phù hợp. Vui lòng cung cấp thêm thông tin (tên đầy đủ, "
        "lớp, năm học, học kỳ) để tra cứu chính xác hơn."
    ),
    question: str = "",
    suppress_no_data_warning: bool = False,
) -> FinalResponse:
    llm_answer = strip_think_tags(llm_answer)

    warnings: List[str] = []
    if not has_data and not suppress_no_data_warning:
        warnings.append(no_data_warning)

    answer_parts = [llm_answer]
    citation_block = _build_citation_text(citations)
    if citation_block:
        answer_parts.append(citation_block)

    full_answer = "\n".join(answer_parts)

    logger.info(
        "Response built: %d ky tu, %d citations, %d warnings",
        len(full_answer), len(citations), len(warnings),
    )

    return FinalResponse(
        answer_text=full_answer,
        citations=citations,
        warnings=warnings,
        metadata={"question": question, "has_data": has_data},
    )


def format_for_display(response: FinalResponse) -> str:
    return response.answer_text
