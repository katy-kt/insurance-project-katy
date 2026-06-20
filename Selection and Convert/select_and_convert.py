"""
保險保單 PDF 自動篩選與轉換腳本
================================
功能：
  1. 掃描 INPUT_DIR 中所有 PDF（只讀前兩頁比對關鍵字）
  2. 依五大類別分類，挑出命中的 PDF
  3. 複製到 OUTPUT_DIR（不動原始資料夾）
  4. 用 docling 轉換為 .txt 供 RAG 系統使用

安裝需求：
  pip install docling tqdm

注意：docling 首次執行會下載模型，可能需要幾分鐘。
"""

import shutil
import logging
from pathlib import Path

from tqdm import tqdm

# ══════════════════════════════════════════════════════════════
# 設定區（依需求修改）
# ══════════════════════════════════════════════════════════════

# 爬蟲下載的保單條款資料夾
INPUT_DIR = Path(r"C:\Users\Katy\Desktop\畢業專題\tii_pdfs\健康保險\保單條款")

# 篩選結果輸出資料夾（會自動建立）
OUTPUT_DIR = Path(r"C:\Users\Katy\Desktop\畢業專題\demo_dataset")

# 每類至多選幾份（避免某類過多）
MAX_PER_CATEGORY = 3

# 五大類別關鍵字（命中其中一個字就算）
CATEGORIES = {
    "一、醫療險理賠實務與條款認定": [
        "實支實付", "住院費用", "手術費用", "雜費", "理賠",
        "住院醫療費用", "醫療費用保險金", "住院醫療保險",
    ],
    "二、醫療與健康險保單健檢及預算規劃": [
        "住院日額", "日額型", "定額給付", "住院日額給付",
        "綜合醫療", "一年定期住院",
    ],
    "三、特定醫療險種（癌症／重大傷病／長照）比較與附加詢問": [
        "癌症", "防癌", "重大疾病", "重大傷病", "特定傷病",
        "長期照顧", "長照", "失能", "長期看護",
    ],
    "四、新生兒專屬醫療險配置": [
        "幼童", "兒童", "嬰兒", "新生兒", "學生", "嬰幼",
        "0歲", "親子", "珍愛寶貝",
    ],
    "五、醫療險理賠之法律與資產保全爭議": [
        "批註", "條款變更", "法定傳染病", "豁免保險費",
        "除外責任", "爭議", "保全", "等待期",
    ],
}

# ══════════════════════════════════════════════════════════════
# 日誌
# ══════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(OUTPUT_DIR.parent / "selector.log",
                            encoding="utf-8", mode="w"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# PDF 文字擷取（只取前兩頁，用 pypdf 快速讀取）
# ══════════════════════════════════════════════════════════════
def extract_first_two_pages(pdf_path: Path) -> str:
    """
    快速讀取 PDF 前兩頁文字，用於關鍵字比對。
    比 docling 快很多，只用於篩選階段。
    """
    try:
        import pypdf
        reader = pypdf.PdfReader(str(pdf_path))
        text = ""
        for i, page in enumerate(reader.pages):
            if i >= 2:
                break
            text += (page.extract_text() or "")
        return text
    except Exception as e:
        log.warning(f"  pypdf 無法讀取 {pdf_path.name}：{e}")
        return ""


# ══════════════════════════════════════════════════════════════
# 分類邏輯
# ══════════════════════════════════════════════════════════════
def classify(text: str, filename: str) -> list[str]:
    """
    回傳命中的類別名稱清單（可能同時屬於多類）。
    先比對檔名，再比對內文。
    """
    combined = filename + text
    hits = []
    for cat, keywords in CATEGORIES.items():
        if any(kw in combined for kw in keywords):
            hits.append(cat)
    return hits


# ══════════════════════════════════════════════════════════════
# docling PDF → txt 轉換
# ══════════════════════════════════════════════════════════════
def convert_to_txt(pdf_path: Path, txt_path: Path) -> bool:
    """
    用 docling 把 PDF 轉成 Markdown 格式的 txt。
    docling 的輸出包含結構化標記，適合後續 RAG 切分。
    """
    try:
        from docling.document_converter import DocumentConverter
        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))
        txt_path.write_text(
            result.document.export_to_markdown(),
            encoding="utf-8"
        )
        return True
    except Exception as e:
        log.warning(f"  docling 轉換失敗 {pdf_path.name}：{e}")
        # fallback：用 pypdf 純文字
        try:
            import pypdf
            reader = pypdf.PdfReader(str(pdf_path))
            text = "\n".join(
                (page.extract_text() or "") for page in reader.pages
            )
            txt_path.write_text(text, encoding="utf-8")
            log.info(f"  fallback pypdf 轉換成功：{txt_path.name}")
            return True
        except Exception as e2:
            log.error(f"  fallback 也失敗：{e2}")
            return False


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 確認 pypdf 已安裝
    try:
        import pypdf
    except ImportError:
        print("請先安裝 pypdf：pip install pypdf")
        return

    pdf_files = sorted(INPUT_DIR.glob("*.pdf"))
    log.info(f"共找到 {len(pdf_files)} 份 PDF，開始掃描...")

    # 分類結果：{類別名: [pdf_path, ...]}
    category_results: dict[str, list[Path]] = {cat: [] for cat in CATEGORIES}
    unclassified: list[Path] = []

    # ── 第一步：快速掃描分類 ──
    for pdf_path in tqdm(pdf_files, desc="掃描分類中", unit="份"):
        try:
            text = extract_first_two_pages(pdf_path)
            hits = classify(text, pdf_path.stem)

            if hits:
                for cat in hits:
                    category_results[cat].append(pdf_path)
            else:
                unclassified.append(pdf_path)
        except Exception as e:
            log.warning(f"跳過壞檔 {pdf_path.name}：{e}")

    # ── 印出分類結果 ──
    print("\n" + "="*60)
    print("【分類結果】")
    print("="*60)
    for cat, files in category_results.items():
        print(f"\n[{cat}]（共 {len(files)} 份，最多選 {MAX_PER_CATEGORY} 份）")
        for f in files[:MAX_PER_CATEGORY]:
            print(f"  {f.name}")
    print(f"\n[無法分類/其他]（共 {len(unclassified)} 份）")
    for f in unclassified[:5]:
        print(f"  {f.name}")
    if len(unclassified) > 5:
        print(f"  ...（還有 {len(unclassified)-5} 份）")

    # ── 第二步：複製選出的 PDF 並轉換為 txt ──
    selected: list[tuple[Path, str]] = []
    for cat, files in category_results.items():
        for pdf_path in files[:MAX_PER_CATEGORY]:
            selected.append((pdf_path, cat))

    log.info(f"\n共選出 {len(selected)} 份 PDF，開始複製與轉換...")

    ok_count = 0
    for pdf_path, cat in tqdm(selected, desc="複製+轉換中", unit="份"):
        # 子資料夾以類別編號命名（避免中文路徑問題）
        cat_num = cat.split("、")[0].replace("一", "1").replace("二", "2") \
                     .replace("三", "3").replace("四", "4").replace("五", "5")
        sub_dir = OUTPUT_DIR / f"cat{cat_num}"
        sub_dir.mkdir(parents=True, exist_ok=True)

        # 複製 PDF
        dest_pdf = sub_dir / pdf_path.name
        try:
            shutil.copy2(pdf_path, dest_pdf)
        except Exception as e:
            log.error(f"複製失敗 {pdf_path.name}：{e}")
            continue

        # 轉換為 txt（同檔名）
        dest_txt = sub_dir / (pdf_path.stem + ".txt")
        if convert_to_txt(dest_pdf, dest_txt):
            log.info(f"  ✅ {pdf_path.name} → {dest_txt.name}")
            ok_count += 1
        else:
            log.warning(f"  ❌ 轉換失敗：{pdf_path.name}")

    print(f"\n{'='*60}")
    print(f"  完成！成功轉換 {ok_count} / {len(selected)} 份")
    print(f"  輸出位置：{OUTPUT_DIR.resolve()}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
