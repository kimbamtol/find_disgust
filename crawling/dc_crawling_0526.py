"""
dc_crawler_direct.py
────────────────────────────────────────────────────────────
실베(dcbest) 글 본문 + 댓글(페이징) 크롤러
• 원본 보기 링크로 이동하지 않음
• 페이지 전환마다 슬립
• 댓글 0개·예외 로그는 crawl_errors.log 기록
"""
import json, re, sys, time, logging
from pathlib import Path
from typing import Dict, List

import bs4, requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by    import By
from selenium.webdriver.support.ui   import WebDriverWait
from selenium.webdriver.support      import expected_conditions as EC

# ── 로그 설정 ──────────────────────────────────────────────
logging.basicConfig(
    filename="crawl_errors.log",
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8"
)

# ── 1. Selenium ────────────────────────────────────────────
CHROMEDRIVER = r"C:\Users\OptLab\Desktop\tori\My논문\GECCO_2025\New_TSP_GPT_GA\chromedriver-win64\chromedriver.exe"
service = Service(CHROMEDRIVER)
opts    = webdriver.ChromeOptions()
opts.add_argument("--start-maximized")
opts.add_argument("--headless=new")
opts.add_argument("--disable-gpu"); opts.add_argument("--no-sandbox")
driver  = webdriver.Chrome(service=service, options=opts)
wait    = WebDriverWait(driver, 10)

# ── 2. requests 세션 ───────────────────────────────────────
sess = requests.Session()
sess.headers.update(
    {"User-Agent": "Mozilla/5.0 Chrome/124 Safari/537.36",
     "Referer"   : "https://gall.dcinside.com/"}
)

# ── 3. 실베 글 메타 가져오기 ───────────────────────────────
def fetch_dcbest_meta(no: int) -> Dict:
    """실베 글(head)에서 기본 메타만 파싱, URL 은 그대로 반환"""
    res = sess.get("https://gall.dcinside.com/board/view/",
                   params={"id":"dcbest","no":no,"_dcbest":6}, timeout=10)
    if res.status_code != 200:
        return {}
    soup = bs4.BeautifulSoup(res.text, "lxml")
    head = soup.select_one("div.gallview_head")
    if not head:
        return {}

    nick = head.select_one("span.nickname").get_text(strip=True)
    ip_m = re.search(r"\((.*?)\)", nick)

    return {
        "no"       : no,
        "url"      : res.url,
        "title"    : head.select_one(".title_subject").get_text(strip=True),
        "author"   : nick.split("(")[0].strip(),
        "author_ip": ip_m.group(1) if ip_m else "—",
        "date"     : head.select_one(".gall_date").get_text(strip=True),
    }

# ── 4. 댓글 크롤러 ──────────────────────────────────────────
def selenium_fetch_comments(page_delay: float = 0.5) -> List[Dict]:
    """div.clear.cmt_txtbox p.usertxt 기준으로 댓글 4필드 수집"""
    def extract() -> List[Dict]:
        # 댓글 블록 로드 대기
        wait.until(EC.presence_of_all_elements_located(
            (By.CSS_SELECTOR, "div.clear.cmt_txtbox p.usertxt, div.comment_dccon")
        ))
        items: List[Dict] = []
        blocks = driver.find_elements(By.CSS_SELECTOR,
                  "li.ub-content, li.ub-w")
        for li in blocks:
            try:
                # 내용
                try:
                    text = li.find_element(
                        By.CSS_SELECTOR, "div.clear.cmt_txtbox p.usertxt"
                    ).text.strip()
                except:
                    text = li.find_element(
                        By.CSS_SELECTOR, "div.comment_dccon"
                    ).text.strip()
                if not text:
                    continue

                # 작성자·IP
                raw = li.find_element(
                    By.CSS_SELECTOR, "div.cmt_nickbox span.nickname, span.nickname"
                ).text.strip()
                ip_m = re.search(r"\((.*?)\)", raw)
                author = raw.split("(")[0].strip()

                # 날짜 (갤러리 버전에 따라 클래스가 달라질 수 있음)
                date = li.find_element(
                    By.CSS_SELECTOR, "span.date_time, span.gall_date, span.ut"
                ).text.strip()

                items.append({
                    "author"   : author,
                    "author_ip": ip_m.group(1) if ip_m else "—",
                    "date"     : date,
                    "content"  : text,
                    "llm_hate_speech"      : None,
                    "llm_misogyny"         : None,
                    "Keyword"              : None,
                    "keyword_content"      : None}
                    )
            except Exception:
                continue
        return items

    comments: List[Dict] = []
    comments.extend(extract())

    # 페이지 넘기기
    while True:
        try:
            cur   = driver.find_element(By.CSS_SELECTOR, "div.cmt_paging em")
            nxt   = cur.find_element(By.XPATH, "following-sibling::a[1]")
            nxt_no = nxt.text.strip()
            driver.execute_script("arguments[0].click();", nxt)
            WebDriverWait(driver, 10).until(
                lambda d: d.find_element(By.CSS_SELECTOR,
                          "div.cmt_paging em").text.strip() == nxt_no)
            time.sleep(page_delay)
            comments.extend(extract())
        except Exception:
            break
    return comments

# ── 5. 글 본문 + 댓글 스크랩 ───────────────────────────────
def scrape_post(url: str) -> Dict:
    driver.get(url)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.gallview_head")))

    head = driver.find_element(By.CSS_SELECTOR, "div.gallview_head")
    nick = head.find_element(By.CSS_SELECTOR, ".nickname").text
    ip_m = re.search(r"\((.*?)\)", nick)

    def cnt(sel: str) -> int:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        return int(els[0].text.replace(",", "")) if els else 0

    return {
        "title"     : head.find_element(By.CSS_SELECTOR, ".title_subject").text.strip(),
        "url"       : url,
        "writer"    : nick.split("(")[0].strip(),
        "writer_ip" : ip_m.group(1) if ip_m else "—",
        "date"      : head.find_element(By.CSS_SELECTOR, ".gall_date").text.strip(),
        "content"   : driver.find_element(By.CSS_SELECTOR, "div.write_div").text.strip(),
        "likes"     : cnt("span.upcnt, #recommend_point, span.gall_recommend"),
        "dislikes"  : cnt("span.downcnt, #non_recommend_point, span.gall_non_recommend"),
        "comments"  : selenium_fetch_comments(),
        "llm_hate_speech"      : None,
        "llm_misogyny"         : None,
        "Keyword"              : None,
        "keyword_content"      : None
    }

# ── 6. 메인 루프 ────────────────────────────────────────────
def crawl(start: int, end: int = 1):
    Path("dc_result").mkdir(exist_ok=True)
    for no in range(start, end - 1, -1):
        print(f"[{no}] ", end="", flush=True)
        try:
            meta = fetch_dcbest_meta(no)
            if not meta:
                print("삭제/블라인드"); continue

            post_data = scrape_post(meta["url"])
            n_com = len(post_data["comments"])
            if n_com == 0:
                logging.warning(f"[{no}] 댓글 0개 (URL: {meta['url']})")

            Path(f"dc_result/{no}.json").write_text(
                json.dumps({"dcbest_meta": meta, "post": post_data},
                           ensure_ascii=False, indent=2),
                "utf-8-sig"
            )
            print(f"저장 ✓ (댓글 {n_com}개)")
        except Exception as e:
            logging.exception(f"[{no}] 크롤링 실패")
            print("ERROR:", e)
        time.sleep(1)

# ── 7. 엔트리포인트 ───────────────────────────────────────
if __name__ == "__main__":
    argc = len(sys.argv)
    if argc == 2:
        s, e = int(sys.argv[1]), 1
    elif argc == 3:
        s, e = int(sys.argv[1]), int(sys.argv[2])
    else:
        s, e = int(input("시작 글 번호≫ ").strip()), 1

    crawl(s, e)
    driver.quit()
