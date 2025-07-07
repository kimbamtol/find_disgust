import json
import re
import sys
import time
import logging
import statistics
from pathlib import Path
from urllib.parse import urljoin
from typing import Dict, List

import requests
import bs4
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ── 설정 ─────────────────────────────────────
USE_SELENIUM_FOR_LIST = True  # 목록도 selenium으로 가져올지 여부

# ── 로그 설정 ────────────────────────────────
logging.basicConfig(
    filename="crawl_errors.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8"
)

# ── Selenium 초기화 ─────────────────────────
CHROMEDRIVER = r"C:\Users\OptLab\Desktop\tori\My논문\GECCO_2025\New_TSP_GPT_GA\chromedriver-win64\chromedriver.exe"
service = Service(CHROMEDRIVER)
opts = webdriver.ChromeOptions()
opts.add_argument("--start-maximized")
#opts.add_argument("--headless=new")
opts.add_argument("--enable-gpu")
opts.add_argument("--no-sandbox")
opts.add_argument("--enable-unsafe-swiftshader")
driver = webdriver.Chrome(service=service, options=opts)
wait = WebDriverWait(driver, 20)

# ── Requests 세션 설정 ──────────────────────
sess = requests.Session()
sess.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.fmkorea.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive"
})

BASE = "https://www.fmkorea.com"

# ── 목록 크롤러 (requests) ────────────────────────
def fetch_best_list_requests(page: int) -> List[Dict]:
    res = sess.get(
        "https://www.fmkorea.com/index.php",
        params={"mid": "best", "page": page},
        timeout=10
    )
    res.raise_for_status()
    soup = bs4.BeautifulSoup(res.text, "lxml")

    posts = []
    for li in soup.select("li.li_best2_pop0"):
        try:
            href = li.select_one("a.pc_voted_count")["href"]
            no = int(href.rsplit("/", 1)[-1])
            comment_text = li.select_one("span.comment_count").get_text(strip=True)
            comment_count = int(re.sub(r"[^\d]", "", comment_text))
            url = urljoin(BASE, f"/best/{no}")
            posts.append({
                "no": no,
                "comment_count": comment_count,
                "url": url
            })
        except Exception:
            logging.exception(f"[requests] 목록 파싱 실패: {li}")
            continue

    return posts

# ── 목록 크롤러 (selenium) ───────────────────────
def fetch_best_list_selenium(page: int) -> List[Dict]:
    url = f"https://www.fmkorea.com/index.php?mid=best&page={page}"
    driver.get(url)
    wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "li.li_best2_pop0")))

    posts = []
    for li in driver.find_elements(By.CSS_SELECTOR, "li.li_best2_pop0"):
        try:
            href = li.find_element(By.CSS_SELECTOR, "a.pc_voted_count").get_attribute("href")
            no = int(href.rsplit("/", 1)[-1])
            comment_text = li.find_element(By.CSS_SELECTOR, "span.comment_count").text.strip()
            comment_count = int(re.sub(r"[^\d]", "", comment_text))
            posts.append({
                "no": no,
                "comment_count": int(comment_count),
                "url": urljoin(BASE, f"/best/{no}")
            })
        except Exception:
            logging.exception(f"[selenium] 목록 파싱 실패")
            continue
    return posts

# ── 댓글 크롤러 ───────────────────────────────
def selenium_fetch_comments() -> List[Dict]:
    def extract() -> List[Dict]:
        wait.until(EC.presence_of_all_elements_located(
            (By.CSS_SELECTOR, "ul.fdb_lst_ul li.fdb_itm")
        ))
        items: List[Dict] = []
        for li in driver.find_elements(By.CSS_SELECTOR, "ul.fdb_lst_ul li.fdb_itm"):
            try:
                author = li.find_element(
                    By.CSS_SELECTOR, "div.meta a.member_plate"
                ).text.strip()
                date = li.find_element(
                    By.CSS_SELECTOR, "div.meta span.date"
                ).text.strip()
                content = li.find_element(
                    By.CSS_SELECTOR, "div.comment-content .xe_content"
                ).text.strip()
                items.append({
                    "author": author,
                    "author_ip": "—",
                    "date": date,
                    "content": content,
                    "llm_hate_speech": None,
                    "llm_misogyny": None,
                    "Keyword": None,
                    "keyword_content": None
                })
            except Exception:
                continue
        return items

    all_comments: List[Dict] = extract()

    current_page = 1
    while True:
        try:
            pg = driver.find_element(By.CSS_SELECTOR, "div.bd_pg")
            next_page = str(current_page + 1)
            links = pg.find_elements(By.CSS_SELECTOR, "a")
            target = None
            for a in links:
                if a.text.strip() == next_page:
                    target = a
                    break
            if not target:
                break

            driver.execute_script("arguments[0].click();", target)
            wait.until(EC.text_to_be_present_in_element(
                (By.CSS_SELECTOR, "div.bd_pg strong.this"),
                next_page
            ))
            time.sleep(0.5)
            all_comments.extend(extract())
            current_page += 1
        except Exception:
            break

    return all_comments

# ── 본문 크롤러 ───────────────────────────────
def scrape_post(url: str) -> Dict:
    driver.get(url)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.rd_hd")))

    head = driver.find_element(By.CSS_SELECTOR, "div.rd_hd")
    title = head.find_element(By.CSS_SELECTOR, "h1.np_18px span").text.strip()
    author_plate = head.find_element(By.CSS_SELECTOR, "a.member_plate").text.strip()
    ip_m = re.search(r"\((.*?)\)", author_plate)
    writer = author_plate.split("(")[0].strip()
    date = head.find_element(By.CSS_SELECTOR, "span.date").text.strip()

    def cnt(sel: str) -> int:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if not els:
            return 0
        text = els[0].text.replace(",", "").strip()
        try:
            return int(text)
        except ValueError:
            return 0

    content = driver.find_element(By.CSS_SELECTOR, "article .xe_content").text.strip()

    return {
        "title": title,
        "url": url,
        "writer": writer,
        "writer_ip": ip_m.group(1) if ip_m else "—",
        "date": date,
        "content": content,
        "likes": cnt("span.btn_img.new_voted_count"),
        "dislikes": cnt("a.vote3"),
        "comments": selenium_fetch_comments(),
        "llm_hate_speech": None,
        "llm_misogyny": None,
        "Keyword": None,
        "keyword_content": None
    }

# ── 페이지 단위 크롤링 ───────────────────────
def crawl_page(page: int):
    output_dir = Path("fm_korea_result")
    output_dir.mkdir(exist_ok=True)

    try:
        posts = fetch_best_list_selenium(page) if USE_SELENIUM_FOR_LIST else fetch_best_list_requests(page)
    except Exception as e:
        print(f"[Page {page}] 목록 불러오기 실패 → {e}")
        return

    if not posts:
        print(f"[Page {page}] 글을 찾지 못함")
        return

    counts = [p["comment_count"] for p in posts]
    avg_comments = statistics.mean(counts)
    print(f"[Page {page}] 게시글 수: {len(posts)}, 평균 댓글 수: {avg_comments:.1f}")

    for p in posts:
        no, cnt_ = p["no"], p["comment_count"]
        if cnt_ < avg_comments:
            print(f"  [{no}] 댓글 {cnt_}개 → 평균 이하, 스킵")
            continue

        print(f"  [{no}] 댓글 {cnt_}개 → 크롤링 시작")
        try:
            data = scrape_post(p["url"])
            (output_dir / f"{no}.json").write_text(
                json.dumps({"meta": p, "post": data}, ensure_ascii=False, indent=2),
                "utf-8-sig"
            )
            print(f"    저장 완료 ({len(data['comments'])}개 댓글)")
        except Exception:
            logging.exception(f"[{no}] 크롤링/저장 실패")
            print(f"    ERROR: 크롤링 실패")

# ── 엔트리포인트 ─────────────────────────────
if __name__ == "__main__":
    start = int(input("시작 베스트 페이지 번호≫ ").strip())
    end = int(input("끝 베스트 페이지 번호≫  ").strip())

    for page in range(start, end - 1, -1):
        print(f"\n=== Page {page} 크롤링 시작 ===")
        crawl_page(page)

    driver.quit()
