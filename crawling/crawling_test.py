from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

options = Options()
options.add_argument("--start-maximized")
# options.add_argument("--headless")

driver = webdriver.Chrome(options=options)
url = "https://gall.dcinside.com/board/view/?id=dcbest&no=1&_dcbest=6&page=4189"
driver.get(url)

wait = WebDriverWait(driver, 10)
all_comments = []

def extract_comments():
    time.sleep(1)
    comments = driver.find_elements(By.CSS_SELECTOR, 'div.clear.cmt_txtbox p.usertxt')
    return [c.text.strip() for c in comments if c.text.strip()]

def go_to_next_page():
    try:
        current = driver.find_element(By.CSS_SELECTOR, "div.cmt_paging em")  # 현재 페이지
        next_page = current.find_element(By.XPATH, "following-sibling::a[1]")  # 다음 형제 a 태그
        next_page_num = next_page.text.strip()
        print(f"➡ 다음 댓글 페이지 클릭: {next_page_num}")
        driver.execute_script("arguments[0].click();", next_page)
        return True
    except Exception:
        return False

# 첫 페이지
all_comments.extend(extract_comments())

# 다음 페이지들 반복
while go_to_next_page():
    all_comments.extend(extract_comments())

driver.quit()

# 결과 출력
for idx, comment in enumerate(all_comments, 1):
    print(f"[{idx}] {comment}")
