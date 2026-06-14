"""
HRDK LAW-RADAR - main.py (Phase 1 업데이트)
--------------------------------------------
변경 사항:
  - law_scrapper.py → hrdk_law_core.scraper (공유 코어)
  - worknet_api.py  → hrdk_law_core.worknet  (공유 코어)
  - 하이브리드 검증 (직능연 × AI 투트랙) 신규 추가
  - SQLite 지식베이스 daily_analysis 누적 저장 신규 추가
"""

import os
import time
import sys

from config import (
    TARGET_DATE, QUALIFICATION_CSV_PATH,
    LAW_API_KEY, WORKNET_API_KEY,
    GCP_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_URL,
    DB_PATH,          # ← config.py에 추가 필요 (기본값: "hrdk_law.db")
)

# ── 공유 코어 임포트 ─────────────────────────────────────
from hrdk_law_core.scraper  import get_base_laws
from hrdk_law_core.worknet  import get_worknet_job_count
from hrdk_law_core.db       import KnowledgeBase
from hrdk_law_core.hybrid   import verify_with_krivet

# ── 기존 모듈 (변경 없음) ────────────────────────────────
from brain_gemini   import run_ai_analysis
from report_maker   import upload_to_google_sheet, create_excel_report, send_webhook_with_file


def load_qualification_list(csv_path: str) -> str:
    """종목 CSV를 문자열로 읽습니다. 인코딩 자동 폴백."""
    if not os.path.exists(csv_path):
        print(f"⚠️ 경고: {csv_path} 파일을 찾을 수 없습니다.")
        return ""
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(csv_path, "r", encoding="cp949") as f:
            return f.read()


def main():
    print(f"🚀 [HRDK LAW-RADAR] {TARGET_DATE} 데이터 수집 및 분석 시작...\n" + "=" * 50)
    start_time = time.time()

    # ── 지식베이스 로드 ──────────────────────────────────
    kb = KnowledgeBase(DB_PATH)
    print(f"📚 지식베이스 로드 완료 ({DB_PATH})")

    try:
        qnet_certs_text = load_qualification_list(QUALIFICATION_CSV_PATH)

        # ═══════════════════════════════════════════════════
        # 1. 법령 수집 (공유 코어 사용)
        # ═══════════════════════════════════════════════════
        laws = get_base_laws(api_key=LAW_API_KEY, target_date=TARGET_DATE)

        if laws is None:
            err_msg = "법제처 서버 연결 완전 실패. 안전하게 종료합니다."
            print(f"❌ [결정적 오류] {err_msg}")
            upload_to_google_sheet(
                total_len=0, target_laws=[],
                status="🔴 시스템 에러 (법제처 API)", log=err_msg,
            )
            sys.exit(1)  # 🛠️ [타임아웃 재시도 패치] return → sys.exit(1)
            # 종료코드 1 = 워크플로우가 "실패"로 인식 → main.yml 재시도 루프 트리거

        if not laws:
            print(f"  ℹ️ {TARGET_DATE} 시행 법령 없음. (0건 처리)")
            upload_to_google_sheet(
                total_len=0, target_laws=[],
                status="🟢 정상 작동 (공포 법령 없음)",
                log="해당 일자에 새로 시행되는 국가 법령이 없습니다.",
            )
            send_webhook_with_file(create_excel_report([]), 0, 0, 0)
            return

        target_laws, failed_queue, all_results = [], [], []

        # ═══════════════════════════════════════════════════
        # 2. AI 정밀 분석 루프
        # ═══════════════════════════════════════════════════
        print(f"\n🏎️  총 {len(laws)}건 분석 시작...")
        for idx, law in enumerate(laws):
            print(f"  [{idx+1}/{len(laws)}] 🔍 {law['법령명']} (Gemini 전송 중...)")
            t0 = time.time()

            if law.get("스킵여부"):
                print("    ⏩ [스킵: 조직/직제 관련]")
                all_results.append({
                    "시행일자": law["시행일자"], "법령명": law["법령명"],
                    "상세 분석결과": "조직/직제 관련 법령으로 AI 분석 생략",
                    "연관성_판별": "해당없음", "검토 필요": "X",
                    "조문별 다이렉트 링크": law["링크"],
                })
                continue

            success, is_related, law_info = run_ai_analysis(law, qnet_certs_text)
            elapsed = time.time() - t0

            if success:
                if is_related != "해당없음":
                    # ── 워크넷 매쉬업 (공유 코어 사용) ──
                    print(f"    📞 워크넷 수요 조회 중... ({law_info.get('관련 종목')})")
                    job_demand = get_worknet_job_count(
                        law_info.get("관련 종목", ""), api_key=WORKNET_API_KEY
                    )
                    law_info["워크넷_실시간_구인건수"] = job_demand

                    # ── 🌟 [신규] 하이브리드 검증 ──────
                    law_info = verify_with_krivet(law_info, kb)
                    hybrid_tag = {
                        "직능연_검증":  "✅ 직능연_검증",
                        "AI_스마트_보정": "💡 AI_보정",
                        "AI_신규판단":  "🆕 신규",
                    }.get(law_info.get("hybrid_status", ""), "")

                    target_laws.append(law_info)
                    print(f"    ✅ 관련 법령 식별 ({elapsed:.1f}초) [구인: {job_demand}] [{hybrid_tag}]")
                else:
                    law_info["워크넷_실시간_구인건수"] = "-"
                    print(f"    ❌ 해당없음 ({elapsed:.1f}초)")

                all_results.append(law_info)
            else:
                law["error_msg"] = law_info.get("error", "알 수 없음")
                failed_queue.append(law)
                print(f"    ⏩ [분석 실패: {law['error_msg']}] ({elapsed:.1f}초)")

        # ═══════════════════════════════════════════════════
        # 3. 패자부활전
        # ═══════════════════════════════════════════════════
        if failed_queue:
            print(f"\n🚑 패자부활전 {len(failed_queue)}건 시작... (20초 대기)")
            time.sleep(20)
            for law in failed_queue:
                print(f"  [재시도] {law['법령명']}... ", end="", flush=True)
                success, is_related, law_info = run_ai_analysis(law, qnet_certs_text, attempt_count=3)
                if success:
                    if is_related != "해당없음":
                        job_demand = get_worknet_job_count(
                            law_info.get("관련 종목", ""), api_key=WORKNET_API_KEY
                        )
                        law_info["워크넷_실시간_구인건수"] = job_demand
                        law_info = verify_with_krivet(law_info, kb)
                        target_laws.append(law_info)
                        print(f"✅ (구인: {job_demand}) [{law_info.get('hybrid_status','')}]")
                    else:
                        law_info["워크넷_실시간_구인건수"] = "-"
                        print("❌ (해당없음)")
                    all_results.append(law_info)
                else:
                    final_err = law.get("error_msg", "Gemini 크레딧 소진")
                    print(f"💀 [최종 실패] {final_err}")
                    all_results.append({
                        "시행일자": law["시행일자"], "법령명": law["법령명"],
                        "상세 분석결과": f"AI 분석 최종 실패 (사유: {final_err})",
                        "연관성_판별": "해당없음", "검토 필요": "O",
                        "워크넷_실시간_구인건수": "-",
                    })

        # ═══════════════════════════════════════════════════
        # 4. SQLite 지식베이스 누적 저장 (신규)
        # ═══════════════════════════════════════════════════
        if target_laws:
            print(f"\n💾 SQLite 지식베이스 누적 저장 중... ({len(target_laws)}건)")
            for law_info in target_laws:
                try:
                    kb.upsert_daily(law_info)
                except Exception as e:
                    print(f"  ⚠️ SQLite 저장 실패 ({law_info.get('법령명', '')}): {e}")
            print("  ✅ SQLite 저장 완료")

        # ═══════════════════════════════════════════════════
        # 5. 구글 시트 & 보고서 (기존과 동일)
        # ═══════════════════════════════════════════════════
        print("\n📝 구글 시트 마스터 DB 적재 시작...")
        ai_fail_count = sum(
            1 for r in all_results if "AI 분석 최종 실패" in str(r.get("상세 분석결과", ""))
        )
        status_text = "🟡 부분 지연/실패" if ai_fail_count > 0 else "🟢 정상 작동"
        log_text = (
            f"총 {len(laws)}건 중 {len(target_laws)}건 매칭. "
            f"AI 실패 {ai_fail_count}건. "
            f"하이브리드: "
            f"직능연검증={sum(1 for r in target_laws if r.get('hybrid_status')=='직능연_검증')}건, "
            f"AI보정={sum(1 for r in target_laws if r.get('hybrid_status')=='AI_스마트_보정')}건, "
            f"신규={sum(1 for r in target_laws if r.get('hybrid_status')=='AI_신규판단')}건"
        )

        upload_to_google_sheet(len(laws), target_laws, status=status_text, log=log_text)

        print("\n📊 보고용 엑셀 파일 생성 중...")
        excel_filename = create_excel_report(target_laws)

        print("\n🚀 Make.com 웹훅 전송 시작...")
        send_webhook_with_file(excel_filename, len(laws), len(target_laws), 0)

        elapsed_total = time.time() - start_time
        print(f"\n🎉 [종료] 완료! (소요 시간: {elapsed_total / 60:.1f}분)")

    except Exception as e:
        fatal_msg = f"런타임 에러: {str(e)}"
        print(f"\n💥 [치명적 오류] {fatal_msg}")
        try:
            upload_to_google_sheet(
                total_len=0, target_laws=[],
                status="🔴 시스템 에러 (런타임 실패)",
                log=fatal_msg[:400],
            )
        except Exception as sheet_err:
            print(f"  ❌ 구글 시트 로깅도 실패: {sheet_err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
