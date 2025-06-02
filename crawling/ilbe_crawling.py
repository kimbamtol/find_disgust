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

# ── 로그 설정 ──────────────────────────────────────────
logging.basicConfig(
    filename="ilbe_crawl_errors.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8"
)

CHROMEDRIVER = r"C:\Users\OptLab\Desktop\tori\My논문\GECCO_2025\New_TSP_GPT_GA\chromedriver-win64\chromedriver.exe"
service = Service(CHROMEDRIVER)

opts = webdriver.ChromeOptions()
opts.add_argument("--disable-gpu")
opts.add_argument("--no-sandbox")
opts.add_argument("--ignore-certificate-errors")
opts.add_argument("--allow-insecure-localhost")
opts.add_argument("--window-size=1920,1080")


driver = None
wait = None

def restart_driver():
    global driver, wait
    try:
        if driver:
            driver.quit()
    except Exception:
        pass
    driver = webdriver.Chrome(service=service, options=opts)
    wait = WebDriverWait(driver, 15)

restart_driver()

sess = requests.Session()
sess.headers.update({
    "User-Agent": "Mozilla/5.0 Chrome/124 Safari/537.36",
    "Referer": "https://www.ilbe.com/"
})
sess.verify = certifi.where()

def parse_list_page(page: int) -> List[Dict]:
    list_url = f"https://www.ilbe.com/list/ilbe?page={page}&listStyle=list"
    try:
        driver.get(list_url)
        time.sleep(2)
    except Exception as e:
        logging.error(f"[parse_list_page] Selenium\uc73c\ub85c \ub9ac\uc2a4\ud2b8 \ud398\uc774\uc9c0 {page} \ub85c\ub4dc \uc2e4\ud328: {e}")
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

        href = t_tag["href"]
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

def scrape_post(url: str) -> Dict:
    driver.get(url)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.post-content")))

    try:
        title = driver.find_element(By.CSS_SELECTOR, "meta[property='og:title']").get_attribute("content")
    except:
        title = driver.title

    nick_raw = driver.find_element(By.CSS_SELECTOR, "span.nick").text.strip()
    ip_m = re.search(r"\\((.*?)\\)", nick_raw)
    date = driver.find_element(By.CSS_SELECTOR, "span.date").text.strip()

    content_div = driver.find_element(By.CSS_SELECTOR, "div.post-content")
    text_parts = [
        p.text.strip()
        for p in content_div.find_elements(By.CSS_SELECTOR, "p")
        if p.text.strip()
    ]
    img_tags = content_div.find_elements(By.CSS_SELECTOR, "img")
    images = [img.get_attribute("src") for img in img_tags if img.get_attribute("src")]

    def cnt(sel: str) -> int:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if not els:
            return 0
        txt = els[0].text.replace(",", "").strip()
        try:
            return int(txt)
        except ValueError:
            return 0

    def selenium_fetch_comments(page_delay: float = 0.5) -> List[Dict]:
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

        try:
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

        for p in range(1, max_page + 1):
            try:
                driver.execute_script(f"loadComment({p});")
                time.sleep(page_delay)
                comments_list.extend(extract())
            except Exception:
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

def crawl(start_page: int, end_page: int = 1):
    Path("ilbe_result").mkdir(exist_ok=True)

    for page in range(start_page, end_page - 1, -1):
        print(f"\n📄 리스트 페이지 {page} 크롤링…")
        posts_meta = parse_list_page(page)
        if not posts_meta:
            print("  └─ 글 없음/요청 실패")
            continue

        comment_counts = [p["comments"] for p in posts_meta]
        avg_comments = sum(comment_counts) / len(comment_counts)
        print(f"  · 이 페이지 평균 댓글수: {avg_comments:.2f}")

        filtered = [p for p in posts_meta if p["comments"] >= avg_comments]
        print(f"  · 평균 이상 글수: {len(filtered)} / {len(posts_meta)}")

        for meta in filtered:
            art_id = meta["id"]
            print(f"  [{art_id}] {meta['url']} (댓글수={meta['comments']})", end=" ")
            try:
                post_data = scrape_post(meta["url"])
                n_com = len(post_data["comments"])
                if n_com == 0:
                    logging.warning(f"[{art_id}] 댓글 0개 (URL: {meta['url']})")

                Path(f"ilbe_result/{art_id}.json").write_text(
                    json.dumps({"ilbe_meta": meta, "post": post_data}, ensure_ascii=False, indent=2),
                    "utf-8-sig"
                )
                print(f"→ 저장 ✓ (실제 수집 댓글 {n_com}개)")
            except Exception as e:
                logging.exception(f"[{art_id}] 크롤링 실패")
                print("ERROR:", e)
                if "invalid session id" in str(e).lower():
                    print("⚠️ WebDriver 세션이 유효하지 않음 → 드라이버 재시작")
                    restart_driver()

            time.sleep(1)

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

    try:
        if driver:
            driver.quit()
    except:
        pass
