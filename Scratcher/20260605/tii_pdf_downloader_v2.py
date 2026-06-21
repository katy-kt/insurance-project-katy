"""
保發中心醫療險 PDF 全自動下載爬蟲（正式版）
=========================================
根據 step1_diagnose_frames.py 診斷結果重寫：
  - 無 frame / iframe 結構，直接在頂層操作
  - 關鍵字輸入框：id="TNDSearch"  name="KW"
  - 資料庫下拉：  id="DBselect"   name="select"
  - 送出按鈕：    name="Action"   value="執行"
  - 半自動模式：程式自動填關鍵字，驗證碼（若有）由使用者手動輸入後按執行

執行方式：
    python tii_pdf_downloader_v2.py

套件需求：
    pip install selenium webdriver-manager requests pandas beautifulsoup4
"""

import io
import os
import re
import time
import random
import logging
from pathlib import Path

import requests
import pandas as pd
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException

try:
    from webdriver_manager.chrome import ChromeDriverManager
    USE_WDM = True
except ImportError:
    USE_WDM = False

# ══════════════════════════════════════════════════════════════
# 設定區（依需求修改）
# ══════════════════════════════════════════════════════════════

BASE_URL = "https://insprod.tii.org.tw/QueryFullText.aspx"

# 要搜尋的關鍵字清單（程式會逐一搜尋）
SEARCH_KEYWORDS = [
    "醫療",
    "住院醫療",
    "實支實付",
    "手術",
    "癌症",
    "重大傷病",
    "長期照護",
]

# PDF 儲存根目錄
OUTPUT_ROOT = Path("./tii_pdfs")

# 等待使用者手動輸入驗證碼並按執行的最長秒數
CAPTCHA_TIMEOUT = 300  # 5 分鐘

# 每次操作之間的隨機延遲（秒）
SLEEP_MIN, SLEEP_MAX = 1.5, 3.0

# ══════════════════════════════════════════════════════════════
# 日誌設定
# ══════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("pdf_downloader.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def rsleep(a=SLEEP_MIN, b=SLEEP_MAX):
    time.sleep(random.uniform(a, b))


# ══════════════════════════════════════════════════════════════
# WebDriver 初始化
# ══════════════════════════════════════════════════════════════

def build_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--window-size=1400,900")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install()) if USE_WDM else Service()
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    return driver


# ══════════════════════════════════════════════════════════════
# 查詢與等待結果
# ══════════════════════════════════════════════════════════════

def fill_search_form(driver: webdriver.Chrome, keyword: str) -> bool:
    """
    在頁面填入關鍵字。
    如果有驗證碼，程式會暫停等待使用者手動輸入後按「執行」。
    回傳 True = 成功進入結果頁；False = 超時或失敗。
    """
    logger.info(f"開啟查詢頁面，關鍵字：{keyword!r}")
    driver.get(BASE_URL)
    rsleep(3, 5)

    wait = WebDriverWait(driver, 15)

    # ── 1. 找關鍵字輸入框並填入 ────────────────────────────
    try:
        kw_input = wait.until(EC.presence_of_element_located((By.ID, "TNDSearch")))
        kw_input.clear()
        kw_input.send_keys(keyword)
        logger.info(f"  已填入關鍵字：{keyword!r}")
    except TimeoutException:
        logger.error("  找不到關鍵字輸入框 (id=TNDSearch)，頁面結構可能已變動。")
        return False

    # ── 2. 選擇資料庫（DBselect）───────────────────────────
    # 診斷顯示只有一個 select，value 選項未知；嘗試選含「保險商品」的選項
    try:
        db_select_elem = driver.find_element(By.ID, "DBselect")
        db_select = Select(db_select_elem)
        options_text = [o.text.strip() for o in db_select.options]
        logger.info(f"  DBselect 選項：{options_text}")

        # 優先選含「保險商品」或「條款」的選項
        preferred = next(
            (o for o in options_text if any(kw in o for kw in ["保險商品", "條款", "商品"])),
            None
        )
        if preferred:
            db_select.select_by_visible_text(preferred)
            logger.info(f"  已選擇資料庫：{preferred!r}")
        else:
            logger.info(f"  DBselect 保持預設值：{db_select.first_selected_option.text!r}")
    except NoSuchElementException:
        logger.warning("  找不到 DBselect，保持預設。")

    # ── 3. 提示使用者完成驗證碼並送出 ─────────────────────
    print("\n" + "="*55)
    print(f"⚠️  關鍵字「{keyword}」已自動填入。")
    print("   請在瀏覽器中：")
    print("   1. 確認關鍵字與資料庫選項正確")
    print("   2. 輸入驗證碼（若有）")
    print("   3. 點擊「執行」按鈕")
    print(f"   程式等待最多 {CAPTCHA_TIMEOUT} 秒...")
    print("="*55 + "\n")

    # ── 4. 等待結果出現（偵測結果頁特徵）─────────────────
    try:
        WebDriverWait(driver, CAPTCHA_TIMEOUT).until(
            lambda d: _result_page_detected(d)
        )
        logger.info("  ✅ 結果頁面已載入！")
        rsleep(2, 3)
        return True
    except TimeoutException:
        logger.error("  ⏰ 等待結果頁超時。")
        return False


def _result_page_detected(driver: webdriver.Chrome) -> bool:
    """
    判斷結果頁是否已出現。
    保發中心全文搜尋結果頁通常包含：
      - 多筆 <a> 連結指向 PDF
      - 或包含「查無資料」文字
      - URL 改變（加入查詢參數）
    """
    try:
        # URL 出現查詢參數代表已送出
        if "KW=" in driver.current_url or "?" in driver.current_url:
            return True
        # 頁面出現 PDF 連結
        pdf_links = driver.find_elements(By.XPATH, "//a[contains(@href, '.pdf') or contains(@href, 'PDF')]")
        if pdf_links:
            return True
        # 頁面出現「查無資料」
        if "查無資料" in driver.page_source or "no data" in driver.page_source.lower():
            return True
        # 頁面出現典型結果結構（表格或清單含「條款」「商品名稱」）
        if any(kw in driver.page_source for kw in ["條款", "商品名稱", "保險商品名稱"]):
            return True
    except Exception:
        pass
    return False


# ══════════════════════════════════════════════════════════════
# 解析結果頁
# ══════════════════════════════════════════════════════════════

def parse_results(driver: webdriver.Chrome) -> list[dict]:
    """
    解析當前結果頁，抽取所有可下載的 PDF 資訊。
    回傳 list of dict：{"title", "url", "company"}
    """
    results = []
    soup = BeautifulSoup(driver.page_source, "html.parser")

    # ── 策略 A：找所有 href 含 .pdf 的 <a> 連結 ───────────
    pdf_anchors = soup.find_all("a", href=lambda h: h and (
        ".pdf" in h.lower() or "PDF" in h or "download" in h.lower()
    ))

    for anchor in pdf_anchors:
        href = anchor["href"].strip()
        # 補全相對路徑
        if href.startswith("/"):
            href = "https://insprod.tii.org.tw" + href
        elif not href.startswith("http"):
            href = "https://insprod.tii.org.tw/" + href

        title = anchor.get_text(strip=True) or anchor.get("title", "").strip()

        # 嘗試從附近的 <td> 找公司名稱
        parent_td = anchor.find_parent("td")
        company = ""
        if parent_td:
            row = parent_td.find_parent("tr")
            if row:
                cells = row.find_all("td")
                # 通常公司名在第 1 或第 2 欄
                if len(cells) >= 2:
                    company = cells[0].get_text(strip=True) or cells[1].get_text(strip=True)

        if href and title:
            results.append({"title": title, "url": href, "company": company})

    if results:
        logger.info(f"  策略A 找到 {len(results)} 個 PDF 連結")
        return results

    # ── 策略 B：pandas read_html 解析表格，再找連結 ────────
    try:
        tables = pd.read_html(io.StringIO(driver.page_source), encoding="utf-8")
        for df in tables:
            cols = " ".join(str(c) for c in df.columns)
            if any(kw in cols for kw in ["商品", "條款", "公司", "名稱"]):
                logger.info(f"  策略B 找到表格（{len(df)} 列），但無直接 PDF 連結，記錄文字資料")
                return df.to_dict("records")
    except Exception:
        pass

    logger.warning("  本頁未找到任何 PDF 連結或資料表格。")
    return []


# ══════════════════════════════════════════════════════════════
# 翻頁
# ══════════════════════════════════════════════════════════════

def get_total_records(driver: webdriver.Chrome) -> int:
    """嘗試抓總筆數（用於估算總頁數）"""
    try:
        soup = BeautifulSoup(driver.page_source, "html.parser")
        text = soup.get_text()
        # 常見格式：「共 XXX 筆」「查詢結果：XXX 件」
        m = re.search(r"共\s*(\d[\d,]*)\s*[筆件]", text)
        if m:
            return int(m.group(1).replace(",", ""))
    except Exception:
        pass
    return -1


def go_next_page(driver: webdriver.Chrome, current_page: int) -> bool:
    """
    嘗試翻到下一頁。
    支援：「下一頁」文字連結 / 「>」/ 數字分頁連結。
    """
    for text in ["下一頁", "次頁", ">", ">>", "Next"]:
        try:
            link = driver.find_element(By.LINK_TEXT, text)
            link.click()
            rsleep(2, 4)
            logger.info(f"  → 翻到第 {current_page + 1} 頁")
            return True
        except NoSuchElementException:
            continue

    # 嘗試點擊數字 (current_page + 1)
    try:
        link = driver.find_element(By.LINK_TEXT, str(current_page + 1))
        link.click()
        rsleep(2, 4)
        logger.info(f"  → 翻到第 {current_page + 1} 頁（數字連結）")
        return True
    except NoSuchElementException:
        pass

    return False


# ══════════════════════════════════════════════════════════════
# PDF 下載
# ══════════════════════════════════════════════════════════════

def sync_cookies(driver: webdriver.Chrome, session: requests.Session):
    """將 Selenium 的 Cookies 同步給 requests.Session，確保下載不被擋"""
    session.cookies.clear()
    for cookie in driver.get_cookies():
        session.cookies.set(cookie["name"], cookie["value"])
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": BASE_URL,
    })


def download_pdf(url: str, save_path: Path, session: requests.Session) -> bool:
    """下載單一 PDF 到指定路徑"""
    try:
        resp = session.get(url, timeout=30, stream=True)
        if resp.status_code == 200:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(f"  ✅ 下載成功：{save_path.name}")
            return True
        else:
            logger.warning(f"  ❌ HTTP {resp.status_code}：{url}")
    except Exception as e:
        logger.error(f"  ❌ 下載失敗：{e}  URL={url}")
    return False


def safe_filename(text: str) -> str:
    """移除 Windows 不允許的檔名字元"""
    return re.sub(r'[\\/*?:"<>|]', "", text).strip()[:80]


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def main():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    driver = build_driver()
    session = requests.Session()

    success_total = 0
    error_log = []

    try:
        for keyword in SEARCH_KEYWORDS:
            logger.info(f"\n{'='*60}")
            logger.info(f"開始搜尋關鍵字：{keyword!r}")
            logger.info(f"{'='*60}")

            save_dir = OUTPUT_ROOT / safe_filename(keyword)
            save_dir.mkdir(parents=True, exist_ok=True)

            # ── 填表、等結果 ──────────────────────────────────
            ok = fill_search_form(driver, keyword)
            if not ok:
                logger.warning(f"關鍵字 {keyword!r} 查詢失敗，跳過。")
                continue

            # ── 同步 Cookies ──────────────────────────────────
            sync_cookies(driver, session)

            total = get_total_records(driver)
            if total >= 0:
                logger.info(f"共找到約 {total} 筆資料")

            # ── 逐頁抓取 ──────────────────────────────────────
            page = 1
            downloaded_urls = set()   # 避免重複下載

            while True:
                logger.info(f"解析第 {page} 頁...")
                items = parse_results(driver)

                if not items:
                    logger.info("此頁無資料，結束翻頁。")
                    break

                for item in items:
                    # 只處理有 URL 的 PDF 項目
                    if "url" not in item or not item["url"]:
                        continue
                    url = item["url"]
                    if url in downloaded_urls:
                        continue
                    downloaded_urls.add(url)

                    title   = safe_filename(str(item.get("title", "unknown")))
                    company = safe_filename(str(item.get("company", "")))
                    fname   = f"{company}_{title}.pdf" if company else f"{title}.pdf"
                    save_path = save_dir / fname

                    if save_path.exists():
                        logger.info(f"  已存在，跳過：{fname}")
                        continue

                    # 同步 Cookies 後再下載
                    sync_cookies(driver, session)
                    ok = download_pdf(url, save_path, session)
                    if ok:
                        success_total += 1
                    else:
                        error_log.append(item)

                    rsleep(1, 2.5)

                # ── 翻頁 ──────────────────────────────────────
                if not go_next_page(driver, page):
                    logger.info("已到最後一頁。")
                    break
                page += 1
                sync_cookies(driver, session)   # 翻頁後重新同步

    except KeyboardInterrupt:
        logger.info("使用者中止。")
    finally:
        driver.quit()

    # ── 最終報告 ──────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"【完成】成功下載 PDF：{success_total} 份")
    print(f"        失敗筆數：{len(error_log)} 筆")
    print(f"        PDF 儲存位置：{OUTPUT_ROOT.resolve()}")
    print(f"{'='*60}")

    if error_log:
        err_path = Path("error_log.txt")
        with open(err_path, "w", encoding="utf-8") as f:
            for item in error_log:
                f.write(str(item) + "\n")
        print(f"失敗清單已存至：{err_path}")


if __name__ == "__main__":
    main()
