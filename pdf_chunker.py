"""
=============================================================
PDF Chunker for Claude Project Knowledge
=============================================================
대용량 PDF 파일을 Claude 프로젝트의 Knowledge에 업로드할 수 있도록
적절한 크기의 .txt 청크 파일로 분할하는 도구입니다.

사용법:
    python pdf_chunker.py input.pdf [옵션]

옵션:
    --output-dir, -o    출력 디렉토리 (기본값: ./chunks_<파일명>)
    --max-tokens, -t    청크당 최대 토큰 수 (기본값: 4000)
    --overlap, -v       청크 간 겹치는 문장 수 (기본값: 3)
    --encoding, -e      텍스트 인코딩 (기본값: utf-8)

예시:
    python pdf_chunker.py my_document.pdf
    python pdf_chunker.py my_document.pdf -o ./output -t 3000
    python pdf_chunker.py my_document.pdf --max-tokens 5000 --overlap 5

필요한 패키지:
    pip install pdfplumber tiktoken
=============================================================
"""

import os
import re
import sys
import argparse
import datetime
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    print("❌ pdfplumber가 설치되어 있지 않습니다.")
    print("   다음 명령어로 설치하세요: pip install pdfplumber")
    sys.exit(1)

try:
    import tiktoken
except ImportError:
    print("❌ tiktoken이 설치되어 있지 않습니다.")
    print("   다음 명령어로 설치하세요: pip install tiktoken")
    sys.exit(1)


# ─────────────────────────────────────────────
# 토큰 카운팅
# ─────────────────────────────────────────────
def get_tokenizer():
    """Claude 모델과 호환되는 토크나이저를 로드합니다."""
    # cl100k_base는 Claude/GPT-4 계열과 유사한 토큰 수를 제공합니다.
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, tokenizer) -> int:
    """텍스트의 토큰 수를 반환합니다."""
    return len(tokenizer.encode(text))


# ─────────────────────────────────────────────
# PDF 텍스트 추출
# ─────────────────────────────────────────────
def extract_text_from_pdf(pdf_path: str) -> list[dict]:
    """
    PDF에서 페이지별 텍스트를 추출합니다.
    반환: [{"page": 1, "text": "..."}, ...]
    """
    pages = []
    print(f"📄 PDF 열기: {pdf_path}")

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        print(f"   총 {total} 페이지 감지")

        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""

            # 테이블이 있으면 테이블도 추출하여 보완
            tables = page.extract_tables()
            table_text = ""
            if tables:
                for table in tables:
                    if table:
                        for row in table:
                            cleaned_row = [str(cell).strip() if cell else "" for cell in row]
                            table_text += " | ".join(cleaned_row) + "\n"
                        table_text += "\n"

            # 테이블 텍스트가 본문에 없으면 추가
            combined = text
            if table_text and table_text.strip() not in text:
                combined = text + "\n\n[표 데이터]\n" + table_text

            pages.append({
                "page": i + 1,
                "text": combined.strip()
            })

            # 진행률 표시
            if (i + 1) % 100 == 0 or (i + 1) == total:
                pct = (i + 1) / total * 100
                print(f"   추출 진행: {i + 1}/{total} ({pct:.1f}%)")

    return pages


# ─────────────────────────────────────────────
# 논리적 섹션 감지
# ─────────────────────────────────────────────
HEADING_PATTERNS = [
    # 한국어 및 일반적 제목 패턴
    re.compile(r"^(제\s*\d+\s*(장|절|조|편|항|부|관))", re.MULTILINE),
    re.compile(r"^(Chapter\s+\d+)", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^(Section\s+\d+)", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^(Part\s+[IVXLCDM\d]+)", re.MULTILINE | re.IGNORECASE),
    # 번호 매겨진 제목
    re.compile(r"^(\d+\.(?:\d+\.)*\s+[A-Z가-힣])", re.MULTILINE),
    # 대문자로만 된 줄 (영문 제목)
    re.compile(r"^([A-Z][A-Z\s]{10,})$", re.MULTILINE),
]


def detect_sections(text: str) -> list[str]:
    """
    텍스트에서 논리적 섹션 구분점을 찾아 섹션 제목을 반환합니다.
    """
    sections = []
    for pattern in HEADING_PATTERNS:
        for match in pattern.finditer(text):
            sections.append(match.group(0).strip())
    return sections


# ─────────────────────────────────────────────
# 스마트 청킹
# ─────────────────────────────────────────────
def split_into_sentences(text: str) -> list[str]:
    """텍스트를 문장 단위로 분할합니다."""
    # 줄바꿈으로 먼저 분할한 뒤, 빈 줄이 아닌 것만 유지
    lines = text.split("\n")
    sentences = []
    for line in lines:
        line = line.strip()
        if line:
            sentences.append(line)
    return sentences


def create_chunks(
    pages: list[dict],
    max_tokens: int = 4000,
    overlap_sentences: int = 3,
    tokenizer=None
) -> list[dict]:
    """
    페이지별 텍스트를 토큰 제한에 맞춰 청크로 분할합니다.

    특징:
    - 페이지 경계를 존중하면서도 작은 페이지는 합침
    - 섹션 제목이 감지되면 해당 지점에서 청크를 나눔
    - 청크 간 약간의 오버랩으로 문맥 연결 유지
    - 각 청크에 출처 페이지 범위를 기록
    """
    if tokenizer is None:
        tokenizer = get_tokenizer()

    chunks = []
    current_lines = []
    current_tokens = 0
    current_page_start = pages[0]["page"] if pages else 1
    current_page_end = current_page_start
    current_headings = []

    # 헤더 예산: 각 청크 상단에 들어갈 메타정보용
    header_budget = 100  # 토큰
    effective_max = max_tokens - header_budget

    def flush_chunk():
        nonlocal current_lines, current_tokens, current_page_start, current_page_end, current_headings

        if not current_lines:
            return

        body = "\n".join(current_lines)

        # 청크 헤더 구성
        header = f"[페이지 {current_page_start}-{current_page_end}]"
        if current_headings:
            header += f" | 섹션: {', '.join(current_headings[:3])}"

        full_text = header + "\n" + "=" * 50 + "\n\n" + body

        # 요약을 위한 미리보기 (첫 200자)
        preview = body[:200].replace("\n", " ").strip()
        if len(body) > 200:
            preview += "..."

        chunks.append({
            "text": full_text,
            "page_start": current_page_start,
            "page_end": current_page_end,
            "headings": list(current_headings),
            "preview": preview,
            "tokens": count_tokens(full_text, tokenizer),
        })

        # 오버랩 처리: 마지막 N개 문장을 다음 청크로 이월
        overlap_lines = current_lines[-overlap_sentences:] if overlap_sentences > 0 else []

        current_lines = list(overlap_lines)
        current_tokens = count_tokens("\n".join(current_lines), tokenizer) if current_lines else 0
        current_headings = []

    for page_data in pages:
        page_num = page_data["page"]
        text = page_data["text"]

        if not text.strip():
            continue

        # 섹션 제목 감지
        headings = detect_sections(text)
        if headings:
            current_headings.extend(headings)

        sentences = split_into_sentences(text)

        for sentence in sentences:
            sentence_tokens = count_tokens(sentence, tokenizer)

            # 이 문장을 추가하면 제한 초과하는지 확인
            if current_tokens + sentence_tokens > effective_max and current_lines:
                # 새 섹션 시작이면 여기서 자르기
                is_heading = any(p.match(sentence) for p in HEADING_PATTERNS)
                flush_chunk()
                current_page_start = page_num
                if is_heading:
                    current_headings = [sentence[:80]]

            current_lines.append(sentence)
            current_tokens += sentence_tokens
            current_page_end = page_num

    # 마지막 청크 처리
    flush_chunk()

    return chunks


# ─────────────────────────────────────────────
# 파일 출력
# ─────────────────────────────────────────────
def save_chunks(chunks: list[dict], output_dir: str, pdf_name: str):
    """청크를 .txt 파일로 저장하고 readme.txt 목차를 생성합니다."""

    os.makedirs(output_dir, exist_ok=True)

    total_chunks = len(chunks)
    pad = len(str(total_chunks))

    # ── 개별 청크 파일 저장 ──
    filenames = []
    for i, chunk in enumerate(chunks):
        num = str(i + 1).zfill(pad)
        filename = f"chunk_{num}.txt"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(chunk["text"])

        filenames.append(filename)
        print(f"   ✅ {filename} ({chunk['tokens']} 토큰, 페이지 {chunk['page_start']}-{chunk['page_end']})")

    # ── readme.txt (목차) 생성 ──
    readme_path = os.path.join(output_dir, "readme.txt")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write("=" * 60 + "\n")
        f.write(f"  문서 목차 (Table of Contents)\n")
        f.write(f"  원본: {pdf_name}\n")
        f.write(f"  생성일: {now}\n")
        f.write(f"  총 청크 수: {total_chunks}\n")
        f.write("=" * 60 + "\n\n")

        f.write("이 디렉토리는 대용량 PDF 문서를 Claude가 처리할 수 있도록\n")
        f.write("분할한 텍스트 청크들을 담고 있습니다.\n\n")
        f.write("사용법:\n")
        f.write("  - 특정 내용을 찾으려면 아래 목차에서 페이지 범위와\n")
        f.write("    미리보기를 참고하여 해당 청크 파일을 참조하세요.\n")
        f.write("  - 각 청크 파일 상단에 [페이지 범위]와 [섹션명]이\n")
        f.write("    표시되어 있습니다.\n\n")

        f.write("-" * 60 + "\n")
        f.write("  목차\n")
        f.write("-" * 60 + "\n\n")

        for i, chunk in enumerate(chunks):
            num = str(i + 1).zfill(pad)
            f.write(f"📄 chunk_{num}.txt\n")
            f.write(f"   페이지: {chunk['page_start']}-{chunk['page_end']}\n")
            f.write(f"   토큰: {chunk['tokens']}\n")
            if chunk["headings"]:
                headings_str = ", ".join(chunk["headings"][:3])
                f.write(f"   섹션: {headings_str}\n")
            f.write(f"   미리보기: {chunk['preview']}\n")
            f.write("\n")

        # ── 통계 요약 ──
        total_tokens = sum(c["tokens"] for c in chunks)
        avg_tokens = total_tokens // total_chunks if total_chunks else 0
        total_pages = chunks[-1]["page_end"] if chunks else 0

        f.write("=" * 60 + "\n")
        f.write("  통계\n")
        f.write("=" * 60 + "\n")
        f.write(f"  총 페이지: {total_pages}\n")
        f.write(f"  총 청크: {total_chunks}\n")
        f.write(f"  총 토큰: {total_tokens:,}\n")
        f.write(f"  평균 토큰/청크: {avg_tokens:,}\n")

        # Claude 프로젝트 Knowledge 용량 안내
        f.write("\n")
        f.write("-" * 60 + "\n")
        f.write("  ⚠️  Claude 프로젝트 Knowledge 업로드 참고사항\n")
        f.write("-" * 60 + "\n")
        f.write("  - 프로젝트 Knowledge의 컨텍스트 윈도우 한도를 확인하세요.\n")
        f.write("  - 모든 청크를 한번에 넣기 어려울 수 있습니다.\n")
        f.write("  - 필요한 섹션만 선별적으로 업로드하는 것을 권장합니다.\n")
        f.write("  - 또는 MCP FileServer를 사용하여 필요 시에만\n")
        f.write("    참조하는 방식도 고려해 보세요.\n")

    print(f"\n   📋 readme.txt 목차 생성 완료")
    print(f"\n{'=' * 50}")
    print(f"  ✅ 완료! {total_chunks}개 청크 생성")
    print(f"  📁 출력 디렉토리: {output_dir}")
    print(f"  📊 총 토큰: {total_tokens:,}")
    print(f"{'=' * 50}")

    return filenames


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="대용량 PDF를 Claude 프로젝트 Knowledge용 텍스트 청크로 변환",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python pdf_chunker.py my_book.pdf
  python pdf_chunker.py report.pdf -o ./output -t 3000
  python pdf_chunker.py manual.pdf --max-tokens 5000 --overlap 5

출력물:
  - chunk_001.txt, chunk_002.txt, ...  (분할된 텍스트 파일)
  - readme.txt                          (목차 및 인덱스)
        """
    )
    parser.add_argument("pdf_path", help="변환할 PDF 파일 경로")
    parser.add_argument(
        "-o", "--output-dir",
        help="출력 디렉토리 (기본값: ./chunks_<파일명>)",
        default=None
    )
    parser.add_argument(
        "-t", "--max-tokens",
        help="청크당 최대 토큰 수 (기본값: 4000)",
        type=int,
        default=4000
    )
    parser.add_argument(
        "-v", "--overlap",
        help="청크 간 겹치는 문장 수 (기본값: 3)",
        type=int,
        default=3
    )
    parser.add_argument(
        "-e", "--encoding",
        help="텍스트 인코딩 (기본값: utf-8)",
        default="utf-8"
    )

    args = parser.parse_args()

    # 입력 파일 확인
    pdf_path = args.pdf_path
    if not os.path.exists(pdf_path):
        print(f"❌ 파일을 찾을 수 없습니다: {pdf_path}")
        sys.exit(1)

    pdf_name = Path(pdf_path).stem

    # 출력 디렉토리 설정
    output_dir = args.output_dir or f"./chunks_{pdf_name}"

    print()
    print("=" * 50)
    print("  📚 PDF Chunker for Claude")
    print("=" * 50)
    print(f"  입력: {pdf_path}")
    print(f"  출력: {output_dir}")
    print(f"  최대 토큰/청크: {args.max_tokens}")
    print(f"  오버랩 문장: {args.overlap}")
    print("=" * 50)
    print()

    # Step 1: 텍스트 추출
    print("📖 Step 1: PDF에서 텍스트 추출 중...")
    pages = extract_text_from_pdf(pdf_path)

    empty_pages = sum(1 for p in pages if not p["text"].strip())
    print(f"   ✅ {len(pages)}페이지 추출 완료 (빈 페이지: {empty_pages}개)")
    if empty_pages > len(pages) * 0.5:
        print("   ⚠️  빈 페이지가 많습니다. 스캔된 PDF일 수 있습니다.")
        print("      스캔 PDF는 OCR 처리가 필요합니다 (pytesseract + pdf2image).")
    print()

    # Step 2: 토크나이저 로드
    print("🔤 Step 2: 토크나이저 로드 중...")
    tokenizer = get_tokenizer()
    total_tokens = sum(count_tokens(p["text"], tokenizer) for p in pages)
    print(f"   ✅ 전체 문서 토큰 수: {total_tokens:,}")
    estimated_chunks = max(1, total_tokens // args.max_tokens)
    print(f"   📊 예상 청크 수: 약 {estimated_chunks}개")
    print()

    # Step 3: 스마트 청킹
    print("✂️  Step 3: 스마트 청킹 진행 중...")
    chunks = create_chunks(
        pages,
        max_tokens=args.max_tokens,
        overlap_sentences=args.overlap,
        tokenizer=tokenizer
    )
    print(f"   ✅ {len(chunks)}개 청크 생성 완료")
    print()

    # Step 4: 파일 저장
    print("💾 Step 4: 파일 저장 중...")
    save_chunks(chunks, output_dir, pdf_name)


if __name__ == "__main__":
    main()
