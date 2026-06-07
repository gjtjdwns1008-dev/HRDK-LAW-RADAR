import requests
import xml.etree.ElementTree as ET
import time
import re
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 🌟 수정: QNET_CERTS가 config.py로 이동했으므로 import에서 삭제합니다.
from config import LAW_API_KEY, TARGET_DATE

# ==========================================
# 통신 안정성 세팅 (기존과 동일)
# ==========================================
HEADERS = {'User-Agent': 'Mozilla/5.0'}
session = requests.Session()
retry = Retry(connect=3, backoff_factor=0.5)
adapter = HTTPAdapter(max_retries=retry)
session.mount('http://', adapter)
session.mount('https://', adapter)

# ==========================================
# 마크다운 정제 함수 (기존과 동일)
# ==========================================
def clean_to_markdown(title, content):
    if not content: return ""
    text = content.strip()
    text = re.sub(r'(①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩)', r'\n- **\1**', text)
    return f"### 📜 {title}\n{text}\n"

# ==========================================
# 법령 수집 메인 함수 (소관부처 추출 로직 포함)
# ==========================================
def get_base_laws(target_date=TARGET_DATE):
    """특정 일자의 법령을 수집하고, 소관부처 정보와 프리필터링을 수행합니다."""
    all_laws_dict = {}
    
    SKIP_KEYWORDS = ['직제', '행정기구', '사무분장', '분장규정', '위원회', '정원', '위임전결', '선거', '복무규정', '인사규정', '여비규정', '표창규칙']
    
    for target_type in ['law', 'histlaw']:
        page = 1
        while True:
            search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_KEY}&target={target_type}&type=XML&efYd={target_date}~{target_date}&display=100&page={page}"
            try:
                response = session.get(search_url, headers=HEADERS, timeout=15)
                if not response.text.strip() or response.status_code != 200: break
                root = ET.fromstring(response.text)
                law_nodes = root.findall('.//law')
                if not law_nodes: break
                
                for law in law_nodes:
                    law_id = law.findtext('법령일련번호', '')
                    law_name = law.findtext('법령명한글', '').strip()
                    enforce_date = law.findtext('시행일자', '')
                    
                    # 🌟 [소관부처 수집] API에서 제공하는 소관부처명 추출
                    ministry = law.findtext('소관부처명', '알 수 없음').strip()
                    
                    prom_num_raw = law.findtext('공포번호', '')
                    prom_num = re.sub(r'\D', '', prom_num_raw)
                    prom_date = law.findtext('공포일자', '').strip()
                    
                    if not law_id or law_name in all_laws_dict: continue
                    
                    base_law_link = f"https://www.law.go.kr/법령/{law_name}"

                    # 프리필터링
                    if any(k in law_name for k in SKIP_KEYWORDS):
                        all_laws_dict[law_name] = {
                            "법령명": law_name, 
                            "시행일자": enforce_date,
                            "소관부처": ministry, # 🌟 스킵 법령에도 부처 표기
                            "공포번호": prom_num, 
                            "공포일자": prom_date, 
                            "원본": "조직/기구 관련 법령으로 AI 분석 생략", 
                            "링크": base_law_link,
                            "스킵여부": True 
                        }
                        continue

                    # 디테일 정보 수집
                    detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_KEY}&target={target_type}&MST={law_id}&type=XML"
                    detail_response = session.get(detail_url, headers=HEADERS, timeout=15)
                    detail_root = ET.fromstring(detail_response.text)
                    
                    reason_text = ""
                    for tag in ['.//개정이유', './/제개정이유']:
                        r_node = detail_root.find(tag)
                        if r_node is not None and r_node.text: reason_text += r_node.text.strip() + "\n"
                    
                    article_1 = ""
                    changed_articles = []
                    
                    for jomun in detail_root.findall('.//조문단위'):
                        if jomun.attrib.get('조문여부') == '조문':
                            title = jomun.find('조문제목').text if jomun.find('조문제목') is not None else ""
                            content = jomun.find('조문내용').text if jomun.find('조문내용') is not None else ""
                            
                            if "제1조(" in title or "목적" in title:
                                article_1 = clean_to_markdown(title, content)
                            elif "개정" in content or "신설" in content:
                                changed_articles.append(clean_to_markdown(title, content))

                    stars = "\n".join([s.text.strip() for s in detail_root.findall('.//별표내용') if s.text])
                    
                    full_text = f"### 🏢 개정이유\n{reason_text}\n\n"
                    full_text += f"{article_1}\n" if article_1 else ""
                    
                    if changed_articles:
                        full_text += "### 🚨 이번에 바뀐 핵심 조문\n" + "\n".join(changed_articles)
                    else:
                        body_text = "\n".join([j.text.strip() for j in detail_root.findall('.//조문내용') if j.text])
                        full_text += f"### 🚨 전체 조문 (바뀐 조문 탐색 실패 시)\n{body_text}"
                    
                    if stars:
                        full_text += f"\n\n### ⭐ 별표(자격 기준 등)\n{stars}"
                    
                    full_text = full_text[:15000]
                    
                    # 🌟 4. 정상 법령 딕셔너리에 소관부처 정보 추가 적재
                    all_laws_dict[law_name] = {
                        "법령명": law_name, 
                        "시행일자": enforce_date, 
                        "소관부처": ministry,   # 🌟 핵심: 소관부처 정보 주입
                        "공포번호": prom_num, 
                        "공포일자": prom_date, 
                        "원본": full_text, 
                        "링크": base_law_link,
                        "스킵여부": False 
                    }
                    time.sleep(0.1) 
                if len(law_nodes) < 100: break
                page += 1
            except Exception as e: 
                print(f"⚠️ 법령 수집 중 에러 발생: {e}")
                break
                
    return list(all_laws_dict.values())
