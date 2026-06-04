# ============================================================
# 模組二：結合 GVDR 混合架構的 PDF 文字提取與條文結構化切分
#
# 架構：
#   階段 A → Docling 將 PDF 解析為乾淨 Markdown
#   階段 B → GVDR 四步驟自動切分條文：
#            Generate（LLM 生成 Regex）
#            Validate（沙箱執行）
#            Diagnose（雙層診斷）
#            Rectify（迭代修復）
#   輸出   → 結構化 JSON，供 RAG 系統使用
#
# 必備套件安裝指令：
#   pip install docling openai
#   （若使用 Gemini：pip install google-generativeai）
# ============================================================

import re
import json
import logging
import traceback
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ── Docling PDF 解析套件 ─────────────────────────────────────
from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import PdfFormatOption

# ── LLM API（預設使用 OpenAI 相容介面；可替換為 Gemini） ───
import openai  # pip install openai

# ─────────────────────────────────────────────
# 全域設定
# ─────────────────────────────────────────────

# LLM API 金鑰（請替換為實際金鑰，或從環境變數讀取）
import os
LLM_API_KEY = os.getenv("OPENAI_API_KEY", "your-api-key-here")
LLM_MODEL   = "gpt-4o"   # 可替換為 gemini-1.5-pro 等

# GVDR 最大迭代修復次數
MAX_RECTIFY_ROUNDS = 3

# 前導文本採樣長度（字元數）
PREAMBLE_LENGTH = 2000

# Markdown 引用區塊（>）行數佔比上限警告閾值
BLOCKQUOTE_RATIO_THRESHOLD = 0.10

# 輸出 JSON 根目錄
OUTPUT_ROOT = Path("./tii_structured")

# ─────────────────────────────────────────────
# 日誌設定
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("gvdr.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 資料結構定義
# ─────────────────────────────────────────────

@dataclass
class ClauseItem:
    """單一條文的結構化資料"""
    clause_no: str      # 條號，如「第一條」、「第 1 條」
    title: str          # 條文標題
    content: str        # 條文內文


@dataclass
class PolicyDocument:
    """完整保單的結構化輸出"""
    company: str                        # 保險公司名稱
    product: str                        # 商品名稱
    insurance_type: str                 # 險種
    source_pdf: str                     # 來源 PDF 路徑
    clauses: list[ClauseItem] = field(default_factory=list)
    hard_sample: bool = False           # 是否標記為 Hard Sample
    parse_notes: str = ""               # 解析備註


@dataclass
class DiagnosisReport:
    """GVDR 雙層診斷報告"""
    layer1_ok: bool = True              # 第一層：語法錯誤檢核
    layer1_error: str = ""
    layer2_ok: bool = True              # 第二層：語意結構一致性
    missing_clauses: list[int] = field(default_factory=list)   # 跳號清單
    starts_from_one: bool = True        # 是否從第 1 條開始
    blockquote_ratio: float = 0.0      # Markdown 引用區塊佔比
    blockquote_warning: bool = False
    total_matched: int = 0             # 成功比對到的條號數


# ─────────────────────────────────────────────
# 階段 A：Docling PDF → Markdown 前處理
# ─────────────────────────────────────────────

def load_pdf_as_markdown(pdf_path: Path) -> str:
    """
    使用 Docling 將 PDF 解析為 Markdown 文字
    - 保留雙欄排版的正確閱讀順序
    - 保留表格結構（轉為 Markdown 表格）
    - 自動過濾頁首、頁尾、頁碼等版面雜訊
    """
    logger.info(f"[A] 開始解析 PDF：{pdf_path.name}")

    # ── Docling 管線設定 ──────────────────────────────────────
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = False            # 若為掃描檔，改為 True
    pipeline_options.do_table_structure = True  # 偵測表格結構
    pipeline_options.table_structure_options.do_cell_matching = True

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )

    result = converter.convert(str(pdf_path))
    raw_markdown = result.document.export_to_markdown()

    # ── 後置清理：移除版面雜訊 ────────────────────────────────
    cleaned = _clean_markdown_noise(raw_markdown)

    logger.info(f"[A] 解析完成，清理後字數：{len(cleaned)}")
    return cleaned


def _clean_markdown_noise(text: str) -> str:
    """
    過濾保險條款 PDF 中常見的版面雜訊：
    1. 獨立頁碼行（如「- 1 -」、「第 2 頁」、純數字行）
    2. 重複出現的公司名稱頁首行
    3. 連續空行壓縮為單一空行
    """
    lines = text.splitlines()
    cleaned_lines = []

    for line in lines:
        stripped = line.strip()

        # 過濾純頁碼行：「- 1 -」、「第 n 頁」、「Page n」、純數字
        if re.match(r"^[-\s]*\d+[-\s]*$", stripped):
            continue
        if re.match(r"^第\s*\d+\s*頁.*$", stripped):
            continue
        if re.match(r"^[Pp]age\s*\d+", stripped):
            continue
        # 過濾僅含分隔線的行
        if re.match(r"^[-─━=＝]{3,}$", stripped):
            continue

        cleaned_lines.append(line)

    # 壓縮連續空行
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines))
    return result.strip()


# ─────────────────────────────────────────────
# LLM 呼叫工具
# ─────────────────────────────────────────────

def call_llm(prompt: str, system: str = "", temperature: float = 0.2) -> str:
    """
    呼叫 LLM API（OpenAI 相容介面）
    可替換為 google.generativeai 的 Gemini 呼叫
    """
    client = openai.OpenAI(api_key=LLM_API_KEY)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=temperature,
    )
    return response.choices[0].message.content.strip()


# ─────────────────────────────────────────────
# 中文數字轉阿拉伯整數工具
# ─────────────────────────────────────────────

CN_NUM_MAP = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "百": 100,
}

def cn_to_arabic(cn: str) -> int:
    """
    將中文數字字串轉為阿拉伯整數
    支援：一、二十三、一百二十等
    不支援「千」以上（保險條款通常不超過百條）
    """
    cn = cn.strip()

    # 若已是阿拉伯數字，直接回傳
    if re.match(r"^\d+$", cn):
        return int(cn)

    result = 0
    temp = 0
    for char in cn:
        val = CN_NUM_MAP.get(char, -1)
        if val == -1:
            continue
        if val == 10:   # 「十」作為乘號
            if temp == 0:
                temp = 1  # 「十X」= 10+X
            result += temp * 10
            temp = 0
        elif val == 100:
            if temp == 0:
                temp = 1
            result += temp * 100
            temp = 0
        else:
            temp = val
    result += temp
    return result if result > 0 else 1


def extract_clause_number_int(clause_no: str) -> Optional[int]:
    """
    從條號字串提取整數序號
    支援格式：「第一條」、「第 12 條」、「1.」、「（一）」等
    """
    # 「第X條」阿拉伯數字
    m = re.search(r"第\s*(\d+)\s*條", clause_no)
    if m:
        return int(m.group(1))

    # 「第X條」中文數字
    m = re.search(r"第\s*([零一二三四五六七八九十百]+)\s*條", clause_no)
    if m:
        return cn_to_arabic(m.group(1))

    # 純阿拉伯數字「1.」「1、」
    m = re.match(r"^(\d+)[.、。]", clause_no.strip())
    if m:
        return int(m.group(1))

    # 括號中文數字「（一）」
    m = re.search(r"[（(]([零一二三四五六七八九十百]+)[）)]", clause_no)
    if m:
        return cn_to_arabic(m.group(1))

    return None


# ─────────────────────────────────────────────
# 階段 B：GVDR 核心切分邏輯
# ─────────────────────────────────────────────

class GVDRProcessor:
    """
    實作 Generation-Validation-Diagnosis-Rectification 混合架構
    用於將保險條款 Markdown 文字切分為結構化條文
    """

    def __init__(self, company: str, product: str, insurance_type: str):
        self.company = company
        self.product = product
        self.insurance_type = insurance_type

    # ── G：動態規則生成 ───────────────────────────────────────
    def _generate_regex(self, preamble: str, error_feedback: str = "") -> str:
        """
        【G - Generation】
        送入前 2000 字元前導文本，讓 LLM：
        1. 先以自然語言描述條號編目特徵（樣式特徵歸納）
        2. 再輸出對應的 Python re 正規表示式

        回傳 LLM 給出的 Python regex 字串（不含外層引號）
        """
        feedback_section = ""
        if error_feedback:
            feedback_section = f"""
【上一輪錯誤診斷報告】
{error_feedback}

請根據上述報告修正 Regex，特別注意上面提到的問題。
"""

        prompt = f"""你是保險條款文本分析專家，請分析以下保險條款前導文本（前 2000 字元），執行兩個任務：

【任務一：樣式特徵歸納】
用 2-3 句繁體中文描述這份條款的「條號編目特徵」，例如：
- 是否使用「第 X 條」格式？X 是中文數字還是阿拉伯數字？
- 條號後是否緊接標題？格式為何？
- 是否有巢狀括號（如「（一）」「（二）」）作為子條款？

【任務二：動態規則生成】
根據上述特徵，撰寫一個 Python re 的 pattern 字串（raw string 格式），
用於 re.finditer(pattern, full_text, re.MULTILINE) 時，
能同時捕捉：
- group(1)：條號（如「第一條」「第 1 條」）
- group(2)：標題（若有；若無則為空字串，但 group 仍需存在）
- group(3)：內文（直到下一個條號之前的全部文字）

【輸出格式要求】
請嚴格按照以下格式輸出，不要加其他說明：

STYLE_DESCRIPTION:
<你的特徵描述>

REGEX_PATTERN:
<僅輸出 raw string，例如 r"第([零一二三四五六七八九十百]+)條\\s*([^\\n]*)\\n([\\s\\S]*?)(?=第[零一二三四五六七八九十百]+條|$)">

{feedback_section}

【前導文本】
{preamble}
"""
        system = "你是精通 Python re 模組與保險法規文本解析的工程師，請輸出格式嚴格符合要求的結果。"
        raw_output = call_llm(prompt, system=system, temperature=0.15)

        # 解析 LLM 回覆，提取 REGEX_PATTERN 區塊
        pattern_match = re.search(r"REGEX_PATTERN:\s*\n(.+?)(?:\n\n|$)", raw_output, re.DOTALL)
        if pattern_match:
            pattern_str = pattern_match.group(1).strip()
            # 移除可能的 markdown 反引號包覆
            pattern_str = re.sub(r"^`+|`+$", "", pattern_str).strip()
            # 若 LLM 回傳含 r"..." 包裝，去掉外層引號
            inner = re.match(r'^r["\'](.+)["\']$', pattern_str, re.DOTALL)
            if inner:
                pattern_str = inner.group(1)
            logger.info(f"[G] 生成 Regex：{pattern_str[:80]}...")
            return pattern_str
        else:
            logger.warning("[G] 無法從 LLM 回覆中提取 REGEX_PATTERN，使用預設 pattern")
            # 保底預設 pattern（適用大多數「第X條」格式）
            return r"第([零一二三四五六七八九十百\d]+)條\s*([^\n]*)\n([\s\S]*?)(?=第[零一二三四五六七八九十百\d]+條|$)"

    # ── V：沙箱執行 ──────────────────────────────────────────
    def _validate_in_sandbox(self, pattern: str, full_text: str) -> tuple[list[dict], str]:
        """
        【V - Validation】
        在沙箱中執行 re.finditer，捕捉 Runtime Error
        回傳 (matches_list, error_msg)
        - matches_list：每個元素為 {clause_no, title, content}
        - error_msg：若有語法/執行錯誤，返回錯誤訊息；否則為空字串
        """
        matches = []
        try:
            compiled = re.compile(pattern, re.MULTILINE | re.DOTALL)
            for m in compiled.finditer(full_text):
                clause_no_raw = m.group(1) if m.lastindex >= 1 else ""
                title_raw     = m.group(2).strip() if m.lastindex >= 2 else ""
                content_raw   = m.group(3).strip() if m.lastindex >= 3 else ""

                # 重組完整條號字串
                clause_no_str = f"第{clause_no_raw}條"

                matches.append({
                    "clause_no": clause_no_str,
                    "title": title_raw,
                    "content": content_raw,
                })
            logger.info(f"[V] 沙箱執行成功，共匹配 {len(matches)} 條條文")
            return matches, ""

        except re.error as e:
            error_msg = f"Regex 語法錯誤（re.error）：{e}"
            logger.warning(f"[V] {error_msg}")
            return [], error_msg

        except Exception as e:
            error_msg = f"執行期錯誤：{traceback.format_exc()}"
            logger.warning(f"[V] {error_msg}")
            return [], error_msg

    # ── D：雙層診斷 ──────────────────────────────────────────
    def _diagnose(self, matches: list[dict], full_text: str, runtime_error: str = "") -> DiagnosisReport:
        """
        【D - Diagnosis】
        第一層：語法 / 執行期錯誤
        第二層：語意結構一致性（條號序列 + Markdown 格式異常）
        """
        report = DiagnosisReport()

        # ── 第一層：Runtime Error ──────────────────────────────
        if runtime_error:
            report.layer1_ok = False
            report.layer1_error = runtime_error
            return report  # 第一層失敗，不繼續第二層

        # ── 第二層：語意結構一致性 ────────────────────────────
        report.total_matched = len(matches)

        # 2a. 將條號轉為阿拉伯整數序列
        clause_ints = []
        for item in matches:
            num = extract_clause_number_int(item["clause_no"])
            if num is not None:
                clause_ints.append(num)

        # 2b. 是否從第 1 條開始
        if clause_ints and clause_ints[0] != 1:
            report.starts_from_one = False
            logger.warning(f"[D] 條號序列不從 1 開始，從 {clause_ints[0]} 開始")

        # 2c. 檢查跳號（連續性）
        for i in range(1, len(clause_ints)):
            expected = clause_ints[i - 1] + 1
            actual   = clause_ints[i]
            if actual != expected:
                # 記錄所有漏抓的條號
                for missing in range(expected, actual):
                    report.missing_clauses.append(missing)
                    logger.warning(f"[D] 跳號：預期第 {expected} 條，實際為第 {actual} 條，漏抓 {actual - expected} 條")

        report.layer2_ok = (
            report.starts_from_one
            and len(report.missing_clauses) == 0
        )

        # 2d. Markdown 格式異常：引用區塊（>）佔比
        lines = full_text.splitlines()
        total_lines = len(lines) if lines else 1
        blockquote_lines = sum(1 for l in lines if l.strip().startswith(">"))
        report.blockquote_ratio = blockquote_lines / total_lines

        if report.blockquote_ratio > BLOCKQUOTE_RATIO_THRESHOLD:
            report.blockquote_warning = True
            logger.warning(
                f"[D] Markdown 引用區塊佔比過高：{report.blockquote_ratio:.1%}（閾值 {BLOCKQUOTE_RATIO_THRESHOLD:.0%}）"
                f"，可能有版面解析異常"
            )

        return report

    # ── R：迭代修復 ──────────────────────────────────────────
    def _build_feedback_prompt(self, report: DiagnosisReport, pattern: str) -> str:
        """根據診斷報告組裝回饋文字，提供給下一輪 G 階段"""
        parts = [f"上一輪使用的 Regex：{pattern}"]

        if not report.layer1_ok:
            parts.append(f"❌ 第一層錯誤（Regex 語法或執行期錯誤）：\n{report.layer1_error}")

        if not report.layer2_ok:
            if not report.starts_from_one:
                parts.append("❌ 條號序列未從第 1 條開始，可能漏抓了前面的條文。")
            if report.missing_clauses:
                missing_str = "、".join(f"第 {n} 條" for n in report.missing_clauses[:10])
                parts.append(f"❌ 跳號，漏抓條號：{missing_str}{'…等' if len(report.missing_clauses) > 10 else ''}")

        if report.blockquote_warning:
            parts.append(
                f"⚠️  Markdown 引用區塊（> 開頭的行）佔全文 {report.blockquote_ratio:.1%}，超過 10% 閾值。"
                f"可能是條文被誤包進引用語法，請調整 Regex 讓內文群組不捕捉 > 開頭的行，或在內文中過濾它。"
            )

        return "\n".join(parts)

    # ── GVDR 主流程 ──────────────────────────────────────────
    def process(self, full_text: str) -> tuple[list[ClauseItem], bool, str]:
        """
        執行完整 GVDR 流程
        回傳 (clauses, is_hard_sample, notes)
        """
        preamble = full_text[:PREAMBLE_LENGTH]
        error_feedback = ""
        pattern = ""
        matches = []

        for round_num in range(1, MAX_RECTIFY_ROUNDS + 1):
            logger.info(f"[GVDR] 第 {round_num} 輪（上限 {MAX_RECTIFY_ROUNDS} 輪）")

            # G：生成 Regex
            pattern = self._generate_regex(preamble, error_feedback=error_feedback)

            # V：沙箱執行
            matches, runtime_error = self._validate_in_sandbox(pattern, full_text)

            # D：雙層診斷
            report = self._diagnose(matches, full_text, runtime_error=runtime_error)

            # 診斷通過，結束迭代
            if report.layer1_ok and report.layer2_ok:
                logger.info(f"[GVDR] ✅ 第 {round_num} 輪通過診斷，共 {len(matches)} 條")
                clauses = [
                    ClauseItem(
                        clause_no=m["clause_no"],
                        title=m["title"],
                        content=m["content"],
                    )
                    for m in matches
                ]
                return clauses, False, f"第 {round_num} 輪通過，Regex：{pattern[:50]}..."

            # R：組裝診斷回饋，準備下一輪修復
            error_feedback = self._build_feedback_prompt(report, pattern)
            logger.warning(f"[GVDR] 第 {round_num} 輪未通過，準備修復...\n{error_feedback}")

        # ── 超過最大迭代次數：降級處理 ───────────────────────
        logger.warning(f"[GVDR] ⚠️  已達最大重試輪次 {MAX_RECTIFY_ROUNDS}，降級為純 LLM 萃取")
        clauses, notes = self._fallback_llm_extraction(full_text)
        return clauses, True, f"Hard Sample：降級使用純 LLM 萃取，原因：{error_feedback[:200]}"

    # ── 備援：純 LLM 萃取 ─────────────────────────────────────
    def _fallback_llm_extraction(self, full_text: str) -> tuple[list[ClauseItem], str]:
        """
        GVDR 失敗後的備援方案：
        直接呼叫 LLM，要求以 JSON 格式逐條輸出條文
        """
        logger.info("[Fallback] 使用純 LLM 備援萃取...")

        # 為節省 Token，取前 8000 字元（可依需求調整）
        excerpt = full_text[:8000]

        prompt = f"""以下是一份保險條款的文字內容。
請將所有條文提取出來，以 JSON 陣列格式輸出，每個元素包含：
- clause_no：條號（如「第一條」）
- title：條文標題（若無請填空字串）
- content：條文內文

只輸出 JSON 陣列本身，不要有任何說明文字或 Markdown 程式碼區塊。

【條款文字】
{excerpt}
"""
        try:
            raw = call_llm(prompt, temperature=0.1)
            # 若 LLM 仍包了 ```json ... ```，剝除
            raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
            data = json.loads(raw)
            clauses = [
                ClauseItem(
                    clause_no=item.get("clause_no", ""),
                    title=item.get("title", ""),
                    content=item.get("content", ""),
                )
                for item in data if isinstance(item, dict)
            ]
            logger.info(f"[Fallback] 備援萃取成功，共 {len(clauses)} 條")
            return clauses, "備援 LLM 萃取成功"

        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"[Fallback] 備援萃取失敗：{e}")
            return [], f"備援失敗：{e}"


# ─────────────────────────────────────────────
# 主處理器：整合 A + B 兩階段
# ─────────────────────────────────────────────

def process_single_pdf(
    pdf_path: Path,
    company: str,
    product: str,
    insurance_type: str,
    output_dir: Path = OUTPUT_ROOT,
) -> PolicyDocument:
    """
    處理單一 PDF 檔案的完整流程：
    1. Docling 解析 PDF → Markdown
    2. GVDR 切分條文
    3. 組裝 PolicyDocument 並儲存 JSON
    """
    logger.info(f"\n{'='*60}")
    logger.info(f"處理：{pdf_path.name}")
    logger.info(f"公司：{company}  商品：{product}  險種：{insurance_type}")
    logger.info(f"{'='*60}")

    doc = PolicyDocument(
        company=company,
        product=product,
        insurance_type=insurance_type,
        source_pdf=str(pdf_path),
    )

    # ── 階段 A：PDF → Markdown ────────────────────────────────
    try:
        markdown_text = load_pdf_as_markdown(pdf_path)
    except Exception as e:
        logger.error(f"[A] Docling 解析失敗：{e}")
        doc.hard_sample = True
        doc.parse_notes = f"Docling 解析失敗：{e}"
        return doc

    # ── 階段 B：GVDR 切分 ─────────────────────────────────────
    processor = GVDRProcessor(company, product, insurance_type)
    clauses, is_hard, notes = processor.process(markdown_text)

    doc.clauses = clauses
    doc.hard_sample = is_hard
    doc.parse_notes = notes

    # ── 輸出 JSON ─────────────────────────────────────────────
    output_dir.mkdir(parents=True, exist_ok=True)
    json_filename = f"{pdf_path.stem}.json"
    json_path = output_dir / json_filename

    output_data = {
        "company": doc.company,
        "product": doc.product,
        "insurance_type": doc.insurance_type,
        "source_pdf": doc.source_pdf,
        "hard_sample": doc.hard_sample,
        "parse_notes": doc.parse_notes,
        "total_clauses": len(doc.clauses),
        "clauses": [
            {
                "clause_no": c.clause_no,
                "title": c.title,
                "content": c.content,
            }
            for c in doc.clauses
        ],
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    logger.info(f"✅ 輸出 JSON：{json_path}（{len(doc.clauses)} 條條文）")
    return doc


# ─────────────────────────────────────────────
# 批次處理：遍歷整個下載資料夾
# ─────────────────────────────────────────────

def batch_process(
    pdf_root: Path,
    output_root: Path = OUTPUT_ROOT,
    skip_existing: bool = True,
) -> None:
    """
    批次處理 PDF 資料夾下的所有 PDF 檔案
    :param pdf_root: 模組一下載的 PDF 根目錄
    :param output_root: JSON 輸出根目錄
    :param skip_existing: 若 JSON 已存在則跳過
    """
    pdf_files = list(pdf_root.rglob("*.pdf"))
    logger.info(f"共找到 {len(pdf_files)} 份 PDF 待處理")

    success_count = 0
    hard_sample_count = 0

    for pdf_path in pdf_files:
        json_path = output_root / (pdf_path.stem + ".json")
        if skip_existing and json_path.exists():
            logger.info(f"⏭️  已存在，跳過：{pdf_path.name}")
            continue

        # ── 從檔名解析元資料（格式：保險公司_商品名稱_日期.pdf）──
        parts = pdf_path.stem.split("_", 2)
        company = parts[0] if len(parts) > 0 else "未知"
        product = parts[1] if len(parts) > 1 else pdf_path.stem
        # 險種從父資料夾名稱推斷（模組一依險種建立資料夾）
        insurance_type = pdf_path.parent.name if pdf_path.parent != pdf_root else "未分類"

        doc = process_single_pdf(
            pdf_path=pdf_path,
            company=company,
            product=product,
            insurance_type=insurance_type,
            output_dir=output_root,
        )

        if doc.hard_sample:
            hard_sample_count += 1
        else:
            success_count += 1

    logger.info(f"\n{'='*60}")
    logger.info(f"批次處理完成")
    logger.info(f"正常完成：{success_count} 份")
    logger.info(f"Hard Sample：{hard_sample_count} 份（需人工複查）")
    logger.info(f"{'='*60}")


# ─────────────────────────────────────────────
# 程式進入點
# ─────────────────────────────────────────────
if __name__ == "__main__":
    # ── 單一 PDF 測試模式 ──────────────────────────────────────
    test_pdf = Path("./tii_pdfs/住院醫療險/範例保險公司_住院醫療保險附約_20231001.pdf")

    if test_pdf.exists():
        process_single_pdf(
            pdf_path=test_pdf,
            company="範例保險公司",
            product="住院醫療保險附約",
            insurance_type="醫療險",
            output_dir=OUTPUT_ROOT,
        )
    else:
        # ── 批次模式：處理模組一下載的所有 PDF ───────────────
        batch_process(
            pdf_root=Path("./tii_pdfs"),
            output_root=OUTPUT_ROOT,
            skip_existing=True,
        )
