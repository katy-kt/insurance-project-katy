"""
保發中心健康保險商品 PDF 下載爬蟲 v3
修正：
  1. 結果頁偵測改為等 URL 出現 ResultQueryAll.aspx
  2. 自動勾選「未停售」checkbox
  3. 進結果頁後逐筆點開商品詳細頁，找保單條款 PDF 下載
"""

import re
import time
import random
import logging
import requests
from pathlib import Path

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
CAPTCHA_WAIT = 180    # 等使用者填驗證碼上限秒數

# 要抓的保險類別（對應頁面選項文字）
TARGET_CATEGORIES = ["健康保險"]

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


def safe_name(s, max_len=80):
    return re.sub(r'[\\/*?:"<>|]', "", str(s)).strip()[:max_len]


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
# 填表
# ══════════════════════════════════════════════════════════
def setup_query_form(driver, category_text):
    """
    自動選好所有選項並勾選「未停售」，
    然後暫停等使用者填驗證碼並送出查詢。
    等到 URL 變成 ResultQueryAll.aspx 才繼續。
    """
    log.info(f"開啟查詢頁面，準備類別：{category_text!r}")
    driver.get(BASE_URL)
    rnd(3, 5)

    wait = WebDriverWait(driver, 15)

    # ── 1. 公司類別 → 人身保險 ──────────────────────────
    try:
        Select(wait.until(EC.presence_of_element_located(
            (By.NAME, "categoryId")
        ))).select_by_visible_text("人身保險")
        log.info("  ✓ 公司類別 = 人身保險")
        rnd(0.8, 1.5)
    except Exception as e:
        log.error(f"  找不到 categoryId：{e}")
        return False

    # ── 2. 公司名稱 → 壽險全部 ──────────────────────────
    try:
        WebDriverWait(driver, 10).until(
            lambda d: any(
                "壽險全部" in o.text
                for o in Select(d.find_element(By.NAME, "CompanyID")).options
            )
        )
        sel = Select(driver.find_element(By.NAME, "CompanyID"))
        target = next((o for o in sel.options if "壽險全部" in o.text), None)
        if target:
            sel.select_by_visible_text(target.text)
            log.info(f"  ✓ 公司名稱 = {target.text!r}")
        rnd(0.8, 1.5)
    except Exception as e:
        log.warning(f"  CompanyID 保持預設：{e}")

    # ── 3. 保險類別 ──────────────────────────────────────
    try:
        Select(driver.find_element(By.NAME, "f_CategoryId1")
               ).select_by_visible_text(category_text)
        log.info(f"  ✓ 保險類別 = {category_text!r}")
        rnd(0.5, 1.0)
    except Exception as e:
        log.error(f"  找不到 f_CategoryId1：{e}")
        return False

    # ── 4. 勾選「未停售」checkbox ────────────────────────
    try:
        cb = driver.find_element(By.ID, "endDate2")
        if not cb.is_selected():
            cb.click()
            log.info("  ✓ 已勾選「未停售」")
        else:
            log.info("  ✓ 「未停售」已是勾選狀態")
        rnd(0.3, 0.8)
    except NoSuchElementException:
        log.warning("  找不到「未停售」checkbox，略過")

    # ── 5. 提示使用者填驗證碼 ────────────────────────────
    print("\n" + "="*55)
    print(f"  已自動設定：人身保險 / 壽險全部 / {category_text} / 未停售")
    print("  請在瀏覽器中：")
    print("    1. 在「查詢識別碼」欄填入圖形驗證碼")
    print("    2. 點擊「開始查詢」按鈕")
    print(f"  等候最多 {CAPTCHA_WAIT} 秒...")
    print("="*55 + "\n")

    # ── 6. 等待跳轉到結果頁（URL 一定包含 ResultQueryAll）
    try:
        WebDriverWait(driver, CAPTCHA_WAIT).until(
            lambda d: "ResultQueryAll" in d.current_url
        )
        log.info(f"  ✅ 進入結果頁：{driver.current_url}")
        rnd(2, 3)
        return True
    except TimeoutException:
        log.error("  ⏰ 等待超時，跳過此類別")
        # 存 debug
        with open("debug_form_page.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        return False


# ══════════════════════════════════════════════════════════
# 結果列表頁：收集所有商品的詳細頁連結
# ══════════════════════════════════════════════════════════
def collect_product_links(driver):
    """
    解析結果列表頁，回傳所有商品詳細頁的 URL。
    同時處理翻頁，直到最後一頁。
    """
    all_links = []
    page = 1

    while True:
        log.info(f"  掃描結果列表第 {page} 頁...")
        rnd(1, 2)
        soup = BeautifulSoup(driver.page_source, "html.parser")

        # 找所有連到詳細頁的 <a>（href 含 QueryDetail 或 ProductDetail）
        links_on_page = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(kw in href for kw in ["QueryDetail", "ProductDetail", "Detail"]):
                full = href if href.startswith("http") else "https://insprod.tii.org.tw" + href
                name = a.get_text(strip=True)
                links_on_page.append({"name": name, "url": full})

        # 若沒找到詳細頁連結，改找所有 <tr> 並嘗試抓點擊事件
        if not links_on_page:
            for a in soup.find_all("a"):
                onclick = a.get("onclick", "")
                m = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", onclick)
                if m:
                    href = m.group(1)
                    full = href if href.startswith("http") else "https://insprod.tii.org.tw" + href
                    links_on_page.append({"name": a.get_text(strip=True), "url": full})

        log.info(f"    第 {page} 頁找到 {len(links_on_page)} 個商品連結")

        # 如果還是 0，存 debug 看看
        if not links_on_page and page == 1:
            with open("debug_result_list.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            log.warning("    找不到任何商品連結，已存 debug_result_list.html")
            # 印前 50 個連結供診斷
            log.info("    頁面所有 <a> 連結：")
            for a in soup.find_all("a")[:50]:
                log.info(f"      href={a.get('href')!r} onclick={a.get('onclick','')[:60]!r} text={a.get_text(strip=True)[:30]!r}")
            break

        all_links.extend(links_on_page)

        # 翻頁
        if not _go_next_page(driver, page):
            break
        page += 1

    log.info(f"  共收集 {len(all_links)} 個商品詳細頁連結")
    return all_links


def _go_next_page(driver, current_page):
    for text in ["下一頁", "次頁", ">", ">>"]:
        try:
            driver.find_element(By.LINK_TEXT, text).click()
            rnd(2, 3)
            return True
        except NoSuchElementException:
            pass
    try:
        driver.find_element(By.LINK_TEXT, str(current_page + 1)).click()
        rnd(2, 3)
        return True
    except NoSuchElementException:
        pass
    return False


# ══════════════════════════════════════════════════════════
# 商品詳細頁：找保單條款 PDF
# ══════════════════════════════════════════════════════════
def extract_pdf_from_detail(driver, product_url):
    """
    進入商品詳細頁，找「保單條款」類別的 PDF 連結。
    回傳 list of {label, pdf_url}
    """
    try:
        driver.get(product_url)
        rnd(1.5, 3)
    except Exception as e:
        log.error(f"  無法開啟詳細頁：{e}")
        return []

    soup = BeautifulSoup(driver.page_source, "html.parser")
    pdfs = []

    # 找所有 .pdf 連結
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" not in href.lower():
            continue
        full = href if href.startswith("http") else "https://insprod.tii.org.tw" + href
        label = a.get_text(strip=True) or a.get("title", "")

        # 判斷是否為保單條款（優先）
        context = ""
        parent = a.find_parent(["td", "li", "div", "p"])
        if parent:
            context = parent.get_text(" ", strip=True)

        is_clause = any(kw in label + context for kw in ["條款", "保單", "clause"])
        pdfs.append({"label": label, "pdf_url": full, "is_clause": is_clause})

    # 也找 onclick 型連結
    for a in soup.find_all("a", onclick=True):
        onclick = a["onclick"]
        m = re.search(r"['\"]([^'\"]*\.pdf[^'\"]*)['\"]", onclick, re.I)
        if m:
            href = m.group(1)
            full = href if href.startswith("http") else "https://insprod.tii.org.tw" + href
            label = a.get_text(strip=True)
            is_clause = any(kw in label for kw in ["條款", "保單"])
            pdfs.append({"label": label, "pdf_url": full, "is_clause": is_clause})

    # 若沒找到，存 debug
    if not pdfs:
        fname = "debug_detail_" + re.sub(r"[^\w]", "_", product_url[-30:]) + ".html"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        log.warning(f"  詳細頁找不到 PDF，已存 {fname}")

    # 只回傳保單條款；若沒有標記為條款的，回傳全部
    clause_pdfs = [p for p in pdfs if p["is_clause"]]
    return clause_pdfs if clause_pdfs else pdfs


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
        "Referer": "https://insprod.tii.org.tw/",
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

            # ── 填表等驗證碼 ──────────────────────────────
            if not setup_query_form(driver, category):
                continue

            result_list_url = driver.current_url
            log.info(f"結果列表 URL：{result_list_url}")

            # ── 收集所有商品詳細頁連結（含翻頁）────────────
            sync_cookies(driver, session)
            product_links = collect_product_links(driver)

            if not product_links:
                log.warning("沒有找到任何商品，請確認結果頁結構，檢查 debug_result_list.html")
                continue

            # ── 逐筆進詳細頁下載保單條款 PDF ──────────────
            for idx, product in enumerate(product_links, 1):
                log.info(f"[{idx}/{len(product_links)}] {product['name']!r}")

                sync_cookies(driver, session)
                pdfs = extract_pdf_from_detail(driver, product["url"])

                if not pdfs:
                    log.warning(f"  此商品無 PDF")
                    err_list.append({"product": product["name"], "reason": "no PDF found"})
                    continue

                for pdf in pdfs:
                    url   = pdf["pdf_url"]
                    label = safe_name(pdf["label"]) or "條款"
                    pname = safe_name(product["name"])
                    fname = f"{pname}_{label}.pdf"
                    path  = save_dir / fname

                    if path.exists():
                        log.info(f"  已存在，略過：{fname}")
                        continue

                    sync_cookies(driver, session)
                    if download_pdf(url, path, session):
                        ok_total += 1
                    else:
                        err_list.append({"product": product["name"], "pdf_url": url})

                    rnd(0.8, 2.0)

                # 每 10 筆回結果列表重新同步一次 session
                if idx % 10 == 0:
                    driver.get(result_list_url)
                    rnd(1, 2)
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
        log.info("失敗清單已存至 error_log.txt")


if __name__ == "__main__":
    main()
