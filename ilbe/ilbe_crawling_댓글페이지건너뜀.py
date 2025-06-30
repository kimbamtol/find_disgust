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
    filename="crawl_errors.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8"
)

# ── Selenium 설정 (헤드리스 옵션 제거 → 브라우저 창 띄우기) ─────────
CHROMEDRIVER = r"C:\Users\OptLab\Desktop\tori\My논문\GECCO_2025\New_TSP_GPT_GA\chromedriver-win64\chromedriver.exe"
service = Service(CHROMEDRIVER)

opts = webdriver.ChromeOptions()
# headless 모드를 제거해서 실제 창이 뜨도록 합니다.
# opts.add_argument("--headless=new")
opts.add_argument("--disable-gpu")
opts.add_argument("--no-sandbox")
opts.add_argument("--ignore-certificate-errors")
opts.add_argument("--allow-insecure-localhost")
opts.add_argument("--window-size=1920,1080")

driver = webdriver.Chrome(service=service, options=opts)
wait = WebDriverWait(driver, 15)

# ── requests 세션 (certifi로 SSL 검증) ─────────────────────────────────
sess = requests.Session()
sess.headers.update({
    "User-Agent": "Mozilla/5.0 Chrome/124 Safari/537.36",
    "Referer": "https://www.ilbe.com/"
})
sess.verify = certifi.where()

# ── parse_list_page 함수: Selenium으로 리스트 페이지 로드 후 id, url, 댓글수 추출 ───
def parse_list_page(page: int) -> List[Dict]:
    """
    Selenium을 사용하여 ILBE 리스트 페이지를 직접 열고, BeautifulSoup으로 파싱합니다.
    각 게시물의 id, url, comments(댓글 수)를 추출한 뒤 리스트로 반환합니다.
    """
    list_url = f"https://www.ilbe.com/list/ilbe?page={page}&listStyle=list"
    try:
        driver.get(list_url)
        # 페이지 로딩이 완료될 때까지 충분히 기다립니다.
        # (여기서는 임의로 2초 정도 sleep을 두었지만, 필요하면 적절히 조절하세요)
        time.sleep(2)
        # 또는 아래처럼 특정 요소가 로드될 때까지 명시적으로 기다릴 수도 있습니다.
        # wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "ul.board-body > li")))
    except Exception as e:
        logging.error(f"[parse_list_page] Selenium으로 리스트 페이지 {page} 로드 실패: {e}")
        return []

    # 브라우저가 렌더링한 HTML 전체를 가져옵니다.
    page_source = driver.page_source
    soup = bs4.BeautifulSoup(page_source, "lxml")

    posts: List[Dict] = []
    for li in soup.select("ul.board-body > li"):
        classes = li.get("class", [])
        if "notice-line" in classes or "ad-line" in classes:
            continue

        # 댓글 수 <span class="comment"><a>숫자</a></span>
        c_tag = li.select_one("span.comment a")
        # 제목+링크 <span class="title"><a class="subject" href="...">...</a>...</span>
        t_tag = li.select_one("span.title a.subject")

        if not (c_tag and t_tag):
            continue

        # 댓글 수를 정수로 변환
        try:
            comment_cnt = int(c_tag.text.strip())
        except ValueError:
            continue

        # href에서 ID 추출
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

# ── scrape_post 함수: 본문+댓글(페이징 포함) ──────────────────────────
def scrape_post(url: str) -> Dict:
    """
    주어진 게시물 URL을 Selenium으로 열어,
    – 제목, 작성자, 작성일, 본문 텍스트, 이미지 URL, 추천/비추천 수
    – 댓글 페이징(첫페이지 → 다음 → 다음 ...) 방식으로 전부 수집
    결과를 딕셔너리로 반환합니다.
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

    # ── 댓글 수집: “첫페이지 → 다음 → 다음 …” 순서 ─────────────────────────
    def selenium_fetch_comments(page_delay: float = 0.5) -> List[Dict]:
        """
        댓글 페이징 영역을 모두 순회하며 comment-item을 수집합니다.
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

        # (a) “첫페이지” 버튼 클릭
        try:
            first_btn = wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//div[@class='paginate']/a[contains(@class,'prev') and normalize-space(text())='첫페이지']"
                ))
            )
            first_btn.click()
            time.sleep(page_delay)
        except Exception:
            # 댓글이 한 페이지밖에 없으면 이 부분에서 예외가 나고, 바로 extract()만 수행
            comments_list.extend(extract())
            return comments_list

        # (b) 1페이지 댓글 수집
        comments_list.extend(extract())

        # (c) “다음” 버튼 반복 클릭 → 2,3,4... 모두 수집
        while True:
            try:
                nxt_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "div.paginate a.next2")))
            except Exception:
                break

            if "disabled" in (nxt_btn.get_attribute("class") or "").lower():
                break

            try:
                prev_active = driver.find_element(By.CSS_SELECTOR, "div.paginate a.page-on")
                prev_page_no = int(prev_active.text.strip())
            except Exception:
                prev_page_no = None

            nxt_btn.click()
            time.sleep(page_delay)

            try:
                new_active = driver.find_element(By.CSS_SELECTOR, "div.paginate a.page-on")
                new_page_no = int(new_active.text.strip())
            except Exception:
                new_page_no = None

            if prev_page_no is not None and new_page_no == prev_page_no:
                break

            comments_list.extend(extract())

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
      1) parse_list_page (Selenium) 로 (id, url, comments) 목록 가져오기
      2) 댓글 수 평균 계산
      3) 평균 이상 게시물만 scrape_post 로 크롤링해서 JSON 저장
    """
    Path("result").mkdir(exist_ok=True)

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
                Path(f"result/{art_id}.json").write_text(
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
