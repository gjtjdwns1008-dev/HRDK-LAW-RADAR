import requests
import xml.etree.ElementTree as ET
from config import WORKNET_API_KEY

def fetch_single_job_count(cert_name):
    """자격증 이름 하나를 받아 워크넷에서 현재 구인 중인 공고 건수를 반환합니다."""
    if not WORKNET_API_KEY:
        return "인증키 없음"
        
    # 고용24 채용정보목록 조회 Open API 기본 URL
    url = "https://www.work24.go.kr/cm/openApi/call/wk/callOpenApiSvcInfo210L01.do"
    
    # 필수 파라미터 및 검색 키워드 세팅
    params = {
        "authKey": WORKNET_API_KEY,
        "callTp": "L",          # L: 목록 조회
        "returnType": "XML",    # XML 반환 필수
        "startPage": 1,
        "display": 10,
        "keyword": cert_name    # 자격증 명칭 검색 (requests가 자동 UTF-8 인코딩 처리)
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            return "서버에러"
            
        # XML 데이터 파싱
        root = ET.fromstring(response.content)
        
        # 워크넷 API는 통상적으로 전체 결과 건수를 <total> 또는 <totalCount> 태그에 담아 줍니다.
        total_tag = root.find(".//total")
        if total_tag is None:
            total_tag = root.find(".//totalCount")
            
        if total_tag is not None and total_tag.text:
            return f"{total_tag.text}건"
        return "0건"
        
    except Exception as e:
        return "조회실패"

def get_worknet_job_count(certs_string):
    """
    쉼표로 구분된 자격증 목록 문자열(예: '건축기사, 건축산업기사')을 받아
    각 종목별 실시간 구인 건수를 매쉬업 문자열로 결합합니다.
    """
    if not certs_string or certs_string.strip() in ["", "없음", "N/A"]:
        return "-"
        
    # 쉼표 기준으로 종목 쪼개기 및 공백 제거
    cert_list = [c.strip() for c in certs_string.split(",") if c.strip()]
    
    results = []
    for cert in cert_list:
        count = fetch_single_job_count(cert)
        results.append(f"{cert}({count})")
        
    # 결과 조립 예시: "건축기사(142건) | 건축산업기사(45건)"
    return " | ".join(results)
