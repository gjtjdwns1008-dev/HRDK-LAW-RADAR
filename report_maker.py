import os
import json
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from openpyxl import Workbook
from openpyxl.styles import Alignment, PatternFill, Font, Border, Side

# 💡 1단계 config 파일에서 설정값들을 가져옵니다.
from config import COLUMNS, WEBHOOK_URL, GCP_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_URL, TARGET_DATE

# 🌟 [신설 헬퍼] 숫자를 엑셀 열 문자(1->A, 17->Q)로 변환해주는 함수
def get_column_letter(n):
    string = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        string = chr(65 + remainder) + string
    return string

# ==========================================
# 1. 구글 시트 마스터 DB 적재 (통합 Upsert 엔진)
# ==========================================
# 🌟 [고도화] 관제용 상태(status)와 로그(log) 파라미터 기본값 추가
def upload_to_google_sheet(total_len, target_laws, target_date=TARGET_DATE, status="🟢 정상 작동", log="특이사항 없음"):
    """[HRDK LAW-RADAR 오버홀] 국가기술자격 관련 법령 전체 통합 Upsert 및 총괄현황 관제 모니터링 기록"""
    if not GCP_SERVICE_ACCOUNT_JSON or not GOOGLE_SHEET_URL:
        print("  ⚠️ 구글 시트 설정 정보가 없어 적재를 건너뜁니다.")
        return

    try:
        # 인증 로직 (기존과 동일)
        creds_dict = json.loads(GCP_SERVICE_ACCOUNT_JSON.strip(), strict=False)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(GOOGLE_SHEET_URL)

        # 1) 총괄현황표 로깅
        # 🌟 [요구사항 반영] 총괄현황표 실시간 타임스탬프 관제 로깅
        try:
            summary_sheet = spreadsheet.worksheet("총괄현황표")
            
            # 한국 시간(KST) 기준으로 초 단위까지 정확하게 구하기
            from datetime import datetime, timezone, timedelta
            current_now = datetime.now(timezone(timedelta(hours=9)))
            current_time_str = current_now.strftime("%Y-%m-%d %H:%M:%S")
            
            # 확장된 5대 통합 헤더 규격에 맞춰 한 줄 적재
            summary_row = [
                current_time_str,    # 1. 수집일자 (초 단위 시간 포함 🌟)
                total_len,           # 2. 총 검토건수
                len(target_laws),    # 3. 연관 법령건수
                status,              # 4. 모니터링 상태 (정상/에러 표기 🌟)
                log                  # 5. 실행 로그 및 비고 (상세 내용 표기 🌟)
            ]
            summary_sheet.append_row(summary_row)
            print(f"  📊 [총괄현황표 관제 기록 성공] 상태: {status}")
        except Exception as e:
            print(f"  ⚠️ 총괄현황표 관제 데이터 기록 실패: {e}")

        # 2) 🌟 핵심 엔진: 하나의 시트("국가기술자격 관련법령")에 전부 Upsert
        if target_laws:
            try:
                ws_main = spreadsheet.worksheet("국가기술자격 관련법령") # 시트 이름 변경!
                existing_records = ws_main.get_all_records()
                
                max_id_num = 0
                natural_key_map = {}
                
                for idx, record in enumerate(existing_records):
                    mst_id = record.get("MST_ID", "")
                    if mst_id.startswith("HRDK-L-"):
                        try:
                            num = int(mst_id.split("-")[-1])
                            if num > max_id_num: max_id_num = num
                        except: pass
                    
                    nat_key = f"{record.get('법령명','')}|{record.get('근거 조문','')}"
                    natural_key_map[nat_key] = idx + 2

                new_rows_to_append = []

                # 🌟 [개선] COLUMNS 길이에 맞춰 끝 열 범위 문자 자동 계산 (예: 17개면 Q)
                end_col_letter = get_column_letter(len(COLUMNS))

                for info in target_laws:
                    nat_key = f"{info.get('법령명','')}|{info.get('근거 조문','')}"
                    
                    if nat_key in natural_key_map:
                        # Update
                        row_idx = natural_key_map[nat_key]
                        existing_id = existing_records[row_idx - 2].get("MST_ID", "")
                        info["MST_ID"] = existing_id 
                        row_data = [info.get(c, "") for c in COLUMNS]
                        
                        # 🌟 하드코딩(A~O)을 유연한 계산식(A~Q)으로 변경하여 워크넷 데이터 누락 방지!
                        ws_main.update(f'A{row_idx}:{end_col_letter}{row_idx}', [row_data]) 
                        print(f"  🔄 [Update] {existing_id}")
                    else:
                        # Insert
                        max_id_num += 1
                        new_id = f"HRDK-L-{max_id_num:04d}" 
                        info["MST_ID"] = new_id
                        row_data = [info.get(c, "") for c in COLUMNS]
                        new_rows_to_append.append(row_data)
                        print(f"  ✨ [Insert] {new_id}")

                if new_rows_to_append:
                    ws_main.append_rows(new_rows_to_append)
                    
            except Exception as e:
                print(f"  ⚠️ 국가기술자격 관련법령 시트 적재 실패: {e}")

        print("  ✅ 구글 시트 통합 마스터 DB 적재 및 Upsert 완료!")

    except Exception as e:
        print(f"  ❌ 구글 시트 연동 중 에러: {e}")

# ==========================================
# 2. 엑셀 파일 생성 함수 (시트 1개로 단일화)
# ==========================================
def create_excel_report(target_laws, target_date=TARGET_DATE):
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "국가기술자격 관련법령" # 단일 시트
    
    ws1.append(COLUMNS)
    for row_idx, info in enumerate(target_laws, 2):
        ws1.append([info.get(c, "") for c in COLUMNS])
    for col in ws1.columns:
        ws1.column_dimensions[col[0].column_letter].width = 20

    excel_filename = f"HRDK-LAW-RADAR_일일모니터링_{target_date}.xlsx"
    wb.save(excel_filename)
    return excel_filename

# ==========================================
# 3. 메이크닷컴 웹훅 전송 (기존과 동일)
# ==========================================
def send_webhook_with_file(fname, total, high, simple, target_date=TARGET_DATE):
    if not WEBHOOK_URL: return
    # 🌟 [근본 원인 해결!] 메일/웹훅으로 보낼 때도 사람이 읽기 편한 날짜로 변환해서 쏩니다!
    display_date = f"{target_date[:4]}년 {target_date[4:6]}월 {target_date[6:]}일"
    
    # 이제 Make.com은 "20260428"이 아니라 "2026년 04월 28일" 이라는 데이터를 받게 됩니다!
    summary_data = {"date": display_date, "total": f"{total}건", "high": f"{high}건", "simple": f"{simple}건"}
    try:
        if fname and os.path.exists(fname):
            with open(fname, 'rb') as f:
                requests.post(WEBHOOK_URL, data=summary_data, files={'file': (os.path.basename(fname), f, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
        else:
            requests.post(WEBHOOK_URL, data=summary_data)
        print("  ✅ 웹훅 전송 성공!")
    except Exception as e: print(f"  ❌ 웹훅 에러: {e}")
