import os  # 🌟 신설: 파일 존재 여부 확인을 위해 추가
import time
import sys
from config import TARGET_DATE, QUALIFICATION_CSV_PATH  # 🌟 신설: CSV 파일 경로 변수 가져오기
from law_scrapper import get_base_laws
from brain_gemini import run_ai_analysis
from report_maker import upload_to_google_sheet, create_excel_report, send_webhook_with_file
# 🌟 [신설] 워크넷 연동 엔진 모듈 가져오기
from worknet_api import get_worknet_job_count

# ==========================================
# 🌟 신설: CSV 파일을 안전하게 읽어오는 함수
# ==========================================
def load_qualification_list(csv_path):
    if not os.path.exists(csv_path):
        print(f"⚠️ 경고: {csv_path} 파일을 찾을 수 없습니다. 빈 문자열로 대체합니다.")
        return ""
    
    # 강력한 인코딩 에러 방어 로직 (utf-8 실패 시 cp949로 재시도)
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        with open(csv_path, 'r', encoding='cp949') as f:
            return f.read()

def main():
    print(f"🚀 [HRDK LAW-RADAR] {TARGET_DATE} 데이터 수집 및 분석 시작...\n" + "="*50)
    start_time = time.time()

    # 🌟 [관제 강화]: 어떤 에러(429 결제 고갈, 타임아웃 등)가 터져도 구글 시트에 흔적을 강제 적재하기 위한 감시 장치
    try:
        # 🌟 신설: AI에게 먹일 '국가기술자격 종목 리스트'를 CSV에서 텍스트로 미리 읽어옵니다.
        qnet_certs_text = load_qualification_list(QUALIFICATION_CSV_PATH)

        # ==========================================
        # 1. 법령 수집 (프리필터링 포함)
        # ==========================================
        laws = get_base_laws()
        
        # 🌟 [신설] 법제처 서버 응답 불능(None) 시 방어 로직 및 에러 로그 기록
        if laws is None:
            err_msg = "Max retries exceeded / ConnectionTimeoutError. 법제처 서버가 연결을 완전히 거부했습니다."
            print(f"❌ [결정적 오류] {err_msg}")
            print(f"  ⚠️ 잘못된 '0건 리포트' 발송 및 마스터 DB 훼손을 방지하기 위해 웹훅 전송을 차단했습니다.")
            
            # 🌟 총괄현황표에 에러 상황을 즉시 박아넣고 종료합니다.
            upload_to_google_sheet(
                total_len=0, 
                target_laws=[], 
                status="🔴 시스템 에러 (법제처 API)", 
                log=err_msg
            )
            return 

        # 수집은 성공했으나 진짜로 오늘 시행 법령이 없는 경우(정상 0건)
        if not laws:
            print(f"  ℹ️ {TARGET_DATE} 시행되는 법령이 없습니다. (0건 기록 및 빈 리포트 전송)")
            
            # 🌟 정상적으로 0건 처리되었음을 기록 인자로 전달합니다.
            upload_to_google_sheet(
                total_len=0, 
                target_laws=[], 
                status="🟢 정상 작동 (공포 법령 없음)", 
                log="해당 일자에 새로 시행되는 국가 법령이 존재하지 않습니다."
            )
            empty_excel = create_excel_report([])
            
            # 메이크닷컴 에러 방지용 빈 파일 전송
            send_webhook_with_file(empty_excel, 0, 0, 0)
            return 

        # 연관/단순 구분을 없애고 'target_laws' 하나로 통합!
        target_laws = [] 
        failed_queue = []
        all_results_for_sheet = [] # 구글 시트에 넣을 전체 마스터 데이터 모음

        # ==========================================
        # 2. AI 정밀 분석 루프
        # ==========================================
        print(f"\n🏎️  총 {len(laws)}건 분석 시작 (직제/조직 법령은 0.1초 컷으로 패스합니다)...")
        for idx, law in enumerate(laws):
            
            # 담당자님 수정 로직 완벽 반영 (로딩 즉시 송출)
            print(f"  [{idx+1}/{len(laws)}] 🔍 {law['법령명']} (제미나이 서버로 전송... 응답 대기중!)")
            
            start_time_loop = time.time()

            if law.get("스킵여부") == True:
                print("    ⏩ [스킵: 조직/직제 관련]")
                # 새로운 COLUMNS(이름표)에 맞춰 딕셔너리 키 이름 변경
                skip_info = {
                    "시행일자": law["시행일자"], "법령명": law["법령명"], 
                    "상세 분석결과": "조직/직제 관련 법령으로 AI 분석 생략", 
                    "연관성_판별": "해당없음", "검토 필요": "X", "조문별 다이렉트 링크": law["링크"]
                }
                all_results_for_sheet.append(skip_info)
                continue

            # 🌟 핵심 수정: AI 두뇌 호출 시 읽어온 종목 리스트(qnet_certs_text)를 같이 던져줍니다!
            success, is_related, law_info = run_ai_analysis(law, qnet_certs_text)
            
            elapsed = time.time() - start_time_loop
            
            if success:
                if is_related != "해당없음": 
                    # 🌟 [신설] AI가 찾아낸 종목을 바탕으로 실시간 워크넷 구인건수 매쉬업!
                    print(f"    📞 고용24 채용시장 수요 조회 중... ({law_info.get('관련 종목')})")
                    job_demand = get_worknet_job_count(law_info.get("관련 종목", ""))
                    law_info["워크넷_실시간_구인건수"] = job_demand
                    
                    target_laws.append(law_info)
                    print(f"    ✅ 관련 법령 식별 ({elapsed:.1f}초) [구인수: {job_demand}]")
                else: 
                    law_info["워크넷_실시간_구인건수"] = "-"
                    print(f"    ❌ 해당없음 ({elapsed:.1f}초)")
                
                all_results_for_sheet.append(law_info)
            else:
                # 🌟 [패자부활전 에러 트래킹기] 에러 메시지를 딕셔너리에 우선 박아둡니다.
                law["error_msg"] = law_info.get('error', '알 수 없음')
                failed_queue.append(law)
                print(f"    ⏩ [분석 실패: {law['error_msg']}] ({elapsed:.1f}초)")

        # ==========================================
        # 3. 패자부활전 (에러 났던 법령들 재시도)
        # ==========================================
        if failed_queue:
            print(f"\n🚑 패자부활전 {len(failed_queue)}건 시작... (서버 안정을 위해 20초 대기)")
            time.sleep(20)
            for law in failed_queue:
                print(f"  [재시도] {law['법령명']}... ", end="", flush=True)
                success, is_related, law_info = run_ai_analysis(law, qnet_certs_text, attempt_count=3)
                
                if success:
                    if is_related != "해당없음": 
                        # 🌟 [신설] 패자부활전 성공 항목도 워크넷 데이터 연동
                        job_demand = get_worknet_job_count(law_info.get("관련 종목", ""))
                        law_info["워크넷_실시간_구인건수"] = job_demand
                        
                        target_laws.append(law_info)
                        print(f"✅ (관련 법령 식별) [구인수: {job_demand}]")
                    else: 
                        law_info["워크넷_실시간_구인건수"] = "-"
                        print("❌ (해당없음)")
                    all_results_for_sheet.append(law_info)
                else:
                    # 🌟 [최종 실패 관제 연동] 3번 찔렀는데도 완전 거부당한 사유를 확정 추출합니다.
                    final_err = law.get("error_msg", "AI Studio 크레딧 소진 혹은 예산 제한")
                    print(f"💀 [최종 실패] 사유: {final_err}")
                    fail_info = {
                        "시행일자": law["시행일자"], "법령명": law["법령명"], 
                        "상세 분석결과": f"AI 분석 최종 실패 (사유: {final_err})", "연관성_판별": "해당없음", 
                        "검토 필요": "O", "워크넷_실시간_구인건수": "-"
                    }
                    all_results_for_sheet.append(fail_info)

        # ==========================================
        # 4. 보고서 작성 및 발송
        # ==========================================
        print("\n📝 구글 시트 마스터 DB 적재 시작...")
        
        # 🌟 [동적 모니터링 상태 평가식] 최종 실패 문자열 개수를 카운트합니다.
        ai_fail_count = len([r for r in all_results_for_sheet if "AI 분석 최종 실패" in str(r.get("상세 분석결과", ""))])
        
        if ai_fail_count > 0:
            status_text = "🟡 부분 지연/실패"
            log_text = f"총 {len(laws)}건 중 {len(target_laws)}건 매칭 완료했으나, Gemini 크레딧 고갈 등으로 인한 AI 분석 실패가 {ai_fail_count}건 검출되었습니다. 결제 잔액을 확인해 주세요."
        else:
            status_text = "🟢 정상 작동"
            log_text = f"총 {len(laws)}건 수집 및 분석 성공. 기술자격 연관 법령 {len(target_laws)}건 마스터 DB Upsert 완료."

        # 함수에 넘겨주는 바구니를 'target_laws' 하나로 통일하면서 🌟수정된 관제 인자(status, log)를 넘깁니다!
        upload_to_google_sheet(len(laws), target_laws, status=status_text, log=log_text)

        print("\n📊 보고용 엑셀 파일 생성 중...")
        excel_filename = create_excel_report(target_laws)

        print("\n🚀 Make.com 웹훅 전송 시작...")
        send_webhook_with_file(excel_filename, len(laws), len(target_laws), 0)

        elapsed_time = time.time() - start_time
        print(f"\n🎉 [종료] 모든 작업이 완벽하게 완료되었습니다! (소요 시간: {elapsed_time/60:.1f}분)")

    except Exception as e:
        # 🌟 [치명적 런타임 튕김 강제 차단] 스크립트 실행 도중 아예 튕기는 미연의 예외(구글 권한 유실 등)까지 완벽 로깅
        fatal_msg = f"프로그램 실행 중 예기치 못한 크래시(런타임 에러) 발생: {str(e)}"
        print(f"\n💥 [치명적 시스템 다운 에러] {fatal_msg}")
        try:
            upload_to_google_sheet(
                total_len=0, 
                target_laws=[], 
                status="🔴 시스템 에러 (런타임 실패)", 
                log=fatal_msg[:400] # 구글 시트 셀 글자 수 오버플로우 방지
            )
        except Exception as sheet_err:
            print(f"  ❌ 구글 시트 마저 마비되어 대시보드 로그 적재 실패: {sheet_err}")
        
        # 깃허브 액션 콘솔에 에러 등불(Exit 1)을 정상적으로 켜주며 강제 안전 종료
        sys.exit(1)

if __name__ == "__main__":
    main()
