# ============================================================
# 模組一：保發中心海量 PDF 全自動下載爬蟲
# 目標：自動遍歷保發中心商品列表，篩選指定險種並下載條款 PDF
#
# 必備套件安裝指令：
#   pip install selenium webdriver-manager requests
#
# 執行前請確認：
#   1. 已安裝 Chrome 瀏覽器
#   2. webdriver-manager 會自動匹配 ChromeDriver 版本
# ============================================================

import os
import re
import time
import random
import logging
import requests
from pathlib import Path
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException
)
from webdriver_manager.chrome import ChromeDriverManager

# ─────────────────────────────────────────────
# 全域設定
# ─────────────────────────────────────────────
BASE_URL = "https://insprod.tii.org.tw/QueryFullText.aspx"

# 欲爬取的險種關鍵字對應保發中心下拉選單值（依實際頁面 HTML 調整）
TARGET_INSURANCE_TYPES = {
    "醫療險": ["住院醫療", "實支實付", "手術險", "醫療"],
    "癌症險": ["癌症"],
    "意外險": ["傷害", "意外"],
}

# 輸出根目錄
OUTPUT_ROOT = Path("./tii_pdfs")

# 每次請求之間的隨機延遲範圍（秒）
DELAY_MIN = 2.0
DELAY_MAX = 5.0

# 連線逾時設定（秒）
REQUEST_TIMEOUT = 30

# ─────────────────────────────────────────────
# 日誌設定
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scraper.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 工具函式
# ─────────────────────────────────────────────

def random_sleep(min_s: float = DELAY_MIN, max_s: float = DELAY_MAX) -> None:
    """隨機等待，模擬人類操作節奏，降低被封鎖風險"""
    delay = random.uniform(min_s, max_s)
    logger.debug(f"等待 {delay:.1f} 秒...")
    time.sleep(delay)


def sanitize_filename(name: str) -> str:
    """移除或替換檔名中不合法的字元"""
    # 替換 Windows / macOS 檔名不允許的特殊字元
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


def build_filename(company: str, product: str, approve_date: str) -> str:
    """
    依「保險公司_商品名稱_核准日期.pdf」格式組合檔名
    例：國泰人壽_附約防癌終身保險_20230815.pdf
    """
    date_clean = approve_date.replace("/", "").replace("-", "")
    filename = f"{sanitize_filename(company)}_{sanitize_filename(product)}_{date_clean}.pdf"
    return filename


def download_pdf(url: str, save_path: Path, session: requests.Session) -> bool:
    """
    以串流方式下載單一 PDF 檔案
    回傳 True 表示成功，False 表示失敗
    """
    try:
        response = session.get(url, stream=True, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        # 確認回應確實是 PDF（避免下載到錯誤頁面）
        content_type = response.headers.get("Content-Type", "")
        if "pdf" not in content_type.lower() and not url.lower().endswith(".pdf"):
            logger.warning(f"Content-Type 非 PDF：{content_type}，URL：{url}")

        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info(f"✅ 下載成功：{save_path.name}")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ 下載失敗：{url}，原因：{e}")
        return False


# ─────────────────────────────────────────────
# Selenium 瀏覽器初始化
# ─────────────────────────────────────────────

def init_driver() -> webdriver.Chrome:
    """
    初始化 Chrome WebDriver
    使用無頭模式（headless）以節省資源；
    若需觀察瀏覽器行為，可將 headless 設為 False
    """
    options = Options()
    options.add_argument("--headless=new")          # 無頭模式
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # 偽裝成一般使用者的 User-Agent
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60)
    return driver


# ─────────────────────────────────────────────
# 核心爬蟲邏輯
# ─────────────────────────────────────────────

class TIIScraper:
    """
    保發中心商品條款 PDF 爬蟲主類別
    負責：搜尋→分頁遍歷→解析列表→下載 PDF
    """

    def __init__(self, output_root: Path = OUTPUT_ROOT):
        self.driver = init_driver()
        self.wait = WebDriverWait(self.driver, 20)
        self.session = requests.Session()
        # 讓 requests 共用相同的 Cookie（來自 Selenium 登入狀態）
        self.output_root = output_root
        self.error_log: list[dict] = []     # 記錄失敗項目
        self.success_count = 0

    # ── 同步 Selenium Cookie 至 requests.Session ──────────────
    def _sync_cookies(self) -> None:
        """將 Selenium 的 Cookie 同步到 requests.Session"""
        self.session.cookies.clear()
        for cookie in self.driver.get_cookies():
            self.session.cookies.set(cookie["name"], cookie["value"])
        # 同步 Referer / User-Agent Header
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": BASE_URL,
        })

    # ── 開啟頁面並選擇險種 ────────────────────────────────────
    def search_by_type(self, insurance_type_value: str) -> None:
            """
            在保發中心查詢頁面選擇險種並送出查詢
            :param insurance_type_value: 下拉選單中的險種選項文字或 value
            """
            self.driver.get(BASE_URL)
            random_sleep(2.0, 4.0)  # 稍微加長等待時間，確保框架載入完全

            try:
                # =================================================================
                # ⭐ 核心診斷：列出並嘗試網頁上所有的 Frame
                # =================================================================
                frames = self.driver.find_elements(By.TAG_NAME, "frame")
                if not frames:
                    frames = self.driver.find_elements(By.TAG_NAME, "iframe")
                
                logger.info(f"[DIAGNOSIS] 偵測到網頁內包含 {len(frames)} 個框架頁面")
                
                frame_switched = False
                # 遍歷所有找到的框架，嘗試切換進去看看能不能找到選單
                for idx, frame in enumerate(frames):
                    try:
                        self.driver.switch_to.default_content() # 先回到最外層
                        self.driver.switch_to.frame(frame)
                        # 測試這個框架裡面有沒有我們要的下拉選單
                        self.driver.find_element(By.ID, "ctl00_ContentPlaceHolder1_ddlInsuranceType")
                        logger.info(f"[INFO] 成功在第 {idx} 個框架中定位到目標元素！")
                        frame_switched = True
                        break
                    except Exception:
                        continue
                
                if not frame_switched:
                    # 備援：如果都找不到，回到最外層硬碰硬
                    self.driver.switch_to.default_content()
                    logger.warning("[WARNING] 自動掃描框架失敗，嘗試停留在原網頁層級尋找元素")

                # ── 「險種」下拉選單操作 ──
                type_select_elem = self.wait.until(
                    EC.presence_of_element_located(
                        (By.ID, "ctl00_ContentPlaceHolder1_ddlInsuranceType")
                    )
                )
                select = Select(type_select_elem)
                
                # 嘗試用選項文字選取，若失敗則用 value 選取
                try:
                    select.select_by_visible_text(insurance_type_value)
                except NoSuchElementException:
                    select.select_by_value(insurance_type_value)
                    
                logger.info(f"已成功選取險種參數：{insurance_type_value}")
                random_sleep(0.5, 1.5)

                # ── 點擊「查詢」按鈕 ──
                search_btn = self.driver.find_element(
                    By.ID, "ctl00_ContentPlaceHolder1_btnSearch"
                )
                search_btn.click()
                logger.info("已點擊查詢按鈕，等待結果載入...")
                random_sleep(2.0, 4.0)

            except Exception as e:
                logger.error(f"查詢表單操作失敗：{e}")
                raise e

    # ── 點擊下一頁 ────────────────────────────────────────────
    def go_to_next_page(self) -> bool:
        """
        嘗試點擊「下一頁」按鈕
        回傳 True 表示成功翻頁，False 表示已到最後一頁
        """
        try:
            # 保發中心常見分頁結構：尋找包含「下一頁」或「>」的連結
            next_btn = self.driver.find_element(
                By.XPATH,
                "//a[contains(text(),'下一頁') or contains(text(),'>') or @title='下一頁']"
            )
            # 確認下一頁按鈕為可點擊狀態（非 disabled span）
            if next_btn.tag_name == "a":
                next_btn.click()
                random_sleep(2.0, 4.0)
                return True
        except NoSuchElementException:
            pass

        # 備援方案：尋找 ASP.NET 分頁的 __doPostBack 呼叫
        try:
            pager = self.driver.find_element(
                By.XPATH,
                "//tr[contains(@class,'pager') or contains(@class,'GridPager')]//a[last()]"
            )
            if pager.text.strip() in (">", "下一頁", "Next"):
                pager.click()
                random_sleep(2.0, 4.0)
                return True
        except NoSuchElementException:
            pass

        logger.info("已到達最後一頁")
        return False

    # ── 險種關鍵字過濾 ────────────────────────────────────────
    @staticmethod
    def is_target_type(insurance_type: str) -> bool:
        """
        判斷該商品是否屬於目標險種（醫療 / 癌症 / 意外）
        """
        target_keywords = [
            "醫療", "住院", "實支", "手術",
            "癌症", "重大疾病",
            "意外", "傷害",
        ]
        return any(kw in insurance_type for kw in target_keywords)

    # ── 主控流程 ──────────────────────────────────────────────
    def run(self, insurance_type_queries: list[str] | None = None) -> None:
        """
        主控爬蟲流程
        :param insurance_type_queries: 欲查詢的險種關鍵字清單；若為 None 則查詢所有險種
        """
        # 若未指定，使用預設三大目標險種
        if insurance_type_queries is None:
            insurance_type_queries = ["醫療", "癌症", "意外"]

        for query in insurance_type_queries:
            logger.info(f"{'='*50}")
            logger.info(f"開始爬取險種：{query}")
            logger.info(f"{'='*50}")

            try:
                self.search_by_type(query)
                self._sync_cookies()
            except Exception as e:
                logger.error(f"查詢「{query}」失敗，跳過：{e}")
                continue

            page_num = 1
            while True:
                logger.info(f"  正在解析第 {page_num} 頁...")
                items = self.parse_current_page()
                logger.info(f"  本頁找到 {len(items)} 筆商品")

                for item in items:
                    # 二次過濾：確認險種符合目標
                    if not self.is_target_type(item["insurance_type"]):
                        logger.debug(f"險種不符，跳過：{item['product']} ({item['insurance_type']})")
                        continue

                    # 建立儲存路徑：依險種分資料夾
                    type_dir = self.output_root / sanitize_filename(item["insurance_type"])
                    filename = build_filename(
                        item["company"], item["product"], item["approve_date"]
                    )
                    save_path = type_dir / filename

                    # 若已下載過，略過
                    if save_path.exists():
                        logger.info(f"  ⏭️  已存在，跳過：{filename}")
                        continue

                    # 下載 PDF
                    success = download_pdf(item["pdf_url"], save_path, self.session)
                    if success:
                        self.success_count += 1
                    else:
                        self.error_log.append(item)

                    # 每次下載後隨機延遲
                    random_sleep()

                # 嘗試翻頁
                if not self.go_to_next_page():
                    break
                page_num += 1

            logger.info(f"險種「{query}」爬取完畢")

        self._report()

    # ── 最終報告 ──────────────────────────────────────────────
    def _report(self) -> None:
        """輸出爬蟲執行摘要"""
        logger.info(f"\n{'='*50}")
        logger.info(f"爬蟲執行完畢")
        logger.info(f"成功下載：{self.success_count} 份 PDF")
        logger.info(f"失敗筆數：{len(self.error_log)} 筆")

        if self.error_log:
            error_path = Path("error_log.txt")
            with open(error_path, "w", encoding="utf-8") as f:
                for item in self.error_log:
                    f.write(f"{item}\n")
            logger.info(f"失敗清單已儲存至：{error_path}")

    def close(self) -> None:
        """關閉瀏覽器"""
        self.driver.quit()
        logger.info("瀏覽器已關閉")


# ─────────────────────────────────────────────
# 程式進入點
# ─────────────────────────────────────────────
if __name__ == "__main__":
    scraper = TIIScraper(output_root=OUTPUT_ROOT)
    try:
        scraper.run(insurance_type_queries=["醫療", "癌症", "意外"])
    except KeyboardInterrupt:
        logger.info("使用者中斷，正在關閉...")
    finally:
        scraper.close()
