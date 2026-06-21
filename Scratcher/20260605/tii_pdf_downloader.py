# ============================================================
# 模組一：保發中心海量 PDF 全自動下載爬蟲 (真實框架名稱對齊版)
# 目標：對齊保發中心右側真實框架名稱 "QUERY"，實現全自動選取與下載
# ============================================================

import os
import re
import time
import random
import logging
import requests
from pathlib import Path

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
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# 使用保發中心官方入口網址
BASE_URL = "https://insprod.tii.org.tw/QueryFullText.aspx"

# 欲爬取的險種關鍵字（對應下拉選單中的實際中文名稱）
TARGET_INSURANCE_TYPES = {
    "醫療": ["住院醫療", "實支實付", "手術險", "醫療"],
    "癌症": ["癌症"],
    "意外": ["傷害", "意外"],
}

OUTPUT_ROOT = Path("./tii_pdfs")

def random_sleep(min_time=1.5, max_time=3.5):
    time.sleep(random.uniform(min_time, max_time))

# ─────────────────────────────────────────────
# 爬蟲核心類別
# ─────────────────────────────────────────────
class TiiScraper:
    def __init__(self):
        self.success_count = 0
        self.error_log = []
        
        chrome_options = Options()
        # 保持實體瀏覽器開啟，方便觀察
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1400,900")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        self.wait = WebDriverWait(self.driver, 15)  
        self.session = requests.Session()

    def search_by_type(self, insurance_type_value: str) -> None:
        """
        開啟主頁面後，精確切換至右側名為 "QUERY" 的真實查詢框架
        """
        logger.info(f"[INFO] 正在開啟保發中心入口網頁...")
        self.driver.get(BASE_URL)
        random_sleep(3.5, 5.0) # 給網頁完整的載入時間

        try:
            logger.info("[INFO] 正在嘗試穿透保發中心核心框架頁面...")
            self.driver.switch_to.default_content()
            
            # ⭐⭐⭐ 核心修正：保發中心右側主畫面的真實名稱叫做 "QUERY" 或 "query"
            try:
                self.driver.switch_to.frame("QUERY")
                logger.info("[INFO] 成功切換進入官方查詢框架 (Name: 'QUERY')！")
            except Exception:
                try:
                    self.driver.switch_to.frame("query")
                    logger.info("[INFO] 成功切換進入官方查詢框架 (Name: 'query')！")
                except Exception:
                    try:
                        self.driver.switch_to.frame(1)
                        logger.info("[INFO] 成功切換進入第 1 個索引的框架！")
                    except Exception:
                        logger.warning("[WARNING] 無法切換框架，嘗試直接在當前層級尋找元件...")

            # ── 嘗試尋找並操作「險種」下拉選單 ──
            try:
                type_select_elem = self.wait.until(
                    EC.presence_of_element_located((By.ID, "ctl00_ContentPlaceHolder1_ddlInsuranceType"))
                )
            except TimeoutException:
                type_select_elem = self.wait.until(
                    EC.presence_of_element_located((By.ID, "insuranceType"))
                )

            select = Select(type_select_elem)
            try:
                select.select_by_visible_text(insurance_type_value)
            except NoSuchElementException:
                select.select_by_value(insurance_type_value)
                
            logger.info(f"[INFO] 成功選取險種參數：{insurance_type_value}")
            random_sleep(1.0, 2.0)

            # ── 點擊查詢按鈕 ──
            try:
                search_btn = self.driver.find_element(By.ID, "ctl00_ContentPlaceHolder1_btnSearch")
            except NoSuchElementException:
                search_btn = self.driver.find_element(By.ID, "searchButton")
                
            search_btn.click()
            logger.info("[INFO] 已點擊查詢按鈕，正在等待保險商品清單載入...")
            random_sleep(4.0, 6.0)  

        except Exception as e:
            logger.error(f"[ERROR] 查詢表單操作失敗，原因：{e}")
            raise e

    def parse_current_page_items(self):
        """解析目前分頁商品清單的 PDF 連結"""
        items = []
        try:
            try:
                table = self.driver.find_element(By.ID, "ctl00_ContentPlaceHolder1_GridView1")
            except NoSuchElementException:
                table = self.driver.find_element(By.ID, "resultTable")
                
            rows = table.find_elements(By.TAG_NAME, "tr")[1:]  # 跳過表頭
            
            for row in rows:
                try:
                    cols = row.find_elements(By.TAG_NAME, "td")
                    if len(cols) < 5:
                        continue
                    
                    company = cols[1].text.strip()
                    product_name = cols[2].text.strip()
                    
                    links = cols[4].find_elements(By.TAG_NAME, "a")
                    pdf_url = ""
                    for link in links:
                        href = link.get_attribute("href")
                        if href and ".pdf" in href.lower():
                            pdf_url = href
                            break
                    
                    if pdf_url and product_name:
                        items.append({
                            "company": company,
                            "product_name": product_name,
                            "pdf_url": pdf_url
                        })
                except StaleElementReferenceException:
                    continue
                except Exception as e:
                    logger.error(f"[ERROR] 解析單一商品欄位時遭遇異常: {e}")
                    continue
        except NoSuchElementException:
            logger.warning("[WARNING] 找不到結果表格，此類別目前可能查無資料。")
        return items

    def go_to_next_page(self) -> bool:
        """自動點擊下一頁分頁按鈕"""
        try:
            try:
                table = self.driver.find_element(By.ID, "ctl00_ContentPlaceHolder1_GridView1")
            except NoSuchElementException:
                table = self.driver.find_element(By.ID, "resultTable")
                
            pager_row = table.find_elements(By.TAG_NAME, "tr")[-1]
            next_page_links = pager_row.find_elements(By.TAG_NAME, "a")
            
            for link in next_page_links:
                if "next" in link.text.lower() or ">" in link.text:
                    link.click()
                    logger.info("[INFO] 成功點擊下一頁分頁，載入中...")
                    random_sleep(2.5, 4.0)
                    return True
        except Exception:
            pass
        return False

    def download_pdf(self, url: str, save_path: Path, session) -> bool:
        """將 PDF 串流穩定下載至本地專案目錄"""
        try:
            response = session.get(url, timeout=30, stream=True)
            if response.status_code == 200:
                with open(save_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                logger.info(f"【下載成功】→ {save_path.name}")
                return True
            else:
                logger.error(f"[HTTP {response.status_code}] 下載失敗：{url}")
        except Exception as e:
            logger.error(f"[DOWNLOAD ERROR] 下載連線中斷：{e}")
        return False

    def run(self):
        """執行大批次全自動下載流"""
        for label, keywords in TARGET_INSURANCE_TYPES.items():
            logger.info(f"\n{'='*60}\n【系統啟動】開始批次爬取險種分流：{label}\n{'='*60}")
            insurance_dir = OUTPUT_ROOT / label
            insurance_dir.mkdir(parents=True, exist_ok=True)
            
            query_keyword = keywords[0]
            try:
                self.search_by_type(query_keyword)
            except Exception:
                logger.error(f"[SKIP] 查詢「{label}」險種大類失敗，跳過此類別")
                continue

            # 同步最新視窗的 Cookies 給 Requests 確保下載權限
            for cookie in self.driver.get_cookies():
                self.session.cookies.set(cookie['name'], cookie['value'])

            page_num = 1
            while True:
                logger.info(f"正在掃描險種「{label}」第 {page_num} 頁之商品目錄...")
                items = self.parse_current_page_items()
                
                if not items:
                    logger.info("[INFO] 已無更多可下載的商品資料")
                    break
                
                for item in items:
                    safe_product = re.sub(r'[\\/*?:"<>|]', "", item["product_name"])
                    safe_company = re.sub(r'[\\/*?:"<>|]', "", item["company"])
                    filename = f"{safe_company}_{safe_product}.pdf"
                    save_path = insurance_dir / filename
                    
                    if save_path.exists():
                        logger.info(f"檔案先前已下載，跳過：{filename}")
                        continue
                    
                    success = self.download_pdf(item["pdf_url"], save_path, self.session)
                    if success:
                        self.success_count += 1
                    else:
                        self.error_log.append(item)

                    random_sleep(1.0, 2.5)

                if not self.go_to_next_page():
                    break
                page_num += 1

            logger.info(f"險種「{label}」大類全數解鎖下載完畢！")

        self._report()

    def _report(self) -> None:
        logger.info(f"\n{'='*60}\n【爬蟲任務執行摘要】")
        logger.info(f" 成功下載條款 PDF：{self.success_count} 份")
        logger.info(f" 失敗或毀損筆數：{len(self.error_log)} 筆")
        logger.info(f"{'='*60}")

        if self.error_log:
            error_path = Path("error_log.txt")
            with open(error_path, "w", encoding="utf-8") as f:
                for item in self.error_log:
                    f.write(f"{item}\n")

    def close(self) -> None:
        self.driver.quit()
        logger.info("[INFO] 核心瀏覽器服務已安全關閉。")


if __name__ == "__main__":
    scraper = TiiScraper()
    try:
        scraper.run()
    finally:
        scraper.close()