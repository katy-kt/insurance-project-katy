"""
保發中心健康保險 PDF 下載爬蟲 v4
==================================
根據真實頁面結構確認的完整流程：

1. 結果列表頁 URL：ResultQueryAll.aspx（POST 後的頁面，URL 不會變）
2. 商品連結格式：DetailList.aspx?productId=XXXX（相對路徑）
3. 詳細頁 URL：https://insprod.tii.org.tw/DetailList.aspx?productId=XXXX
4. 詳細頁內有 PDF 連結，用 requests 抓詳細頁 HTML 再找 .pdf href
5. 翻頁連結：ResultQueryAll.aspx?page=N（可直接 GET）

修正的核心 bug：
  - URL 拼接改用 urllib.parse.urljoin，正確處理相對路徑
  - 翻頁改成直接 GET ResultQueryAll.aspx?page=N（不用 Selenium 點擊）
  - extract_pdf 加強 debug：request 失敗也存 debug 檔案
"""

import re
import time
import random
import logging
import requests
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    NoSuchElementException, UnexpectedAlertPresentException
)

try:
    from webdriver_manager.chrome import ChromeDriverManager
    _service = Service(ChromeDriverManager().install())
except ImportError:
    _service = Service()

# ══════════════════════════════════════════════
# 設定區
# ══════════════════════════════════════════════
BASE_URL       = "https://insprod.tii.org.tw/Query.aspx"
SITE_ROOT      = "https://insprod.tii.org.tw/"
OUTPUT_ROOT    = Path("./tii_pdfs")
CAPTCHA_WAIT   = 180
TARGET_CATEGORIES = ["健康保險"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("tii_scraper.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def rnd(a=1.0, b=2.5):
    time.sleep(random.uniform(a, b))


def safe_name(s, n=80):
    return re.sub(r'[\\/*?:"<>|]', "", str(s)).strip()[:n]


# ══════════════════════════════════════════════
# WebDriver
# ══════════════════════════════════════════════
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
    return driver


# ══════════════════════════════════════════════
# 填表 + 等結果頁
# ══════════════════════════════════════════════
def setup_and_wait(driver, category_text):
    """
    自動填好查詢表單，等使用者填驗證碼、按查詢。
    用「查詢表單欄位（bmpC）是否消失」偵測已進入結果頁。
    回傳 True 表示成功進入結果頁。
    """
    log.info(f"開啟查詢頁：{BASE_URL}")
    driver.get(BASE_URL)
    rnd(3, 5)

    # 公司類別 → 人身保險
    try:
        Select(driver.find_element(By.NAME, "categoryId")
               ).select_by_visible_text("人身保險")
        rnd(1, 1.5)
    except Exception as e:
        log.error(f"選擇公司類別失敗：{e}")
        return False

    # 公司名稱 → 壽險全部
    try:
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        WebDriverWait(driver, 10).until(
            lambda d: any("壽險全部" in o.text
                          for o in Select(d.find_element(By.NAME, "CompanyID")).options)
        )
        sel = Select(driver.find_element(By.NAME, "CompanyID"))
        t = next((o for o in sel.options if "壽險全部" in o.text), None)
        if t:
            sel.select_by_visible_text(t.text)
        rnd(0.8, 1.2)
    except Exception as e:
        log.warning(f"選擇公司名稱失敗，保持預設：{e}")

    # 保險類別
    try:
        Select(driver.find_element(By.NAME, "f_CategoryId1")
               ).select_by_visible_text(category_text)
        rnd(0.5, 0.8)
    except Exception as e:
        log.error(f"選擇保險類別失敗：{e}")
        return False

    # 勾選「未停售」
    try:
        cb = driver.find_element(By.ID, "endDate2")
        if not cb.is_selected():
            cb.click()
        rnd(0.3, 0.5)
    except NoSuchElementException:
        log.warning("找不到未停售 checkbox")

    print("\n" + "="*55)
    print(f"  已自動設定：人身保險 / 壽險全部 / {category_text} / 未停售")
    print("  請填入驗證碼並點「開始查詢」")
    print(f"  等候最多 {CAPTCHA_WAIT} 秒（填錯會自動關 alert 讓你重試）")
    print("="*55 + "\n")

    deadline = time.time() + CAPTCHA_WAIT
    while time.time() < deadline:
        # 處理「識別碼錯誤」alert
        try:
            alert = driver.switch_to.alert
            msg = alert.text
            alert.accept()
            log.warning(f"  alert 已關閉：{msg!r}，請重新填驗證碼")
            print(f"  ❌ 識別碼錯誤，請重新填驗證碼（剩 {int(deadline-time.time())} 秒）\n")
            time.sleep(1)
            continue
        except Exception:
            pass

        # 偵測查詢表單是否已消失（代表已進入結果頁）
        try:
            form_gone = len(driver.find_elements(By.NAME, "bmpC")) == 0
        except UnexpectedAlertPresentException:
            try:
                driver.switch_to.alert.accept()
            except Exception:
                pass
            continue

        if form_gone:
            log.info("  ✅ 查詢表單已消失，進入結果頁")
            rnd(2, 3)
            return True

        time.sleep(1)

    log.error("  ⏰ 等待超時")
    return False


# ══════════════════════════════════════════════
# 同步 cookies
# ══════════════════════════════════════════════
def sync_cookies(driver, session):
    session.cookies.clear()
    for c in driver.get_cookies():
        session.cookies.set(c["name"], c["value"])
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": SITE_ROOT,
    })
    session.verify = False   # 停用 SSL 憑證驗證（Windows 常見 CA bundle 缺失問題）


# ══════════════════════════════════════════════
# 收集商品列表（用 Selenium 翻頁）
# ══════════════════════════════════════════════
def collect_all_product_links(driver, session):
    """
    從當前結果頁開始，逐頁收集所有 DetailList.aspx?productId=... 連結。
    翻頁用 session.get(ResultQueryAll.aspx?page=N)（比 Selenium 點擊更穩定）。
    """
    all_products = []
    page = 1

    while True:
        log.info(f"  掃描第 {page} 頁...")
        html = driver.page_source
        products_on_page = _parse_product_links(html)
        log.info(f"    找到 {len(products_on_page)} 個商品")

        if not products_on_page:
            if page == 1:
                # 第一頁就沒東西，存 debug
                with open("debug_result_list.html", "w", encoding="utf-8") as f:
                    f.write(html)
                log.warning("    第 1 頁找不到商品！已存 debug_result_list.html")
            break

        all_products.extend(products_on_page)

        # 找下一頁連結
        next_page_url = _find_next_page_url(html, page)
        if not next_page_url:
            log.info("  已到最後一頁")
            break

        # 用 Selenium 直接 GET 下一頁（ResultQueryAll.aspx?page=N 可以 GET）
        full_next = urljoin(SITE_ROOT, next_page_url)
        log.info(f"  前往第 {page+1} 頁：{full_next}")
        driver.get(full_next)
        rnd(1.5, 2.5)
        sync_cookies(driver, session)
        page += 1

    log.info(f"共收集 {len(all_products)} 個商品連結")
    return all_products


def _parse_product_links(html):
    """從 HTML 解析 DetailList.aspx 連結，回傳 [{name, url}]"""
    soup = BeautifulSoup(html, "html.parser")
    products = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "DetailList.aspx" in href and "productId=" in href:
            full_url = urljoin(SITE_ROOT, href)   # 正確處理相對路徑
            name = a.get_text(strip=True)
            products.append({"name": name, "url": full_url})
    return products


def _find_next_page_url(html, current_page):
    """找 ResultQueryAll.aspx?page=N（N = current_page+1）的連結"""
    soup = BeautifulSoup(html, "html.parser")
    next_num = current_page + 1
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if f"ResultQueryAll.aspx?page={next_num}" in href:
            return href
    return None


# ══════════════════════════════════════════════
# 抓詳細頁 PDF 連結
# ══════════════════════════════════════════════
def extract_pdfs(detail_url, session):
    """
    抓詳細頁，只取：
      - 保險商品內容說明（-F.pdf）
      - 保單條款（-A.pdf）
    其他類型（-B/-C/-E/-K 等）略過。
    """
    try:
        resp = session.get(detail_url, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        log.error(f"  詳細頁取得失敗：{e}  URL={detail_url}")
        pid = detail_url.split("productId=")[-1][:40]
        with open(f"debug_detail_{pid}.html", "w", encoding="utf-8") as f:
            f.write(f"<!-- request failed: {e} -->\n<!-- url: {detail_url} -->")
        return []

    html = resp.text

    pattern = re.compile(
        r'href=(Open2\.ashx\?id=[0-9a-f\-]+)>([^<]*\.pdf)',
        re.IGNORECASE
    )
    matches = pattern.findall(html)

    if not matches:
        pid = detail_url.split("productId=")[-1][:40]
        fname = f"debug_detail_{pid}.html"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(html)
        log.warning(f"  詳細頁無 Open2.ashx 連結，已存 {fname}")
        return []

    TARGET_SUFFIXES = {
        "-A.pdf": "保單條款",
        "-F.pdf": "保險商品內容說明",
    }

    pdfs = []
    for ashx_path, filename in matches:
        # 只保留 -A.pdf 和 -F.pdf
        suffix = next((s for s in TARGET_SUFFIXES if filename.upper().endswith(s.upper())), None)
        if suffix is None:
            continue
        full_url = urljoin(SITE_ROOT, ashx_path)
        pdfs.append({
            "label":    TARGET_SUFFIXES[suffix],  # 「保單條款」或「保險商品內容說明」
            "filename": filename,                  # 原始檔名，例如 201311AZ...-A.pdf
            "url":      full_url,
            "subfolder": TARGET_SUFFIXES[suffix],  # 用來建子資料夾
        })

    return pdfs


# ══════════════════════════════════════════════
# 下載 PDF
# ══════════════════════════════════════════════
def download_pdf(url, path, session):
    try:
        r = session.get(url, timeout=30, stream=True)
        if r.status_code == 200:
            # 確認是 PDF（有些網站 200 但回傳 HTML 錯誤頁）
            content_type = r.headers.get("Content-Type", "")
            if "html" in content_type.lower():
                log.warning(f"  回傳 HTML 而非 PDF，略過：{url}")
                return False
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


# ══════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════
def main():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    driver  = build_driver()
    session = requests.Session()
    session.verify = False   # 停用 SSL 憑證驗證
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # 壓掉警告訊息
    ok_total, err_list = 0, []

    try:
        for category in TARGET_CATEGORIES:
            log.info(f"\n{'='*60}\n類別：{category}\n{'='*60}")
            save_dir = OUTPUT_ROOT / safe_name(category)
            save_dir.mkdir(parents=True, exist_ok=True)

            if not setup_and_wait(driver, category):
                continue

            sync_cookies(driver, session)

            # 收集所有商品連結（含翻頁）
            products = collect_all_product_links(driver, session)
            if not products:
                log.warning("沒有找到任何商品")
                continue

            # 逐筆下載保單條款 PDF
            for idx, product in enumerate(products, 1):
                log.info(f"[{idx}/{len(products)}] {product['name']!r}")

                pdfs = extract_pdfs(product["url"], session)
                if not pdfs:
                    err_list.append({"product": product["name"], "reason": "no PDF found"})
                    continue

                for pdf in pdfs:
                    # 子資料夾：tii_pdfs/健康保險/保單條款/ 或 /保險商品內容說明/
                    sub_dir = save_dir / pdf["subfolder"]
                    sub_dir.mkdir(parents=True, exist_ok=True)

                    # 用「商品名稱_原始檔名.pdf」命名，原始檔名已含 .pdf 不重複加
                    pname = safe_name(product["name"])
                    fname = f"{pname}_{pdf['filename']}"
                    path  = sub_dir / fname

                    if path.exists():
                        log.info(f"  已存在，略過：{fname}")
                        continue

                    if download_pdf(pdf["url"], path, session):
                        ok_total += 1
                    else:
                        err_list.append({"product": product["name"], "pdf_url": pdf["url"]})

                    rnd(0.5, 1.5)

                # 每 20 筆重新同步 cookies
                if idx % 20 == 0:
                    sync_cookies(driver, session)

    except KeyboardInterrupt:
        log.info("使用者中止")
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
