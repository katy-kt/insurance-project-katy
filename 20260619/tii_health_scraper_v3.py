"""
保發中心健康保險商品 PDF 下載爬蟲 v3
修正（根據 debug_form_page.html 實際結構分析）：
  1. 此網站查詢用 <form name="form1" method="post"> 送出，
     送出後瀏覽器網址列不會改變，因此改用「查詢表單欄位是否消失」
     加上「結果頁關鍵字是否出現」來判斷是否已進入結果頁，
     不再依賴 URL 變化。
  2. 自動勾選「未停售」checkbox
  3. 結果列表頁的商品連結改用 requests.Session()（已同步瀏覽器 cookies）
     直接抓取詳細頁內容，瀏覽器全程留在結果列表頁不離開，
     避免因為這個網站沒有可重複造訪的結果頁 URL 而導致無法回頭。
  4. 偵測到「識別碼錯誤」alert 會自動關閉，並提示重新輸入，
     在等待時限內可以重試多次。
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
    此網站用 POST 送出查詢、URL 不會改變，
    改用「查詢欄位是否已消失」判斷是否進入結果頁。
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

    # ── 6. 等待查詢送出後的結果頁，同時處理「識別碼錯誤」alert ──
    #
    # 重要發現（來自 debug_form_page.html 分析）：
    #   <form name="form1" method="post" action="ResultQueryAll.aspx"></form>
    # 這個網站用 POST 送出查詢，瀏覽器位址列「不會」變成 ResultQueryAll.aspx
    # （傳統 form POST 後 URL 通常還是顯示原本網址，或維持 Query.aspx）。
    # 因此不能用「URL 是否包含 ResultQueryAll」當判斷依據，
    # 必須改成偵測「查詢表單欄位是否已從畫面上消失，換成商品清單表格」。
    #
    # 判斷方式：
    #   (a) 若出現 alert「識別碼錯誤」→ 關閉並繼續等待使用者重填
    #   (b) 若頁面的 <input name="bmpC"> 驗證碼欄位已經不存在 → 代表頁面已換成結果頁
    #   (c) 若頁面同時出現「商品名稱」等表格關鍵字 → 雙重確認進入結果頁
    from selenium.common.exceptions import UnexpectedAlertPresentException

    deadline = time.time() + CAPTCHA_WAIT
    attempt  = 0

    while time.time() < deadline:
        # 優先檢查是否有 alert 需要關閉
        try:
            alert = driver.switch_to.alert
            msg   = alert.text
            alert.accept()          # 關閉 alert（點確定）
            attempt += 1
            log.warning(f"  ⚠ 捕捉到 alert：{msg!r}，已關閉（第 {attempt} 次）")
            print(f"\n  ❌ 識別碼錯誤，請重新填入驗證碼並點「開始查詢」"
                  f"（剩餘 {int(deadline - time.time())} 秒）\n")
            time.sleep(1)
            continue
        except Exception:
            pass  # 沒有 alert，繼續往下檢查頁面是否已換成結果頁

        # 檢查查詢表單欄位是否還存在
        try:
            form_still_present = len(driver.find_elements(By.NAME, "bmpC")) > 0
        except UnexpectedAlertPresentException:
            try:
                driver.switch_to.alert.accept()
            except Exception:
                pass
            continue
        except Exception:
            form_still_present = True  # 讀取失敗就當作還沒換頁，保險起見

        if not form_still_present:
            # 表單欄位消失了，很可能已經換成結果頁，再次確認頁面內容
            try:
                src = driver.page_source
            except UnexpectedAlertPresentException:
                try:
                    driver.switch_to.alert.accept()
                except Exception:
                    pass
                continue

            if any(kw in src for kw in ["商品名稱", "保險商品名稱", "查無資料", "共", "筆"]):
                log.info("  ✅ 偵測到查詢表單已消失、結果頁特徵出現")
                rnd(2, 3)
                return True

        time.sleep(1)

    log.error("  ⏰ 等待超時，跳過此類別")
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
def extract_pdf_from_detail(session, product_url):
    """
    用 requests（已同步瀏覽器 cookies）抓商品詳細頁，找保單條款 PDF。
    不使用 driver.get()，避免瀏覽器離開結果列表頁導致無法翻頁／重新整理。
    回傳 list of {label, pdf_url}
    """
    try:
        resp = session.get(product_url, timeout=20)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        log.error(f"  無法取得詳細頁：{e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    pdfs = []

    # 找所有 .pdf 連結
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" not in href.lower():
            continue
        full = href if href.startswith("http") else "https://insprod.tii.org.tw" + href
        label = a.get_text(strip=True) or a.get("title", "")

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

    if not pdfs:
        fname = "debug_detail_" + re.sub(r"[^\w]", "_", product_url[-30:]) + ".html"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(html)
        log.warning(f"  詳細頁找不到 PDF，已存 {fname}")

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

            log.info(f"目前頁面：{driver.title!r}")

            # ── 收集所有商品詳細頁連結（含翻頁，瀏覽器全程留在結果列表頁）──
            sync_cookies(driver, session)
            product_links = collect_product_links(driver)

            if not product_links:
                log.warning("沒有找到任何商品，請確認結果頁結構，檢查 debug_result_list.html")
                continue

            # ── 逐筆用 requests 抓詳細頁、下載保單條款 PDF ──
            # 注意：這裡改用 session.get() 而非 driver.get()，
            # 因為這個網站查詢是 POST 送出、結果頁沒有獨立可重複造訪的 URL，
            # 若用 driver.get() 離開結果列表頁將無法回頭，所以瀏覽器不動，
            # 全部後續抓取都透過已同步 cookies 的 requests session 進行。
            for idx, product in enumerate(product_links, 1):
                log.info(f"[{idx}/{len(product_links)}] {product['name']!r}")

                pdfs = extract_pdf_from_detail(session, product["url"])

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

                    if download_pdf(url, path, session):
                        ok_total += 1
                    else:
                        err_list.append({"product": product["name"], "pdf_url": url})

                    rnd(0.8, 2.0)

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
