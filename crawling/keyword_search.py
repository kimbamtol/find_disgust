import os
import json
import logging
from typing import List, Dict

# 1) 키워드 리스트 정의 (총 32개)
KEYWORDS: List[str] = [
    "bitch", "갈보", "계집신조", "김치녀", "남미새", "노괴", "된장녀",
    "맘충", "보력지원", "보룡인", "보르노", "보르시", "보슬아치", "보전깨",
    "상폐녀", "스탑러커", "아이 낳는 기계", "암퇘지", "양공주", "여왕벌",
    "여자와 북어는 삼일에 한 번씩 패야 맛이 좋아진다", "옐로우 캡", "오또케",
    "일베녀", "주식 갤러리", "캐런", "피싸개", "피타보라스의 정리",
    "한녀", "혜지", "화냥년", "흉자"
]

# 2) 로깅 설정
logging.basicConfig(
    filename="crawl_keywords.log",
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    encoding="utf-8"
)

def update_file(filepath: str) -> None:
    fname = os.path.basename(filepath)
    with open(filepath, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    post = data.get("post", {})
    # 본문 검사
    content = post.get("content", "")
    found_in_post = [kw for kw in KEYWORDS if kw in content]
    post["Keyword"] = bool(found_in_post)
    post["keyword_content"] = found_in_post
    if found_in_post:
        logging.info(f"{fname} ▶ 본문에서 발견: {found_in_post}")

    # 댓글 검사
    for idx, comment in enumerate(post.get("comments", []), start=1):
        ctext = comment.get("content", "")
        found_in_comment = [kw for kw in KEYWORDS if kw in ctext]
        comment["Keyword"] = bool(found_in_comment)
        comment["keyword_content"] = found_in_comment
        if found_in_comment:
            logging.info(f"{fname} ▶ 댓글 #{idx} 에서 발견: {found_in_comment}")

    # 파일 덮어쓰기
    with open(filepath, "w", encoding="utf-8-sig") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def main():
    result_dir = "result"
    if not os.path.isdir(result_dir):
        print(f"폴더 없음: {result_dir}")
        return

    for fn in sorted(os.listdir(result_dir)):
        if not fn.lower().endswith(".json"):
            continue
        path = os.path.join(result_dir, fn)
        try:
            update_file(path)
            print(f"[OK] {fn}")
        except Exception as e:
            logging.exception(f"{fn} 처리 실패: {e}")
            print(f"[ERR] {fn}: {e}")

if __name__ == "__main__":
    main()
