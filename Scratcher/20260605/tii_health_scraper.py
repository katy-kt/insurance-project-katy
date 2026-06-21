"""
保發中心健康保險商品 PDF 下載爬蟲
目標：Query.aspx → 人身保險 / 健康保險 → 逐頁抓 PDF 連結 → 下載
流程：程式自動選好選項，等使用者填驗證碼後繼續，之後全自動翻頁下載
"""

import io
import re
import time
import random
import logging
import requests
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException
)

try:
    from webdriver_manager.chrome import ChromeDriverManager
    _service = Service(ChromeDriverManager().install())
except ImportError:
    _service = Service()

# ══════════════════════════════════════════════════════════
# 設定區
# ══════════════════════════════════════════════════════════
BASE_URL     = "https://insprod.tii.org.tw/Query.aspx"
OUTPUT_ROOT  = Path("./tii_pdfs")
CAPTCHA_WAIT = 180    # 等使用者填驗證碼的秒數上限

# 要查的保險類別清單（對應 f_CategoryId1 的選項文字）
TARGET_CATEGORIES = ["健康保險"]

# 若只要特定公司，改成公司代碼字串，例如 "204"；留空 = 壽險全部 "000"
TARGET_COMPANY_VALUE = "000"   # "000-產壽險全部" 的 value 為 "000"

# ══════════════════════════════════════════════════════════
# 日誌
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("tii_scraper.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def rnd(a=1.2, b=2.8):
    time.sleep(random.uniform(a, b))


# ══════════════════════════════════════════════════════════
# WebDriver
# ══════════════════════════════════════════════════════════
def build_driver():
    opt = Options()
    opt.add_argument("--window-size=1400,900")
    opt.add_argument("--no-sandbox")
    opt.add_argument("--disable-dev-shm-usage")
    opt.add_experimental_option("excludeSwitches", ["enable-automation"])
    opt.add_experimental_option("useAutomationExtension", False)
    opt.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(service=_service, options=opt)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    return driver


# ══════════════════════════════════════════════════════════
# 填表與等待結果
# ══════════════════════════════════════════════════════════
def setup_query_form(driver, category_text):
    """
    自動選好：公司類別=人身保險、公司=壽險全部、保險類別=<category_text>
    然後暫停，讓使用者手動填驗證碼並點「開始查詢」。
    回傳 True 代表成功偵測到結果頁，False 代表超時。
    """
    log.info(f"開啟查詢頁面，準備查詢類別：{category_text!r}")
    driver.get(BASE_URL)
    rnd(3, 5)

    wait = WebDriverWait(driver, 15)

    # ── 1. 公司類別 → 人身保險 ──────────────────────────
    try:
        cat_select = wait.until(
            EC.presence_of_element_located((By.NAME, "categoryId"))
        )
        Select(cat_select).select_by_visible_text("人身保險")
        log.info("  已選：公司類別 = 人身保險")
        rnd(0.8, 1.5)
    except (TimeoutException, NoSuchElementException) as e:
        log.error(f"  找不到 categoryId select：{e}")
        return False

    # ── 2. 公司名稱 → 壽險全部（等頁面動態更新選單後再選）
    try:
        # 等待公司清單包含壽險選項（頁面會動態 reload 選單）
        WebDriverWait(driver, 10).until(
            lambda d: any(
                "壽險" in o.text
                for o in Select(d.find_element(By.NAME, "CompanyID")).options
            )
        )
        company_select = driver.find_element(By.NAME, "CompanyID")
        # 找第一個含「壽險全部」的選項
        opts = Select(company_select).options
        target_opt = next((o for o in opts if "壽險全部" in o.text), None)
        if target_opt:
            Select(company_select).select_by_visible_text(target_opt.text)
            log.info(f"  已選：公司名稱 = {target_opt.text!r}")
        rnd(0.8, 1.5)
    except Exception as e:
        log.warning(f"  CompanyID 選單處理失敗，保持預設：{e}")

    # ── 3. 保險類別 → category_text ─────────────────────
    try:
        f_select = driver.find_element(By.NAME, "f_CategoryId1")
        Select(f_select).select_by_visible_text(category_text)
        log.info(f"  已選：保險類別 = {category_text!r}")
        rnd(0.5, 1.0)
    except NoSuchElementException as e:
        log.error(f"  找不到 f_CategoryId1 select：{e}")
        return False

    # ── 4. 提示使用者填驗證碼 ────────────────────────────
    print("\n" + "="*55)
    print(f"  保險類別已自動選好：{category_text}")
    print("  請在瀏覽器中：")
    print("    1. 確認選項正確")
    print("    2. 在「查詢識別碼」欄填入圖形驗證碼")
    print("    3. 點擊「開始查詢」按鈕")
    print(f"  程式等候最多 {CAPTCHA_WAIT} 秒...")
    print("="*55 + "\n")

    # ── 5. 等待結果頁出現 ────────────────────────────────
    try:
        WebDriverWait(driver, CAPTCHA_WAIT).until(_is_result_page)
        log.info("  ✅ 結果頁已載入")
        rnd(2, 3)
        return True
    except TimeoutException:
        log.error("  ⏰ 等待超時，跳過此類別")
        return False


def _is_result_page(driver):
    """偵測是否已進入結果頁（URL 帶參數，或頁面出現商品清單特徵）"""
    url = driver.current_url
    if "ResultQueryAll.aspx" in url or "Result" in url:
        return True
    src = driver.page_source
    # 結果頁通常含有商品名稱表格標題
    return any(kw in src for kw in ["商品名稱", "保險商品名稱", "查無資料", "查詢結果"])


# ══════════════════════════════════════════════════════════
# 解析結果頁
# ══════════════════════════════════════════════════════════
def parse_result_page(driver):
    """
    從結果頁抓所有商品列的 PDF 連結。
    回傳 list of dict：{title, company, pdf_url}
    """
    items = []
    soup  = BeautifulSoup(driver.page_source, "html.parser")

    # ── 策略 A：直接找所有 .pdf / download href ──────────
    anchors = soup.find_all(
        "a",
        href=lambda h: h and (".pdf" in h.lower() or "download" in h.lower())
    )
    for a in anchors:
        href = a["href"].strip()
        if href.startswith("/"):
            href = "https://insprod.tii.org.tw" + href
        elif not href.startswith("http"):
            href = "https://insprod.tii.org.tw/" + href

        title   = a.get_text(strip=True) or a.get("title", "").strip()
        company = _extract_company(a)
        items.append({"title": title, "company": company, "pdf_url": href})

    if items:
        log.info(f"  策略A：找到 {len(items)} 個 PDF 連結")
        return items

    # ── 策略 B：找 javascript 型連結（onclick 帶 url）────
    js_anchors = soup.find_all("a", onclick=True)
    for a in js_anchors:
        onclick = a.get("onclick", "")
        # 常見格式：window.open('/path/file.pdf') 或 location.href='/...'
        m = re.search(r"['\"]([^'\"]*\.pdf[^'\"]*)['\"]", onclick, re.I)
        if m:
            href = m.group(1)
            if not href.startswith("http"):
                href = "https://insprod.tii.org.tw" + href
            title   = a.get_text(strip=True)
            company = _extract_company(a)
            items.append({"title": title, "company": company, "pdf_url": href})

    if items:
        log.info(f"  策略B：從 onclick 找到 {len(items)} 個 PDF 連結")
        return items

    # ── 策略 C：pandas 讀表格（記錄文字，無 PDF 連結）────
    try:
        tables = pd.read_html(io.StringIO(driver.page_source), encoding="utf-8")
        for df in tables:
            cols = " ".join(str(c) for c in df.columns)
            if any(kw in cols for kw in ["商品", "公司", "類別"]):
                log.info(f"  策略C：找到文字表格 {len(df)} 列（無 PDF 直連）")
                return df.to_dict("records")
    except Exception:
        pass

    log.warning("  本頁找不到任何 PDF 連結或資料表格")
    log.info("  儲存 debug_result_page.html 供人工檢查")
    with open("debug_result_page.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)

    return []


def _extract_company(anchor_tag):
    """嘗試從同一 <tr> 的第一欄抓公司名稱"""
    try:
        row   = anchor_tag.find_parent("tr")
        cells = row.find_all("td") if row else []
        if len(cells) >= 2:
            return cells[1].get_text(strip=True)
    except Exception:
        pass
    return ""


# ══════════════════════════════════════════════════════════
# 翻頁
# ══════════════════════════════════════════════════════════
def next_page(driver, current_page):
    """點下一頁，成功回傳 True"""
    for text in ["下一頁", "次頁", ">", ">>"]:
        try:
            driver.find_element(By.LINK_TEXT, text).click()
            rnd(2, 4)
            log.info(f"  → 翻到第 {current_page + 1} 頁")
            return True
        except NoSuchElementException:
            pass
    try:
        driver.find_element(By.LINK_TEXT, str(current_page + 1)).click()
        rnd(2, 4)
        log.info(f"  → 翻到第 {current_page + 1} 頁（數字）")
        return True
    except NoSuchElementException:
        pass
    return False


# ══════════════════════════════════════════════════════════
# 下載
# ══════════════════════════════════════════════════════════
def sync_cookies(driver, session):
    session.cookies.clear()
    for c in driver.get_cookies():
        session.cookies.set(c["name"], c["value"])
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": BASE_URL,
    })


def download_pdf(url, path, session):
    try:
        r = session.get(url, timeout=30, stream=True)
        if r.status_code == 200:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            log.info(f"  ✅ {path.name}")
            return True
        log.warning(f"  ❌ HTTP {r.status_code}: {url}")
    except Exception as e:
        log.error(f"  ❌ 下載失敗 {e}: {url}")
    return False


def safe_name(s, max_len=80):
    return re.sub(r'[\\/*?:"<>|]', "", str(s)).strip()[:max_len]


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════
def main():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    driver  = build_driver()
    session = requests.Session()
    ok_total, err_list = 0, []

    try:
        for category in TARGET_CATEGORIES:
            log.info(f"\n{'='*60}\n開始抓取類別：{category}\n{'='*60}")
            save_dir = OUTPUT_ROOT / safe_name(category)
            save_dir.mkdir(parents=True, exist_ok=True)

            if not setup_query_form(driver, category):
                continue

            sync_cookies(driver, session)
            seen_urls = set()
            page = 1

            while True:
                log.info(f"解析第 {page} 頁...")
                items = parse_result_page(driver)

                if not items:
                    log.info("無更多資料，結束。")
                    break

                for item in items:
                    url = item.get("pdf_url", "")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)

                    title   = safe_name(item.get("title", "unknown"))
                    company = safe_name(item.get("company", ""))
                    fname   = f"{company}_{title}.pdf" if company else f"{title}.pdf"
                    path    = save_dir / fname

                    if path.exists():
                        log.info(f"  已存在，略過：{fname}")
                        continue

                    sync_cookies(driver, session)
                    if download_pdf(url, path, session):
                        ok_total += 1
                    else:
                        err_list.append(item)
                    rnd(1, 2.5)

                if not next_page(driver, page):
                    log.info("已到最後一頁。")
                    break
                page += 1
                sync_cookies(driver, session)

    except KeyboardInterrupt:
        log.info("使用者中止。")
    finally:
        driver.quit()

    print(f"\n{'='*60}")
    print(f"  成功下載：{ok_total} 份 PDF")
    print(f"  失敗筆數：{len(err_list)} 筆")
    print(f"  儲存位置：{OUTPUT_ROOT.resolve()}")
    print(f"{'='*60}")

    if err_list:
        with open("error_log.txt", "w", encoding="utf-8") as f:
            for x in err_list:
                f.write(str(x) + "\n")


if __name__ == "__main__":
    main()
