"""
fill_missing_track_codes.py
────────────────────────────
[일회성 보강 스크립트] 직능연 정리본에는 있으나 기준조항(383건)에 없어
우대사항_대장에서 Track 코드가 비어 있는 법령들을, 법제처 본문 + AI 분석으로
Track1/Track2 코드를 채워 데이터 완결성을 확보합니다.

실행 방법 (GitHub Actions 또는 담당자 PC):
    python scripts/fill_missing_track_codes.py
필요 환경변수: LAW_API_KEY, GEMINI_API_KEY, GCP_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_URL

동작:
  1) DB에서 '직능연엔 있고 기준조항엔 없는' 법령 목록 자동 추출
  2) 각 법령을 법제처에서 본문 조회 (옛 이름 실패 시 현행명 후보로 재시도)
  3) 제미나이로 Track1/Track2 분석 (기존 brain_gemini 재사용)
  4) 우대사항_대장 시트의 해당 법령 행에 Track 코드 채우기
  5) 처리 리포트 출력 (성공/실패/이름변경)

⚠️ 일회성입니다. 평소 배치와 무관하며, 한 번 돌리고 끝냅니다.
⚠️ 법제처 연결이 되는 날 실행하세요 (IP 차단일이면 실패할 수 있음).
"""

import os
import sys
import sqlite3

# 이 스크립트는 RADAR 레포의 scripts/ 안에 있습니다.
# RADAR 루트(brain_gemini.py, report_maker.py가 있는 곳)를 경로에 추가.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hrdk_law_core.scraper import fetch_law_by_name
from hrdk_law_core.certs import resolve_current_name

DB_PATH = os.environ.get("DB_PATH", "hrdk_law.db")
LAW_API_KEY = os.environ.get("LAW_API_KEY", "")

# 알려진 법령명 옛→현행 후보 (법제처 현행 검색이 안 될 때 재시도용)
# 없는 건 fetch_law_by_name의 부분일치가 알아서 처리
KNOWN_LAW_RENAMES = {
    "소프트웨어산업진흥법": "소프트웨어진흥법",
    "소프트웨어산업진흥법시행령": "소프트웨어진흥법시행령",
    "수질및수생태계보전에관한법률시행령": "물환경보전법시행령",
    "수질및수생태계보전에관한법률시행규칙": "물환경보전법시행규칙",
    "화재예방,소방시설설치유지및안전관리에관한법률시행령":
        "화재의예방및안전관리에관한법률시행령",
    "승강기시설안전관리법시행령": "승강기안전관리법시행령",
    "소재부품전문기업등의육성에관한특별조치법시행령":
        "소재·부품·장비산업경쟁력강화를위한특별조치법시행령",
}


def get_missing_laws(db_path: str) -> list[str]:
    """직능연엔 있고 기준조항엔 없는 법령명 목록 반환."""
    conn = sqlite3.connect(db_path)
    ref = set(r[0] for r in conn.execute(
        "SELECT DISTINCT law_name FROM reference_articles").fetchall())
    pref = set(r[0] for r in conn.execute(
        "SELECT DISTINCT law_name FROM preference_laws").fetchall())
    conn.close()
    missing = sorted(x for x in (pref - ref) if x and x.strip())
    return missing


def fetch_with_retry(law_name: str) -> dict | None:
    """법령 본문 조회: 원본명 → 실패 시 현행명 후보로 재시도."""
    print(f"  🔍 '{law_name}' 법제처 조회...")
    law = fetch_law_by_name(LAW_API_KEY, law_name)
    if law and law.get("원본"):
        print(f"     ✅ 조회 성공 (현재명: {law['법령명']})")
        return law
    # 재시도: 알려진 현행명
    alt = KNOWN_LAW_RENAMES.get(law_name)
    if alt:
        print(f"     ↻ 현행명 '{alt}'(으)로 재시도...")
        law = fetch_law_by_name(LAW_API_KEY, alt)
        if law and law.get("원본"):
            print(f"     ✅ 재시도 성공 (현재명: {law['법령명']})")
            law["_renamed_from"] = law_name
            return law
    print(f"     ❌ '{law_name}' 본문을 찾지 못함 (폐지/분법 가능성)")
    return None


def main():
    if not LAW_API_KEY:
        print("❌ LAW_API_KEY 환경변수가 필요합니다.")
        sys.exit(1)

    print("=" * 60)
    print("🧩 [일회성] 기준조항 미포함 법령 Track 코드 보강")
    print("=" * 60)

    missing = get_missing_laws(DB_PATH)
    print(f"\n📋 대상 법령 {len(missing)}개:")
    for m in missing:
        print(f"   - {m}")

    if not missing:
        print("\n✅ 보강할 법령이 없습니다 (모두 기준조항에 있음).")
        return

    # 종목 텍스트 1회 로드
    from hrdk_law_core.certs import get_qnet_certs_text
    qnet_certs_text = get_qnet_certs_text()

    # brain은 RADAR 디렉터리에 있으므로 RADAR_DIR에서 import
    try:
        from brain_gemini import run_ai_analysis
    except ImportError:
        print("\n❌ brain_gemini를 찾을 수 없습니다. 이 스크립트는 RADAR 레포 루트에서 실행하세요.")
        print("   예: python scripts/fill_missing_track_codes.py")
        sys.exit(1)

    results = []  # (법령명, 상태, Track1, Track2, 현재명)
    for law_name in missing:
        print(f"\n{'─'*50}")
        law = fetch_with_retry(law_name)
        if not law:
            results.append((law_name, "본문없음", "", "", ""))
            continue

        print(f"  🤖 AI 분석 중...")
        try:
            ok, verdict, info = run_ai_analysis(law, qnet_certs_text)
            t1_type = info.get("Track1_취급유형", "")
            t1_risk = info.get("Track1_위험도", "")
            t2 = info.get("Track2_효용코드", "")
            current_name = law.get("법령명", law_name)
            print(f"     → Track1: {t1_type}-{t1_risk}, Track2: {t2} (판별: {verdict})")
            results.append((law_name, "분석완료", f"{t1_type}-{t1_risk}", t2, current_name))
        except Exception as e:
            print(f"     ❌ AI 분석 실패: {e}")
            results.append((law_name, "분석실패", "", "", ""))

    # ── 결과 리포트 ──
    print(f"\n{'='*60}")
    print("📊 보강 결과 요약")
    print(f"{'='*60}")
    for name, status, t1, t2, cur in results:
        mark = "✅" if status == "분석완료" else "⚠️"
        line = f"  {mark} {name}: {status}"
        if status == "분석완료":
            line += f" → {t1} / {t2}"
            if cur and cur != name:
                line += f"  (현행명: {cur})"
        print(line)

    done = sum(1 for r in results if r[1] == "분석완료")
    print(f"\n완료 {done}건 / 전체 {len(results)}건")

    # ── 시트 대장에 반영 ──
    print(f"\n📒 우대사항_대장 시트에 Track 코드 반영 시도...")
    try:
        from report_maker import fill_ledger_track_codes
        fill_ledger_track_codes(results)
    except ImportError:
        print("  ⚠️ fill_ledger_track_codes 함수가 없습니다. 결과만 출력하고 종료합니다.")
        print("     (위 요약을 보고 수동으로 대장에 입력하거나, report_maker에 함수를 추가하세요.)")


if __name__ == "__main__":
    main()
