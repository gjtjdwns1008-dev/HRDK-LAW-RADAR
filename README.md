# 🏛️ AI 법령 모니터링 및 자격·일자리 매쉬업 시스템
**[대국민 정보제공 자동화 프로젝트 - AI 챌린지 출품작]**

## 💡 프로젝트 개요
매일 쏟아지는 법제처의 법령 제/개정 데이터를 AI가 모니터링하여, 491개 **국가기술자격**의 가치 변화(의무고용, 채용우대 등)를 분석합니다. 
더 나아가 **고용24(워크넷) API**와 매쉬업하여 해당 자격증의 실시간 일자리 수요(구인 건수)까지 원스톱으로 예측해 대국민 정보로 제공하는 자동화 파이프라인입니다.

## 🚀 아키텍처 및 워크플로우
1. **Data Ingestion**: 매일 국가법령정보센터 API를 호출하여 개정된 법령 XML 수집
2. **AI Analysis (Gemini Pro)**: '수험생 수요 폭발 관점(Ticketing Intensity)'에서 분석 수행, 5대 우대유형 및 중대재해처벌법 연계성 판단
3. **Data Mash-up**: 도출된 핵심 자격증명으로 고용24(워크넷) API를 교차 호출하여 실시간 구인 공고 수 파악
4. **Structured Pipeline**: Gemini의 응답을 강제 JSON화 하여 무결성 확보 후, Google Sheets 마스터 DB로 실시간 전송

## ⚙️ 시스템 핵심 기술 (Tech Stack)
- **LLM Engine**: Google Gemini 3.5 Flash (JSON Mode 적용)
- **Automation**: GitHub Actions (Cron Job 매일 스케줄링)
- **Data Storage**: Google Sheets API (gspread)
- **External API**: 법제처 Open API, 한국고용정보원(워크넷) Open API

## 📈 데이터베이스 구조 (Google Sheets)
- **[총괄현황표]**: 일자별 모니터링 건수 및 자동화 성공 여부 로깅
- **[국가기술자격 관련법령]**: Unique Key(법령명+조문) 기반 우대유형, 파급력, 실시간 일자리 데이터 누적
