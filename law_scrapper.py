import requests
import xml.etree.ElementTree as ET
import time
import re
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 🌟 수정: QNET_CERTS가 config.py로 이동했으므로 import에서 삭제합니다.
from config import LAW_API_KEY, TARGET_DATE

# ==========================================
# 🛡️ 1차 방어: 통신 안정성 세팅 (urllib3 레벨 5회 강제 자동 재시도)
# ==========================================
HEADERS = {'User-Agent': 'Mozilla/5.0'}
session = requests.Session()
retry = Retry(
    total=5, 
    connect=5,        # 🌟 [핵심] 연결 타임아웃(Timeout) 발생 시 5번 강제 자동 재시도
    read=5,           # 데이터를 읽다가 끊겼을 때도 5번 재시도
    backoff_factor=2, # 재시도 간격을 2초, 4초, 8초로 점진적 증가시켜 서버 부담 완화
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["GET"]
)
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
# 법령 수집 메인 함수 (3중 철통 방어망 탑재)
# ==========================================
def get_base_laws(target_date=TARGET_DATE):
    """특정 일자의 법령을 수집하고, 프리필터링 및 텍스트 정제를 수행하며 네트워크 오류 시 패자부활전을 가동합니다."""
    all_laws_dict = {}
    SKIP_KEYWORDS = ['직제', '행정기구', '사무분장', '분장규정', '위원회', '정원', '위임전결', '선거', '복무규정', '인사규정', '여비규정', '표창규칙']
    
    is_connection_failed = False # 🌟 [3차 방어용] 네트워크 완전 뻗음 감지 플래그
    
    for target_type in ['law', 'histlaw']:
        page = 1
        while True:
            search_url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_KEY}&target={target_type}&type=XML&efYd={target_date}~{target_date}&display=100&page={page}"
            
            # ==========================================
            # 🛡️ 2차 방어: 수동 패자부활전 (법령 목록 조회)
            # ==========================================
            response = None
            for attempt in range(1, 4):
                try:
                    response = session.get(search_url, headers=HEADERS, timeout=30)
                    if response.status_code == 200:
                        break  # 접속 성공 시 재시도 루프 즉시 통과
                except Exception as e:
                    if attempt == 3:
                        print(f"  ❌ [최종 실패] 법령 목록 조회 엔진 작동 불능 (법제처 서버 마비): {e}")
                        is_connection_failed = True
                        break
                    print(f"  ⚠️ [네트워크 지연] 법령 목록 수집 실패({e}). {attempt}회차 실패 ➡️ 20초 대기 후 자동 재시도합니다...")
                    time.sleep(20)
            
            if is_connection_failed or response is None:
                break
                
            if not response.text.strip() or response.status_code != 200: 
                break
                
            try:
                root = ET.fromstring(response.text)
                law_nodes = root.findall('.//law')
                if not law_nodes: break
                
                for law in law_nodes:
                    law_id = law.findtext('법령일련번호', '')
                    law_name = law.findtext('법령명한글', '').strip()
                    enforce_date = law.findtext('시행일자', '')
                    ministry = law.findtext('소관부처명', '알 수 없음').strip()
                    
                    prom_num_raw = law.findtext('공포번호', '')
                    prom_num = re.sub(r'\D', '', prom_num_raw)
                    prom_date = law.findtext('공포일자', '').strip()
                    
                    if not law_id or law_name in all_laws_dict: continue
                    base_law_link = f"https://www.law.go.kr/법령/{law_name}"

                    if any(k in law_name for k in SKIP_KEYWORDS):
                        all_laws_dict[law_name] = {
                            "법령명": law_name, "시행일자": enforce_date, "소관부처": ministry,
                            "공포번호": prom_num, "공포일자": prom_date, 
                            "원본": "조직/기구 관련 법령으로 AI 분석 생략", "링크": base_law_link, "스킵여부": True 
                        }
                        continue

                    detail_url = f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_KEY}&target={target_type}&MST={law_id}&type=XML"
                    
                    # ==========================================
                    # 🛡️ 2차 방어: 수동 패자부활전 (상세 조문 조회)
                    # ==========================================
                    detail_response = None
                    for d_attempt in range(1, 4):
                        try:
                            detail_response = session.get(detail_url, headers=HEADERS, timeout=30)
                            if detail_response.status_code == 200 and detail_response.text.strip():
                                break
                        except Exception as de:
                            if d_attempt == 3:
                                print(f"  ❌ [최종 실패] '{law_name}' 상세 조문 수집 불가로 차단: {de}")
                                is_connection_failed = True
                                break
                            print(f"  ⚠️ [네트워크 지연] '{law_name}' 상세조회 실패({de}). {d_attempt}회차 실패 ➡️ 10초 대기 후 재시도...")
                            time.sleep(10)
                    
                    if is_connection_failed or detail_response is None:
                        break
                        
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
                    full_text = f"### 🏢 개정이유\n{reason_text}\n\n" + (f"{article_1}\n" if article_1 else "")
                    if changed_articles: full_text += "### 🚨 이번에 바뀐 핵심 조문\n" + "\n".join(changed_articles)
                    else: full_text += f"### 🚨 전체 조문 (바뀐 조문 탐색 실패 시)\n" + "\n".join([j.text.strip() for j in detail_root.findall('.//조문내용') if j.text])
                    if stars: full_text += f"\n\n### ⭐ 별표(자격 기준 등)\n{stars}"
                    
                    all_laws_dict[law_name] = {
                        "법령명": law_name, "시행일자": enforce_date, "소관부처": ministry,
                        "공포번호": prom_num, "공포일자": prom_date, "원본": full_text[:15000], "링크": base_law_link, "스킵여부": False 
                    }
                    time.sleep(0.1) 
                
                if is_connection_failed:
                    break
                    
                if len(law_nodes) < 100: break
                page += 1
            except Exception as e: 
                print(f"⚠️ 법령 데이터 파싱/처리 중 크리티컬 에러 발생: {e}")
                is_connection_failed = True
                break
                
        if is_connection_failed: break

    # ==========================================
    # 🛡️ 3차 방어: 0건 가짜 리포트 발송 원천 차단
    # ==========================================
    # 🌟 수집된 데이터가 아예 없는데, 네트워크 에러 플래그가 서 있다면 최종적으로 None 리턴!
    if is_connection_failed and not all_laws_dict:
        return None
        
    return list(all_laws_dict.values())
