import os
import json
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from openpyxl import Workbook
from openpyxl.styles import Alignment, PatternFill, Font, Border, Side

# 💡 1단계 config 파일에서 설정값들을 가져옵니다.
from config import COLUMNS, WEBHOOK_URL, GCP_SA_JSON, GOOGLE_SHEET_ID, TARGET_DATE

# ==========================================
# 1. 구글 시트 마스터 DB 적재 (V28 분류 로직 적용)
# ==========================================
def upload_to_google_sheet(total_len, high_list, simple_list, target_date=TARGET_DATE):
    """[V29 오버홀] 투트랙 매트릭스 반영 및 MST_ID 기반 Upsert 엔진"""
    if not GCP_SA_JSON or not GOOGLE_SHEET_ID:
        print("  ⚠️ 구글 시트 설정 정보가 없어 적재를 건너뜁니다.")
        return

    try:
        # JSON 파싱 및 인증
        creds_dict = json.loads(GCP_SA_JSON.strip(), strict=False)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)

        # ----------------------------------------------------
        # 1) 총괄현황표 로깅 (실행 기록)
        # ----------------------------------------------------
        try:
            summary_sheet = spreadsheet.worksheet("총괄현황표")
            summary_row = [
                f"{target_date[:4]}년_{target_date[4:6]}월_{target_date[6:]}일",
                total_len,
                len(high_list),
                len(simple_list)
            ]
            summary_sheet.append_row(summary_row)
        except Exception as e:
            print(f"  ⚠️ 총괄현황표 기록 실패: {e}")

        # ----------------------------------------------------
        # 2) 🌟 핵심 엔진: 연관높음 법령 Upsert (Update + Insert)
        # ----------------------------------------------------
        if high_list:
            try:
                ws_high = spreadsheet.worksheet("연관높음 법령")
                existing_records = ws_high.get_all_records()
                
                max_id_num = 0
                natural_key_map = {} # {"법령명|근거조문": 엑셀의 행 번호}
                
                # [스캔 단계] 기존 DB를 읽어 '마지막 번호'와 '자연키'를 파악합니다.
                for idx, record in enumerate(existing_records):
                    mst_id = record.get("MST_ID", "")
                    if mst_id.startswith("HRDK-L-"):
                        try:
                            num = int(mst_id.split("-")[-1])
                            if num > max_id_num: max_id_num = num
                        except: pass
                    
                    # 자연키(Natural Key) 생성
                    nat_key = f"{record.get('법령명','')}|{record.get('근거 조문','')}"
                    natural_key_map[nat_key] = idx + 2 # (+2 이유: 헤더가 1행, 리스트는 0부터 시작하므로)

                new_rows_to_append = []

                # [분기 단계] 신규 수집된 데이터 처리
                for info in high_list:
                    nat_key = f"{info.get('법령명','')}|{info.get('근거 조문','')}"
                    
                    if nat_key in natural_key_map:
                        # 💡 CASE A: 이미 DB에 있는 조문이면 -> 덮어쓰기 (Update)
                        row_idx = natural_key_map[nat_key]
                        existing_id = existing_records[row_idx - 2].get("MST_ID", "")
                        info["MST_ID"] = existing_id # 기존 ID 유지
                        
                        row_data = [info.get(c, "") for c in COLUMNS]
                        # 해당 행을 새로운 분석 결과로 덮어씁니다 (A열 ~ O열)
                        ws_high.update(f'A{row_idx}:O{row_idx}', [row_data]) 
                        print(f"  🔄 [Update] 기존 법령 업데이트 완료: {existing_id}")
                        
                    else:
                        # 💡 CASE B: 완전히 새로운 조문이면 -> 맨 밑에 추가 (Insert)
                        max_id_num += 1
                        new_id = f"HRDK-L-{max_id_num:04d}" # 0001, 0002 형태로 포맷팅
                        info["MST_ID"] = new_id
                        
                        row_data = [info.get(c, "") for c in COLUMNS]
                        new_rows_to_append.append(row_data)
                        print(f"  ✨ [Insert] 신규 법령 식별 (ID 발급): {new_id}")

                # 새로 발급된 녀석들은 한꺼번에 시트 맨 아래에 붙여넣기
                if new_rows_to_append:
                    ws_high.append_rows(new_rows_to_append)
                    
            except Exception as e:
                print(f"  ⚠️ 연관높음 법령 시트 적재 실패: {e}")

        # (참고) 단순관련 리스트는 중요도가 낮으므로 기존처럼 Append 처리만 하거나 
        # 원하시면 위 로직을 동일하게 적용할 수 있습니다. 현재는 유지합니다.
        if simple_list:
            try:
                ws_simple = spreadsheet.worksheet("단순관련 법령")
                # 단순 관련은 별도 ID 부여 없이 Append
                for info in simple_list:
                    info["MST_ID"] = "-" 
                simple_rows = [[info.get(c, "") for c in COLUMNS] for info in simple_list]
                ws_simple.append_rows(simple_rows)
            except Exception as e:
                print(f"  ⚠️ 단순관련 법령 시트 적재 실패: {e}")

        print("  ✅ 구글 시트 마스터 DB 적재 및 Upsert 완료!")

    except Exception as e:
        print(f"  ❌ 구글 시트 연동 중 치명적 에러 발생: {e}")

# ==========================================
# 2. 엑셀 파일 생성 함수 (기존과 동일)
# ==========================================
def create_excel_report(high_impact_laws, simple_related_laws, target_date=TARGET_DATE):
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "연관 높은 법령"
    ws2 = wb.create_sheet(title="국가기술자격 관계 법령(단순 관련)")
    
    header_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))

    for ws, data_list in [(ws1, high_impact_laws), (ws2, simple_related_laws)]:
        ws.append(COLUMNS)
        for row_idx, info in enumerate(data_list, 2):
            ws.append([info.get(c, "") for c in COLUMNS])
        # 기본 열 너비 설정
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 20

    excel_filename = f"V29_법령모니터링_{target_date}.xlsx"
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
