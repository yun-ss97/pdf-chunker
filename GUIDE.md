# 대용량 PDF → Claude 프로젝트 Knowledge 업로드 가이드

## 전체 워크플로우 요약

```
[2,000페이지 PDF] → [pdf_chunker.py 실행] → [chunk_001~N.txt + readme.txt]
                                                       ↓
                                    ┌──────────────────────────────────────┐
                                    │  방법 A: 프로젝트 Knowledge에 업로드  │
                                    │  방법 B: MCP FileServer로 연결       │
                                    └──────────────────────────────────────┘
```

---

## Step 1: 환경 준비

### 1-1. Python 패키지 설치

```bash
pip install pdfplumber tiktoken
```

### 1-2. 스크립트 다운로드

`pdf_chunker.py` 파일을 원하는 디렉토리에 저장하세요.

---

## Step 2: PDF 청킹 실행

### 기본 사용법

```bash
python pdf_chunker.py your_document.pdf
```

이 명령은 `./chunks_your_document/` 디렉토리를 생성하고 그 안에:
- `chunk_001.txt`, `chunk_002.txt`, ... (분할된 텍스트)
- `readme.txt` (목차 및 인덱스)

를 생성합니다.

### 고급 옵션

```bash
# 토큰 수를 3000으로 줄이기 (더 작은 청크)
python pdf_chunker.py your_document.pdf --max-tokens 3000

# 출력 디렉토리 지정
python pdf_chunker.py your_document.pdf -o ./my_output

# 오버랩 문장 수 조정 (문맥 연결 강화)
python pdf_chunker.py your_document.pdf --overlap 5
```

### 토큰 수 가이드라인

| 상황 | 권장 토큰 수 | 비고 |
|------|-------------|------|
| 일반 문서 | 4,000 | 기본값, 대부분의 경우 적합 |
| 법률/기술 문서 | 3,000 | 정밀한 참조가 필요할 때 |
| 소설/에세이 | 5,000 | 연속적인 읽기 흐름이 중요할 때 |
| Knowledge 용량이 빠듯할 때 | 2,000~3,000 | 더 선별적으로 업로드 |

---

## Step 3: Claude 프로젝트에 업로드 (방법 A)

### 3-1. Claude 프로젝트 Knowledge 한도 확인

| 플랜 | 프로젝트 Knowledge 컨텍스트 |
|------|---------------------------|
| Pro | 약 200K 토큰 (약 500페이지 분량) |
| Team/Enterprise | 더 큰 용량 가능 |

**⚠️ 중요**: 2,000페이지 PDF의 전체 텍스트는 보통 300K~800K 토큰입니다.
Pro 플랜이라면 전부 넣기 어려울 수 있습니다.

### 3-2. 선별적 업로드 전략

1. `readme.txt`를 먼저 읽고 전체 구조를 파악합니다
2. 작업에 필요한 섹션의 청크만 골라서 업로드합니다
3. 작업 범위가 바뀌면 청크를 교체합니다

### 3-3. 업로드 방법

1. **claude.ai** → 좌측 사이드바에서 **Projects** 클릭
2. 프로젝트 선택 (또는 새로 생성)
3. **Project knowledge** 영역에서 **Add content** → **Upload files**
4. `readme.txt` + 필요한 `chunk_XXX.txt` 파일들을 선택
5. 업로드 완료

### 3-4. 효과적인 사용 팁

Claude에게 다음과 같이 지시하세요:

```
readme.txt에서 목차를 확인한 후,
내가 질문하는 내용과 관련된 청크 파일을 참조해서 답변해줘.
```

---

## Step 4: MCP FileServer로 연결 (방법 B) — 권장

이 방법은 전체 문서를 컨텍스트에 넣지 않고,
Claude가 필요할 때만 파일을 읽어오므로 훨씬 효율적입니다.

### 4-1. Claude Desktop 설치

- https://claude.ai/download 에서 Claude Desktop 다운로드

### 4-2. FileSystem MCP 서버 설정

Claude Desktop의 설정 파일을 편집합니다:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": [
        "-y",
        "@anthropic-ai/mcp-filesystem",
        "/path/to/your/chunks_directory"
      ]
    }
  }
}
```

> `/path/to/your/chunks_directory`를 실제 청크 출력 디렉토리 경로로 변경하세요.

### 4-3. Node.js 필요

MCP FileServer는 Node.js가 필요합니다:
- https://nodejs.org 에서 LTS 버전 설치

### 4-4. 사용 방법

Claude Desktop을 재시작하면 MCP 연결이 활성화됩니다.
Claude에게 다음과 같이 지시하세요:

```
파일 시스템에서 readme.txt를 읽어서 문서 구조를 파악해줘.
그 다음 내가 질문하는 내용에 해당하는 청크를 찾아서 읽어줘.
```

---

## 두 방법 비교

| 비교 항목 | 방법 A: Knowledge 업로드 | 방법 B: MCP FileServer |
|-----------|------------------------|----------------------|
| 설정 난이도 | 쉬움 (드래그 앤 드롭) | 중간 (설정 파일 편집 필요) |
| 전체 문서 처리 | ❌ 용량 한도 있음 | ✅ 필요한 것만 읽음 |
| 응답 속도 | 빠름 (이미 컨텍스트에 있음) | 약간 느림 (매번 파일 읽기) |
| 정확도 | 좋음 | 매우 좋음 (집중 참조) |
| 비용 효율 | 토큰 소비 많음 | 토큰 소비 적음 |
| 웹에서 사용 | ✅ claude.ai 가능 | ❌ Desktop 전용 |

---

## 트러블슈팅

### "빈 페이지가 많습니다" 경고

→ 스캔된 PDF일 가능성이 높습니다. OCR이 필요합니다:

```bash
pip install pytesseract pdf2image
# + Tesseract OCR 엔진 설치 필요
# macOS: brew install tesseract
# Ubuntu: sudo apt install tesseract-ocr
# Windows: https://github.com/UB-Mannheim/tesseract/wiki
```

### 특수문자/깨진 텍스트

→ PDF의 폰트 인코딩 문제일 수 있습니다.
`pdftotext` (poppler-utils)로 시도해 보세요:

```bash
# macOS: brew install poppler
# Ubuntu: sudo apt install poppler-utils
pdftotext -layout your_document.pdf output.txt
```

### Claude가 "파일이 너무 큽니다" 오류

→ 청크 크기를 더 줄이세요 (`--max-tokens 2000`).
→ 또는 필요한 청크만 선별 업로드하세요.
