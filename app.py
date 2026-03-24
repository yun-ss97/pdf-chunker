import os
import re
import io
import json
import datetime
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

try:
    import pdfplumber
except ImportError:
    st.error("pdfplumber가 설치되어 있지 않습니다. `pip install pdfplumber` 실행 후 다시 시도하세요.")
    st.stop()

try:
    import tiktoken
except ImportError:
    st.error("tiktoken이 설치되어 있지 않습니다. `pip install tiktoken` 실행 후 다시 시도하세요.")
    st.stop()


# ─────────────────────────────────────────────
# 기본 설정
# ─────────────────────────────────────────────
st.set_page_config(page_title="PDF Chunker", layout="wide")
st.title("📚 PDF Chunker for Claude")
st.caption("PDF를 토큰 기준으로 청킹하고, 결과를 ZIP으로 다운로드합니다.")

# 세션 상태 초기화
if "zip_bytes" not in st.session_state:
    st.session_state.zip_bytes = None
if "zip_filename" not in st.session_state:
    st.session_state.zip_filename = None
if "result_summary" not in st.session_state:
    st.session_state.result_summary = None
if "chunk_previews" not in st.session_state:
    st.session_state.chunk_previews = None


# ─────────────────────────────────────────────
# 토큰 카운팅
# ─────────────────────────────────────────────
def get_tokenizer():
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, tokenizer) -> int:
    return len(tokenizer.encode(text))


# ─────────────────────────────────────────────
# PDF 텍스트 추출
# ─────────────────────────────────────────────
def extract_text_from_pdf(pdf_path: str, progress_callback=None) -> list[dict]:
    pages = []

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)

        for i, page in enumerate(pdf.pages):
            if progress_callback:
                progress_callback(
                    stage="extract",
                    current=i + 1,
                    total=total,
                    page_num=i + 1,
                )

            text = page.extract_text() or ""

            tables = page.extract_tables()
            table_text = ""
            if tables:
                for table in tables:
                    if table:
                        for row in table:
                            cleaned_row = [str(cell).strip() if cell else "" for cell in row]
                            table_text += " | ".join(cleaned_row) + "\n"
                        table_text += "\n"

            combined = text
            if table_text and table_text.strip() not in text:
                combined = text + "\n\n[표 데이터]\n" + table_text

            pages.append({
                "page": i + 1,
                "text": combined.strip()
            })

    return pages


# ─────────────────────────────────────────────
# 논리적 섹션 감지
# ─────────────────────────────────────────────
HEADING_PATTERNS = [
    re.compile(r"^(제\s*\d+\s*(장|절|조|편|항|부|관))", re.MULTILINE),
    re.compile(r"^(Chapter\s+\d+)", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^(Section\s+\d+)", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^(Part\s+[IVXLCDM\d]+)", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^(\d+\.(?:\d+\.)*\s+[A-Z가-힣])", re.MULTILINE),
    re.compile(r"^([A-Z][A-Z\s]{10,})$", re.MULTILINE),
]


def detect_sections(text: str) -> list[str]:
    sections = []
    for pattern in HEADING_PATTERNS:
        for match in pattern.finditer(text):
            sections.append(match.group(0).strip())
    return sections


# ─────────────────────────────────────────────
# 스마트 청킹
# ─────────────────────────────────────────────
def split_into_sentences(text: str) -> list[str]:
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
    tokenizer=None,
    progress_callback=None,
) -> list[dict]:
    if tokenizer is None:
        tokenizer = get_tokenizer()

    chunks = []
    current_lines = []
    current_tokens = 0
    current_page_start = pages[0]["page"] if pages else 1
    current_page_end = current_page_start
    current_headings = []

    header_budget = 100
    effective_max = max_tokens - header_budget
    total_pages = len(pages)

    def flush_chunk():
        nonlocal current_lines, current_tokens, current_page_start, current_page_end, current_headings

        if not current_lines:
            return

        body = "\n".join(current_lines)

        header = f"[페이지 {current_page_start}-{current_page_end}]"
        if current_headings:
            header += f" | 섹션: {', '.join(current_headings[:3])}"

        full_text = header + "\n" + "=" * 50 + "\n\n" + body

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

        overlap_lines = current_lines[-overlap_sentences:] if overlap_sentences > 0 else []
        current_lines = list(overlap_lines)
        current_tokens = count_tokens("\n".join(current_lines), tokenizer) if current_lines else 0
        current_headings = []

    for idx, page_data in enumerate(pages):
        page_num = page_data["page"]
        text = page_data["text"]

        if progress_callback:
            progress_callback(
                stage="chunking",
                current=idx + 1,
                total=total_pages,
                chunk_count=len(chunks),
                page_num=page_num,
            )

        if not text.strip():
            continue

        headings = detect_sections(text)
        if headings:
            current_headings.extend(headings)

        sentences = split_into_sentences(text)

        for sentence in sentences:
            sentence_tokens = count_tokens(sentence, tokenizer)

            if current_tokens + sentence_tokens > effective_max and current_lines:
                is_heading = any(p.match(sentence) for p in HEADING_PATTERNS)
                flush_chunk()
                current_page_start = page_num
                if is_heading:
                    current_headings = [sentence[:80]]

            current_lines.append(sentence)
            current_tokens += sentence_tokens
            current_page_end = page_num

    flush_chunk()

    if progress_callback:
        progress_callback(
            stage="chunking",
            current=total_pages,
            total=total_pages,
            chunk_count=len(chunks),
            page_num=total_pages if total_pages > 0 else 0,
        )

    return chunks


# ─────────────────────────────────────────────
# 파일 저장
# ─────────────────────────────────────────────
def save_chunks(chunks: list[dict], output_dir: str, pdf_name: str):
    os.makedirs(output_dir, exist_ok=True)

    total_chunks = len(chunks)
    pad = len(str(total_chunks)) if total_chunks > 0 else 1

    filenames = []
    for i, chunk in enumerate(chunks):
        num = str(i + 1).zfill(pad)
        filename = f"chunk_{num}.txt"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(chunk["text"])

        filenames.append(filename)

    readme_path = os.path.join(output_dir, "readme.txt")
    manifest_path = os.path.join(output_dir, "manifest.json")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write("=" * 60 + "\n")
        f.write("  문서 목차 (Table of Contents)\n")
        f.write(f"  원본: {pdf_name}\n")
        f.write(f"  생성일: {now}\n")
        f.write(f"  총 청크 수: {total_chunks}\n")
        f.write("=" * 60 + "\n\n")

        f.write("이 디렉토리는 대용량 PDF 문서를 분할한 텍스트 청크들을 담고 있습니다.\n\n")
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

    manifest = {
        "source_pdf": pdf_name,
        "created_at": now,
        "total_chunks": total_chunks,
        "chunks": [
            {
                "filename": f"chunk_{str(i + 1).zfill(pad)}.txt",
                "page_start": chunk["page_start"],
                "page_end": chunk["page_end"],
                "tokens": chunk["tokens"],
                "headings": chunk["headings"],
                "preview": chunk["preview"],
            }
            for i, chunk in enumerate(chunks)
        ]
    }

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return filenames, readme_path, manifest_path


# ─────────────────────────────────────────────
# ZIP 생성
# ─────────────────────────────────────────────
def make_zip_bytes(folder_path: str) -> bytes:
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(folder_path):
            for file in sorted(files):
                full_path = os.path.join(root, file)
                arcname = os.path.relpath(full_path, folder_path)
                zf.write(full_path, arcname=arcname)

    buffer.seek(0)
    return buffer.getvalue()


# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────
with st.sidebar:
    st.header("설정")
    max_tokens = st.number_input(
        "청크당 최대 토큰 수",
        min_value=500,
        max_value=20000,
        value=4000,
        step=500
    )
    overlap = st.number_input(
        "청크 간 겹치는 문장 수",
        min_value=0,
        max_value=20,
        value=3,
        step=1
    )
    encoding = st.text_input("텍스트 인코딩 표시용", value="utf-8", disabled=True)

    st.markdown("---")
    st.caption("결과는 서버 경로에 직접 저장하지 않고 ZIP으로 다운로드됩니다.")

uploaded_file = st.file_uploader("PDF 파일 업로드", type=["pdf"])
run = st.button("🚀 청킹 실행", type="primary", use_container_width=True)


if run:
    if not uploaded_file:
        st.warning("먼저 PDF 파일을 업로드해 주세요.")
        st.stop()

    progress_bar = st.progress(0)
    status_text = st.empty()
    detail_text = st.empty()

    def update_progress(stage, current=0, total=1, chunk_count=0, page_num=0):
        total = max(total, 1)
        percent = int(current / total * 100)

        if stage == "extract":
            status_text.info(f"📖 PDF 텍스트 추출 중... {current}/{total} 페이지 ({percent}%)")
            detail_text.write(f"현재 페이지: {page_num}")
            progress_bar.progress(percent)

        elif stage == "chunking":
            status_text.info(f"✂️ 청킹 진행 중... {current}/{total} 페이지 ({percent}%)")
            detail_text.write(f"현재 페이지: {page_num} | 현재까지 생성된 청크 수: {chunk_count}")
            progress_bar.progress(percent)

        elif stage == "saving":
            status_text.info("💾 결과 파일 생성 및 압축 중...")
            detail_text.write(f"생성된 청크 수: {chunk_count}")
            progress_bar.progress(100)

        elif stage == "done":
            status_text.success("✅ 청킹이 완료되었습니다.")
            detail_text.write(f"최종 청크 수: {chunk_count}")
            progress_bar.progress(100)

    with st.spinner("PDF 처리 중입니다..."):
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_pdf_path = Path(tmpdir) / uploaded_file.name
            temp_pdf_path.write_bytes(uploaded_file.read())

            pdf_name = temp_pdf_path.stem
            output_dir = Path(tmpdir) / f"chunks_{pdf_name}"
            output_dir.mkdir(parents=True, exist_ok=True)

            pages = extract_text_from_pdf(str(temp_pdf_path), progress_callback=update_progress)
            empty_pages = sum(1 for p in pages if not p["text"].strip())

            tokenizer = get_tokenizer()
            total_tokens = sum(count_tokens(p["text"], tokenizer) for p in pages)

            chunks = create_chunks(
                pages,
                max_tokens=max_tokens,
                overlap_sentences=overlap,
                tokenizer=tokenizer,
                progress_callback=update_progress,
            )

            update_progress(
                stage="saving",
                current=100,
                total=100,
                chunk_count=len(chunks),
            )

            filenames, readme_path, manifest_path = save_chunks(chunks, str(output_dir), pdf_name)
            zip_bytes = make_zip_bytes(str(output_dir))

            update_progress(
                stage="done",
                current=100,
                total=100,
                chunk_count=len(chunks),
            )

    st.session_state.zip_bytes = zip_bytes
    st.session_state.zip_filename = f"chunks_{pdf_name}.zip"
    st.session_state.result_summary = {
        "pages": len(pages),
        "empty_pages": empty_pages,
        "total_tokens": total_tokens,
        "total_chunks": len(chunks),
        "pdf_name": pdf_name,
        "generated_files": len(filenames) + 2,  # readme + manifest
    }
    st.session_state.chunk_previews = chunks[:3]

    st.success("청킹이 완료되었습니다.")


# ─────────────────────────────────────────────
# 결과 표시
# ─────────────────────────────────────────────
if st.session_state.result_summary:
    summary = st.session_state.result_summary

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 페이지", summary["pages"])
    c2.metric("빈 페이지", summary["empty_pages"])
    c3.metric("총 토큰", f"{summary['total_tokens']:,}")
    c4.metric("총 청크", summary["total_chunks"])

    st.info(
        f"원본 파일: `{summary['pdf_name']}.pdf` | "
        f"다운로드에 포함된 파일 수: {summary['generated_files']}개"
    )

    if st.session_state.zip_bytes and st.session_state.zip_filename:
        st.download_button(
            label="📦 ZIP 다운로드",
            data=st.session_state.zip_bytes,
            file_name=st.session_state.zip_filename,
            mime="application/zip",
            use_container_width=True,
        )

    with st.expander("청크 미리보기", expanded=True):
        previews = st.session_state.chunk_previews or []
        if previews:
            for i, chunk in enumerate(previews, start=1):
                st.markdown(
                    f"**chunk_{i}**  \n"
                    f"- 페이지: {chunk['page_start']}-{chunk['page_end']}  \n"
                    f"- 토큰: {chunk['tokens']}  \n"
                    f"- 섹션: {', '.join(chunk['headings'][:3]) if chunk['headings'] else '-'}"
                )
                st.code(chunk["text"][:2000], language="text")
        else:
            st.write("미리볼 청크가 없습니다.")