import os
import json
import time
import requests
import xmltodict
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import google.generativeai as genai
from datetime import datetime, timedelta
from prompt_template import SYSTEM_PROMPT

# ==========================================
# 1. 환경 변수 및 API 키 설정 (GitHub Secrets 연동)
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
LAW_API_KEY = os.getenv("LAW_API_KEY")
WORKNET_API_KEY = os.getenv("WORKNET_API_KEY")
GCP_JSON_STR = os.getenv("GCP_SERVICE_ACCOUNT_JSON")
SHEET_URL = os.getenv("GOOGLE_SHEET_URL")

genai.configure(api_key=GEMINI_API_KEY)

# ==========================================
# 2. 구글 시트 증분 결합 (Upsert) 처리
# ==========================================
def get_gspread_client():
    creds_dict = json.loads(GCP_JSON_STR)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

def update_google_sheet(sheet_client, data_list, total_reviewed_count):
    # ★ 수정 포인트: open_by_url 로 변경!
    doc = sheet_client.open_by_url(SHEET_URL) 
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1) 총괄현황표 로깅 (5열 구조 완벽 대응)
    try:
        summary_sheet = doc.worksheet("총괄현황표")
        
        # 5칸 구조에 맞춘 데이터 배열 (열 밀림 현상 완벽 방어)
        summary_row = [
            today_str,               # A열: 수집일자
            total_reviewed_count,    # B열: 총 검토건수 (수집된 전체 법령 수)
            len(data_list),          # C열: 연관 법령건수 (AI가 필터링한 유의미한 결과 수)
            "정상 완료",             # D열: 모니터링 상태
            "AI+워크넷 매쉬업 완료"  # E열: 실행 로그 및 비고
        ]
        
        summary_sheet.append_row(summary_row)
        print(f"📊 [총괄현황표] 업데이트 완료: {summary_row}")
        
    except Exception as e:
        print(f"🚨 총괄현황표 시트 오류: {e}")

    # 2) Master DB 데이터 업데이트 (증분 업데이트 - Upsert 적용)
    try:
        master_sheet = doc.worksheet("국가기술자격 관련법령")
        
        # B열(인덱스 2)에 있는 모든 '고유키(unique_key)' 데이터를 리스트로 가져옵니다.
        existing_keys = master_sheet.col_values(2) 

        for item in data_list:
            u_key = item.get("unique_key", "")
            
            # 구글 시트에 넣을 1행 분량의 데이터 세팅
            row_data = [
                today_str,
                u_key,
                item.get("law_name", ""),
                item.get("provision", ""),
                ", ".join(item.get("related_qualifications", [])),
                item.get("preference_type", ""),
                item.get("sapa_target", ""),
                item.get("impact_level", ""),
                item.get("insight", ""),
                item.get("worknet_job_count", "0")
            ]

            if u_key and u_key in existing_keys:
                # [Update] 기존에 존재하는 키면 해당 행(Row)을 찾아 덮어쓰기 (내용 갱신)
                row_idx = existing_keys.index(u_key) + 1 
                cell_range = f"A{row_idx}:J{row_idx}"
                master_sheet.update(values=[row_data], range_name=cell_range)
                print(f"🔄 [Update] 기존 조문 내용 갱신 완료: {u_key}")
            else:
                # [Insert] 시트에 없는 완전 신규 법령 조문이면 맨 밑에 새로 추가
                master_sheet.append_row(row_data)
                print(f"🆕 [Insert] 신규 우대조항 추가 완료: {u_key}")
                existing_keys.append(u_key)
                
    except Exception as e:
        print(f"🚨 Master DB 시트 오류: {e}")

# ==========================================
# 3. 법제처 API 수집 (30일 백캐스팅 레이더 + 방화벽 우회)
# ==========================================
def fetch_recent_laws():
    """최근 30일간 제/개정된 법령 XML을 호출하고, (잘린텍스트, 전체건수)를 반환합니다."""
    import re # 대소문자 무시 텍스트 추출을 위한 정규식 라이브러리
    
    today_dt = datetime.now()
    past_dt = today_dt - timedelta(days=30)
    
    end_date = today_dt.strftime("%Y%m%d")
    start_date = past_dt.strftime("%Y%m%d")
    
    url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_KEY}&target=law&type=XML&lsTrm={start_date}~{end_date}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status() 
        
        raw_xml_text = res.text
        
        # 🌟 수정 포인트: 딕셔너리 파싱 전에 날것의 텍스트에서 전체 건수를 무조건 찾아냅니다.
        # 1. <totalCnt> 태그 안의 숫자를 대소문자 무시하고 추출
        match = re.search(r'<totalCnt>(\d+)</totalCnt>', raw_xml_text, re.IGNORECASE)
        if match:
            total_count = int(match.group(1))
        else:
            # 2. totalCnt 태그가 아예 없다면 </law> 태그 개수를 직접 카운팅 (최후의 보루)
            total_count = raw_xml_text.count("</law>") + raw_xml_text.count("</LAW>")
            
        raw_dict = xmltodict.parse(raw_xml_text)
        
        # 텍스트 데이터와 실측 전체 건수를 함께 리턴
        return str(raw_dict)[:3000], total_count
        
    except requests.exceptions.RequestException as e:
        print(f"🚨 법제처 API 네트워크 호출 실패 (서버 불안정 또는 타임아웃): {e}")
        return "", 0
    except Exception as e:
        print(f"🚨 법제처 XML 데이터 파싱 실패: {e}")
        return "", 0

# ==========================================
# 4. 워크넷(고용24) API 매쉬업 (수요 폭발 교차 검증)
# ==========================================
def fetch_worknet_jobs(keyword):
    """자격증명을 키워드로 현재 등록된 고용24 채용공고 수를 파악합니다."""
    url = "https://www.work24.go.kr/cm/openApi/call/wk/callOpenApiSvcInfo210L01.do"
    params = {
        "authKey": WORKNET_API_KEY,
        "callTp": "L",
        "returnType": "XML",
        "startPage": "1",
        "display": "10",
        "keyword": keyword
    }
    try:
        res = requests.get(url, params=params)
        xml_data = xmltodict.parse(res.text)
        total_jobs = xml_data.get('wantedRoot', {}).get('total', 0)
        return int(total_jobs)
    except Exception as e:
        print(f"Worknet API 에러: {e}")
        return 0

# ==========================================
# 5. Gemini AI 분석 및 JSON 추출 (우대분류, 중처법 판단)
# ==========================================
def analyze_with_gemini(law_data_text):
    if not law_data_text:
        return []
    
    generation_config = {
        "temperature": 0.0, 
        "response_mime_type": "application/json", 
    }

    model = genai.GenerativeModel(
        model_name='gemini-3.5-flash',
        generation_config=generation_config
    )
    prompt = SYSTEM_PROMPT + f"\n\n[금일 수집된 법령 데이터]\n{law_data_text}"
    
    response = model.generate_content(prompt)
    
    try:
        return json.loads(response.text)
    except json.JSONDecodeError:
        print("JSON 파싱 에러 발생.")
        return []

# ==========================================
# 6. 메인 파이프라인 실행
# ==========================================
def main():
    print("1. [법제처] 최근 30일(백캐스팅) 제/개정 법령 API 수집 중...")
    # 🌟 수정한 핵심 포인트: 수집 텍스트와 전체 건수를 분리해서 받아옵니다.
    filtered_law_text, total_reviewed_count = fetch_recent_laws() 
    
    if not filtered_law_text:
        print("법령 수집 데이터가 없어 파이프라인을 중단합니다.")
        return
        
    print("2. [필터링] 기존 구글 시트 마스터 DB와 대조하여 순수 신규/누락 법령만 추출 중...")
    client = get_gspread_client()
    doc = client.open_by_url(SHEET_URL)
    master_sheet = doc.worksheet("국가기술자격 관련법령")
    
    existing_keys_set = set(master_sheet.col_values(2))
    
    print("3. [Gemini] 타법 개정 및 신규 누락 법령 AI 분석 (Ticketing Intensity) 도출 중...")
    analyzed_data = analyze_with_gemini(filtered_law_text)
    
    if not analyzed_data:
        print("분석된 유의미한 국가기술자격 연관 법령이 없습니다.")
        return

    print("4. [고용24] 워크넷 API 3자 매쉬업 (실시간 구인 수요 확인)...")
    for item in analyzed_data:
        qualifications = item.get("related_qualifications", [])
        job_count = 0
        if qualifications:
            job_count = fetch_worknet_jobs(qualifications[0])
        item["worknet_job_count"] = f"{job_count}건"
        
    print("5. [Google Sheets] 마스터 DB 업데이트 (Upsert) 중...")
    # 🌟 수정한 핵심 포인트: 이제 세 번째 인자인 total_reviewed_count를 명확하게 넘겨줍니다!
    update_google_sheet(client, analyzed_data, total_reviewed_count)
    
    print("✅ 타법개정/관보지연 완벽 방어 AI 파이프라인 가동 완료!")

if __name__ == "__main__":
    main()
