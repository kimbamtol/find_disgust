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

# ── 로그 설정 ──────────────────────────────────────────────
logging.basicConfig(
    filename="crawl_errors.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8"
)

# ── 1. Selenium 초기화 ──────────────────────────────────────
CHROMEDRIVER = r"C:\Users\OptLab\Desktop\tori\My논문\GECCO_2025\New_TSP_GPT_GA\chromedriver-win64\chromedriver.exe"
service = Service(CHROMEDRIVER)
opts = webdriver.ChromeOptions()
opts.add_argument("--start-maximized")
#opts.add_argument("--headless=new")
opts.add_argument("--disable-gpu")
opts.add_argument("--no-sandbox")
opts.add_argument("--enable-unsafe-swiftshader")
driver = webdriver.Chrome(service=service, options=opts)
wait = WebDriverWait(driver, 10)

# ── 2. requests 세션 ───────────────────────────────────────
sess = requests.Session()
sess.headers.update({
    "User-Agent": "Mozilla/5.0 Chrome/124 Safari/537.36",
    "Referer": "https://www.fmkorea.com/"
})

BASE = "https://www.fmkorea.com"

# ── 3. 베스트 게시판 목록 페치 ──────────────────────────────
def fetch_best_list(page: int) -> List[Dict]:
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
            logging.exception(f"목록 파싱 실패: {li}")
            continue

    return posts

# ── 4. FmKorea 댓글 크롤러 (페이지네이션 포함) ─────────────────
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

    all_comments: List[Dict] = []
    # 첫 페이지
    all_comments.extend(extract())

    # 페이지네이션: cpage가 1,2,3... 순회
    current_page = 1
    while True:
        # pagination 영역
        try:
            pg = driver.find_element(By.CSS_SELECTOR, "div.bd_pg")
            # 다음 페이지 번호
            next_page = str(current_page + 1)
            # 숫자 링크들 중에 next_page 가 있는지
            links = pg.find_elements(By.CSS_SELECTOR, "a")
            target = None
            for a in links:
                if a.text.strip() == next_page:
                    target = a
                    break
            if not target:
                break  # 더 이상 페이지 없음

            # 클릭 & 로드 대기
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

# ── 5. 글 본문 + 댓글 스크랩 ───────────────────────────────
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

# ── 6. 페이지 단위 크롤러 ─────────────────────────────────
def crawl_page(page: int):
    output_dir = Path("fm_korea_result")
    output_dir.mkdir(exist_ok=True)

    posts = fetch_best_list(page)
    if not posts:
        print(f"Page {page}: 글을 찾지 못함")
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

# ── 7. 엔트리포인트 ───────────────────────────────────────
if __name__ == "__main__":
    page = int(input("크롤링할 베스트 페이지 번호≫ ").strip())
    crawl_page(page)
    driver.quit()
