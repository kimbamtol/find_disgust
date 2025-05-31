import json
import re
import sys
import time
import logging
from pathlib import Path
from typing import Dict, List

import bs4
import requests
import certifi
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ── 로그 설정 ────────────────────────────────────────────────────
logging.basicConfig(
    filename="ilbe_crawl_errors.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8"
)

# ── Selenium 설정 (헤드리스 제거 → 브라우저 창 띄우기) ─────────────
CHROMEDRIVER = r"C:\Users\OptLab\Desktop\tori\My논문\GECCO_2025\New_TSP_GPT_GA\chromedriver-win64\chromedriver.exe"
service = Service(CHROMEDRIVER)

opts = webdriver.ChromeOptions()
# opts.add_argument("--headless=new")  # 주석 처리하여 실제 창이 뜨도록 함
opts.add_argument("--disable-gpu")
opts.add_argument("--no-sandbox")
opts.add_argument("--ignore-certificate-errors")
opts.add_argument("--allow-insecure-localhost")
opts.add_argument("--window-size=1920,1080")

driver = webdriver.Chrome(service=service, options=opts)
wait = WebDriverWait(driver, 15)

# ── requests 세션 (certifi 번들 사용) ─────────────────────────────────
sess = requests.Session()
sess.headers.update({
    "User-Agent": "Mozilla/5.0 Chrome/124 Safari/537.36",
    "Referer": "https://www.ilbe.com/"
})
sess.verify = certifi.where()

# ── parse_list_page 함수: Selenium으로 리스트 페이지 로드 후 id, url, 댓글수 추출 ───
def parse_list_page(page: int) -> List[Dict]:
    """
    Selenium으로 ILBE 리스트 페이지를 열고, BeautifulSoup으로 파싱하여
    게시물 id, full URL, 댓글 수를 반환합니다.
    """
    list_url = f"https://www.ilbe.com/list/ilbe?page={page}&listStyle=list"
    try:
        driver.get(list_url)
        time.sleep(2)  # 페이지 로딩을 위해 잠시 대기
    except Exception as e:
        logging.error(f"[parse_list_page] Selenium으로 리스트 페이지 {page} 로드 실패: {e}")
        return []

    soup = bs4.BeautifulSoup(driver.page_source, "lxml")
    posts: List[Dict] = []
    for li in soup.select("ul.board-body > li"):
        classes = li.get("class", [])
        if "notice-line" in classes or "ad-line" in classes:
            continue

        c_tag = li.select_one("span.comment a")
        t_tag = li.select_one("span.title a.subject")
        if not (c_tag and t_tag):
            continue

        try:
            comment_cnt = int(c_tag.text.strip())
        except ValueError:
            continue

        href = t_tag["href"]  # e.g. "/view/8269027950?page=20000&listStyle=list"
        m = re.search(r"/view/(\d+)", href)
        if not m:
            continue
        art_id = int(m.group(1))
        full_url = f"https://www.ilbe.com/view/{art_id}"

        posts.append({
            "id": art_id,
            "url": full_url,
            "comments": comment_cnt
        })

    return posts

# ── scrape_post 함수: 본문 + 댓글(페이징 포함) ─────────────────────────
def scrape_post(url: str) -> Dict:
    """
    주어진 게시물 URL을 Selenium으로 열어,
    – 제목, 작성자, 날짜, 본문 텍스트, 이미지 URL, 추천/비추천 수
    – 댓글 페이징(“loadComment(1)” → “loadComment(2)” → … 순서)으로 모두 수집
    딕셔너리 형태로 반환합니다.
    """
    driver.get(url)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.post-content")))

    # — 제목 —
    try:
        title = driver.find_element(By.CSS_SELECTOR, "meta[property='og:title']").get_attribute("content")
    except:
        title = driver.title

    # — 작성자 + IP —
    nick_raw = driver.find_element(By.CSS_SELECTOR, "span.nick").text.strip()
    ip_m = re.search(r"\((.*?)\)", nick_raw)

    # — 날짜 —
    date = driver.find_element(By.CSS_SELECTOR, "span.date").text.strip()

    # — 본문 텍스트 + 이미지 목록 —
    content_div = driver.find_element(By.CSS_SELECTOR, "div.post-content")
    text_parts = [
        p.text.strip()
        for p in content_div.find_elements(By.CSS_SELECTOR, "p")
        if p.text.strip()
    ]
    img_tags = content_div.find_elements(By.CSS_SELECTOR, "img")
    images = [
        img.get_attribute("src")
        for img in img_tags
        if img.get_attribute("src")
    ]

    # — 추천/비추천 카운트 헬퍼 —
    def cnt(sel: str) -> int:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if not els:
            return 0
        txt = els[0].text.replace(",", "").strip()
        try:
            return int(txt)
        except ValueError:
            return 0

    # ── 댓글 수집: “loadComment(1)” → “loadComment(2)” → … 순서 ─────────────────
    def selenium_fetch_comments(page_delay: float = 0.5) -> List[Dict]:
        """
        현재 댓글 페이징 영역에 보이는 모든 페이지 번호를 확인한 뒤,
        1부터 최대 페이지 번호까지 순서대로 loadComment(n)을 호출하여 추출합니다.
        """
        def extract() -> List[Dict]:
            wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.comment-item-box")))
            out: List[Dict] = []
            for itm in driver.find_elements(By.CSS_SELECTOR, "div.comment-item"):
                try:
                    author = itm.find_element(By.CSS_SELECTOR, "span.global-nick.nick a").text.strip()
                    date_c = itm.find_element(By.CSS_SELECTOR, "span.date-line").text.strip()
                    text_c = itm.find_element(By.CSS_SELECTOR, "span.cmt").text.strip()
                    good_e = itm.find_elements(By.CSS_SELECTOR, "em[id^='cnt_good_']")
                    bad_e = itm.find_elements(By.CSS_SELECTOR, "em[id^='cnt_bad_']")
                    good = good_e[0].text.strip() if good_e else "0"
                    bad = bad_e[0].text.strip() if bad_e else "0"
                    out.append({
                        "author": author,
                        "date": date_c,
                        "content": text_c,
                        "likes": good,
                        "dislikes": bad,
                        "llm_hate_speech": None,
                        "llm_misogyny": None,
                        "Keyword": None,
                        "keyword_content": None
                    })
                except Exception:
                    continue
            return out

        comments_list: List[Dict] = []

        # (1) 현재 페이지의 최대 댓글 페이지 번호 파악
        try:
            # 모든 페이지 버튼 <a onclick="loadComment(n)"> 요소 수집
            page_btns = driver.find_elements(By.CSS_SELECTOR, "div.paginate a")
            page_nums = []
            for btn in page_btns:
                txt = btn.text.strip()
                if txt.isdigit():
                    try:
                        page_nums.append(int(txt))
                    except ValueError:
                        continue
            max_page = max(page_nums) if page_nums else 1
        except Exception:
            max_page = 1

        # (2) 1부터 max_page까지 순서대로 loadComment(n) 호출하며 댓글 수집
        for p in range(1, max_page + 1):
            try:
                # JavaScript로 직접 loadComment(p) 호출
                driver.execute_script(f"loadComment({p});")
                time.sleep(page_delay)
                comments_list.extend(extract())
            except Exception:
                # 해당 페이지 로드에 실패하면 루프 종료
                break

        return comments_list

    post = {
        "title": title,
        "url": url,
        "writer": nick_raw.split("(")[0].strip(),
        "writer_ip": ip_m.group(1) if ip_m else "—",
        "date": date,
        "content_text": "\n".join(text_parts),
        "content_images": images,
        "likes": cnt("span.recomm-vote > em, span.recomm"),
        "dislikes": cnt("span.recomm-vote.bad > em, span.non-recomm"),
        "comments": selenium_fetch_comments(),
        "llm_hate_speech": None,
        "llm_misogyny": None,
        "Keyword": None,
        "keyword_content": None
    }
    return post

# ── crawl 함수: 평균 댓글수 이상 게시물만 크롤링 ───────────────────
def crawl(start_page: int, end_page: int = 1):
    """
    리스트 페이지 start_page부터 end_page까지(내림차순) 순회하며:
      1) parse_list_page (Selenium)로 (id, url, comments) 목록 가져오기
      2) 댓글 수 평균 계산
      3) 평균 이상 게시물만 scrape_post로 크롤링 후 JSON 저장
    """
    Path("ilbe_result").mkdir(exist_ok=True)

    for page in range(start_page, end_page - 1, -1):
        print(f"\n📄 리스트 페이지 {page} 크롤링…")
        posts_meta = parse_list_page(page)
        if not posts_meta:
            print("  └─ 글 없음/요청 실패")
            continue

        # 댓글 수 평균 계산
        comment_counts = [p["comments"] for p in posts_meta]
        avg_comments = sum(comment_counts) / len(comment_counts)
        print(f"  · 이 페이지 평균 댓글수: {avg_comments:.2f}")

        # 평균 이상 게시물만 필터링
        filtered = [p for p in posts_meta if p["comments"] >= avg_comments]
        print(f"  · 평균 이상 게시물 개수: {len(filtered)} / {len(posts_meta)}")

        for meta in filtered:
            art_id = meta["id"]
            print(f"  [{art_id}] {meta['url']} (댓글수={meta['comments']})", end=" ")
            try:
                post_data = scrape_post(meta["url"])
                n_com = len(post_data["comments"])
                if n_com == 0:
                    logging.warning(f"[{art_id}] 댓글 0개 (URL: {meta['url']})")

                # JSON으로 저장
                Path(f"ilbe_result/{art_id}.json").write_text(
                    json.dumps({"ilbe_meta": meta, "post": post_data},
                               ensure_ascii=False, indent=2),
                    "utf-8-sig"
                )
                print(f"→ 저장 ✓ (실제 수집 댓글 {n_com}개)")
            except Exception as e:
                logging.exception(f"[{art_id}] 크롤링 실패")
                print("ERROR:", e)

            time.sleep(1)

# ── 엔트리포인트 ─────────────────────────────────────────────────
if __name__ == "__main__":
    argc = len(sys.argv)
    if argc == 3:
        sp, ep = int(sys.argv[1]), int(sys.argv[2])
    elif argc == 2:
        sp, ep = int(sys.argv[1]), 1
    else:
        sp = int(input("시작 리스트 페이지≫ ").strip())
        ep = 1

    crawl(sp, ep)
    driver.quit()
