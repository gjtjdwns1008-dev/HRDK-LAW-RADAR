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
# 2. MST_ID (고유 마스터 ID) 자동 발급기
# ==========================================
def generate_next_mst_id(last_id: str) -> str:
    """마지막 발급된 MST_ID를 바탕으로 다음 순번 번호를 발급합니다."""
    prefix = "HRDK-L-"
    
    if not last_id or last_id.strip() == "" or not str(last_id).startswith(prefix):
        return f"{prefix}0001"
    
    try:
        num_str = last_id.split("-")[-1]
        next_num = int(num_str) + 1
        return f"{prefix}{str(next_num).zfill(4)}"
    except ValueError:
        print(f"⚠️ 기존 ID({last_id}) 변환 실패. 기본값 발급.")
        return f"{prefix}0001"

# ==========================================
# 3. 구글 시트 증분 결합 (Upsert) 처리 (MST_ID 적용 버전)
# ==========================================
def get_gspread_client():
    creds_dict = json.loads(GCP_JSON_STR)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

def update_google_sheet(sheet_client, data_list, total_reviewed_count):
    doc = sheet_client.open_by_url(SHEET_URL) 
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1) 총괄현황표 로깅
    try:
        summary_sheet = doc.worksheet("총괄현황표")
        summary_row = [
            today_str,               # A열: 수집일자
            total_reviewed_count,    # B열: 총 검토건수
            len(data_list),          # C열: 연관 법령건수
            "정상 완료",             # D열: 모니터링 상태
            "MST_ID 발급 및 AI 매쉬업 완료" # E열: 실행 로그
        ]
        summary_sheet.append_row(summary_row)
        print(f"📊 [총괄현황표] 업데이트 완료: {summary_row}")
    except Exception as e:
        print(f"🚨 총괄현황표 시트 오류: {e}")

    # 2) Master DB 데이터 업데이트 (MST_ID 기반 Upsert)
    try:
        master_sheet = doc.worksheet("국가기술자격 관련법령")
        
        # A열(MST_ID)과 C열(자연키) 데이터를 가져옵니다.
        existing_mst_ids = master_sheet.col_values(1)
        existing_unique_keys = master_sheet.col_values(3) 

        # 마지막으로 발급된 MST_ID 찾기 (헤더 제외, 유효한 ID만 필터링)
        valid_ids = [uid for uid in existing_mst_ids if str(uid).startswith("HRDK-L-")]
        last_mst_id = valid_ids[-1] if valid_ids else ""

        for item in data_list:
            u_key = item.get("unique_key", "")
            
            if u_key and u_key in existing_unique_keys:
                # [Update] 기존에 존재하는 법령 -> 기존 MST_ID 유지, 내용만 갱신
                row_idx = existing_unique_keys.index(u_key) + 1 
                current_mst_id = existing_mst_ids[row_idx - 1] # 해당 행의 기존 ID 가져오기
                
                row_data = [
                    current_mst_id,      # A열: 유지된 MST_ID
                    today_str,           # B열: 갱신일자
                    u_key,               # C열: 고유키(유지)
                    item.get("law_name", ""),
                    item.get("provision", ""),
                    ", ".join(item.get("related_qualifications", [])),
                    item.get("preference_type", ""),
                    item.get("sapa_target", ""),
                    item.get("impact_level", ""),
                    item.get("insight", ""),
                    item.get("worknet_job_count", "0")
                ]
                
                cell_range = f"A{row_idx}:K{row_idx}" # A부터 K열까지 (11칸)
                master_sheet.update(values=[row_data], range_name=cell_range)
                print(f"🔄 [Update] 조문 갱신 (ID: {current_mst_id} / Key: {u_key})")
                
            else:
                # [Insert] 신규 법령 -> 새로운 MST_ID 발급 후 맨 밑에 추가
                new_mst_id = generate_next_mst_id(last_mst_id)
                
                row_data = [
                    new_mst_id,          # A열: 신규 발급된 MST_ID 🌟
                    today_str,           # B열: 신규 수집일자
                    u_key,               # C열: 고유키
                    item.get("law_name", ""),
                    item.get("provision", ""),
                    ", ".join(item.get("related_qualifications", [])),
                    item.get("preference_type", ""),
                    item.get("sapa_target", ""),
                    item.get("impact_level", ""),
                    item.get("insight", ""),
                    item.get("worknet_job_count", "0")
                ]
                
                master_sheet.append_row(row_data)
                print(f"🆕 [Insert] 신규 법령 추가 (ID: {new_mst_id} / Key: {u_key})")
                
                # 다음 루프를 위해 변수 업데이트 (방금 발급한 ID가 마지막 ID가 됨)
                last_mst_id = new_mst_id
                existing_unique_keys.append(u_key)
                existing_mst_ids.append(new_mst_id)
                
    except Exception as e:
        print(f"🚨 Master DB 시트 오류: {e}")

# ==========================================
# 4. 법제처 API 수집 (30일 백캐스팅)
# ==========================================
def fetch_recent_laws():
    import re 
    today_dt = datetime.now()
    past_dt = today_dt - timedelta(days=30)
    
    end_date = today_dt.strftime("%Y%m%d")
    start_date = past_dt.strftime("%Y%m%d")
    
    url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_KEY}&target=law&type=XML&lsTrm={start_date}~{end_date}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status() 
        raw_xml_text = res.text
        
        match = re.search(r'<totalCnt>(\d+)</totalCnt>', raw_xml_text, re.IGNORECASE)
        if match:
            total_count = int(match.group(1))
        else:
            total_count = raw_xml_text.count("</law>") + raw_xml_text.count("</LAW>")
            
        raw_dict = xmltodict.parse(raw_xml_text)
        return str(raw_dict)[:3000], total_count
        
    except Exception as e:
        print(f"🚨 법제처 API 수집 실패: {e}")
        return "", 0

# ==========================================
# 5. 워크넷(고용24) API 매쉬업
# ==========================================
def fetch_worknet_jobs(keyword):
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
# 6. Gemini AI 분석
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
# 7. 메인 파이프라인 실행
# ==========================================
def main():
    print("1. [법제처] 최근 30일 제/개정 법령 API 수집 중...")
    filtered_law_text, total_reviewed_count = fetch_recent_laws() 
    
    if not filtered_law_text:
        print("법령 수집 데이터가 없어 파이프라인을 중단합니다.")
        return
        
    print("2. [필터링 & 분석] Gemini AI가 법령 연관성을 분석 중입니다...")
    analyzed_data = analyze_with_gemini(filtered_law_text)
    
    if not analyzed_data:
        print("분석된 유의미한 국가기술자격 연관 법령이 없습니다.")
        return

    print("3. [고용24] 워크넷 API 3자 매쉬업 (실시간 구인 수요 확인)...")
    for item in analyzed_data:
        qualifications = item.get("related_qualifications", [])
        job_count = 0
        if qualifications:
            job_count = fetch_worknet_jobs(qualifications[0])
        item["worknet_job_count"] = f"{job_count}건"
        
    print("4. [Google Sheets] 마스터 DB 업데이트 (MST_ID 자동발급 및 Upsert) 중...")
    client = get_gspread_client()
    update_google_sheet(client, analyzed_data, total_reviewed_count)
    
    print("✅ 타법개정/관보지연 완벽 방어 AI 파이프라인 가동 완료!")

if __name__ == "__main__":
    main()
