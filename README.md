# Kostolany Studio

`kostolany.wisdom`를 위한 로컬 뉴스·콘텐츠 작업실입니다. React + TypeScript, FastAPI, SQLite로 구성했습니다. 뉴스 수집부터 우선순위 선정, 책 근거 검색, 영어 원고, 캐러셀 PNG·자막 릴스 MP4, 검수, Buffer 예약과 게시 상태·성과 조회까지 연결합니다.

실제 Instagram 게시와 운영 자동화를 완성하기 위한 추가 작업은 [Instagram 연동 백로그](docs/INSTAGRAM_INTEGRATION_BACKLOG.md)에 우선순위·선행 작업·완료 기준과 함께 정리했습니다.

## 실행

1. 이 폴더의 `.env`에 `OPENAI_API_KEY`를 입력합니다. 현재 빈 파일이 준비되어 있습니다. 키를 채팅이나 화면에 붙여 넣을 필요는 없습니다.
2. `start.cmd`를 더블클릭합니다. 처음에는 의존성을 설치하고 화면을 빌드합니다.
3. 브라우저에서 **http://127.0.0.1:8765**를 엽니다. 실행 창에서 `Ctrl+C`로 종료합니다.

Python 3.13과 Node.js 24에서 확인했습니다. 프로그램을 실행하는 동안만 로컬 작업이 계속됩니다. 서버는 한 개만 실행하세요. 계정 인증이 있는 인터넷 공개용 서버가 아닙니다.

```dotenv
OPENAI_API_KEY=
OPENAI_MODEL=gpt-6-astra
OPENAI_REASONING_EFFORT=xhigh
BUFFER_API_KEY=
BUFFER_CHANNEL_ID=
RSS_INTERVAL_MINUTES=0
```

`.env`는 다음 작업 때 다시 읽습니다. 같은 이름의 OS 환경 변수가 있으면 그 값이 우선합니다. API 키나 전체 책은 프런트엔드 빌드에 포함하지 않습니다. AI는 실행 버튼을 누를 때만 호출하며, 실패한 유료 요청을 자동 재시도하지 않습니다.

## 1~6단계 사용 순서

| 단계 | 화면에서 할 일 | 결과 |
|---|---|---|
| 1. 뉴스 수집 | 뉴스 수집·선정 → 뉴스 수집 | RSS 기사 저장, URL·피드 GUID 중복 방지 |
| 2. 주제 선정 | AI 우선순위 | 최근 수집 40건의 점수·한국어 이유·동일 사건 그룹 |
| 3. 근거와 해석 | 뉴스 선택 → 원문 가져오기 → 실제 원문과 비교 → 본문 확인·저장 → 영어 초안 생성 | 뉴스 근거와 책 참고 구간을 보관한 초안 |
| 4. 두 형식 제작 | 콘텐츠 제작 → 초안 열기 → 원고 수정·저장 → 캐러셀·릴스 제작 | 1080×1350 PNG, 카드 ZIP, 1080×1920 MP4, 캡션, 출처 목록 |
| 5. 검수·예약 | 완성본과 출처 확인 → 검수 완료 → 공개 파일 URL·예약 시각 입력 → Buffer에 예약 전송 | 별도 형식별 예약, 실제 게시 여부 조회 |
| 6. 성과 분석 | 게시·성과 확인 → 성과 분석 → AI 분석 만들기 | 제공된 지표, 갱신 시각, 관찰·다음 실험·해석의 한계 |

수집한 뉴스 목록은 Excel로 내보낼 수 있습니다. SQLite가 원본 저장소입니다. RSS 요약은 원문 전체가 아니므로, 원문 확인 전에는 초안을 생성하지 않습니다. 자동 원문 추출은 사이트에 따라 실패하거나 불필요한 문장을 포함할 수 있어 직접 본문을 수정할 수 있습니다. 유료벽 우회 기능은 없습니다.

현재 릴스는 **자막 장면이 이어지는 무음 영상**입니다. 음성·음악·실사 장면 생성은 포함하지 않습니다. Astra는 텍스트 생성에 사용합니다. 문구와 뉴스 사실, 책 해석의 타당성은 사람이 최종 확인합니다. 근거 문장이 원문에 존재하는지와 인용한 자료 ID는 프로그램에서도 검사하지만, 해석의 정확성까지 자동 보장하지는 않습니다.

## 책 전체를 나중에 넣는 방법

자료실에서 책을 선택하고 `.txt` 또는 `.md` 파일을 등록합니다. UTF-8을 권장하며 CP949도 읽습니다. 파일당 최대 10MB이고 한 책을 여러 장별 파일로 나눠 넣을 수 있습니다. 판본·출판연도도 적어 주세요.

지원하는 책은 아래 세 권으로 제한했습니다.

1. 돈, 뜨겁게 사랑하고 차갑게 다루어라
2. 투자는 심리게임이다
3. 실전 투자강의 (판본에 따라 ‘실전 투자 강의’)

`knowledge/book-template.txt`를 참고하세요.

```text
# 제1장 장 제목
[p. 12]
직접 입력한 본문…

[p. 13]
다음 페이지의 본문…
```

페이지를 모르면 만들지 말고 장 제목만 적습니다. 책 전체는 로컬 DB에 보관하고, 장·페이지 표시를 유지한 약 1,800자 구간으로 나눕니다. AI가 만든 한영 검색어로 관련 구간을 최대 12개 찾아 작성 근거로 보냅니다. 별도 임베딩 서비스나 모델 미세조정은 사용하지 않습니다. 키워드 검색이라 누락될 수 있으며, 관련 구간이 없으면 생성이 중단됩니다.

직접 입력한 자료가 한 개라도 등록되면 **그 책의 임시 요약은 AI 검색에서 제외**합니다. 파일 내용을 수정하려면 새 파일을 등록하고 이전 자료를 삭제하세요. 동일 파일의 중복 등록은 방지합니다. 사용자가 입력한 책 원문과 전체 파일을 외부에 게시하지 않으며, AI 호출 시 검색된 관련 구간만 OpenAI에 전송합니다. 초안은 생성 당시의 참고 구간을 별도 보관하므로 이후 자료실을 수정해도 근거 기록이 남습니다.

## 현재 임시 참고 자료의 범위

`knowledge/seed.json`의 6개 항목은 **책 전체를 읽고 만든 요약이 아닙니다.** 아래 판매 페이지의 출판사 제공 공개 발췌를 짧게 재서술한 임시 자료입니다. 독자 리뷰나 다른 책의 내용은 사용하지 않았습니다. 각 항목에 출처와 공개 발췌 위치를 남겼으며, 확인하지 않은 페이지 번호는 넣지 않았습니다.

- [1권 공개 발췌](https://www.yes24.com/product/goods/117228825): 경제와 시장의 움직임 차이, 추천자의 이해관계.
- [2권 공개 발췌](https://www.yes24.com/product/goods/123254145): 참여자의 심리·자금 사정, 상승과 거래량 해석.
- [3권 공개 발췌](https://www.yes24.com/product/goods/122091748): 논리에 근거한 독립 판단, 채권의 개별 조건 검토.

공개 콘텐츠는 코스톨라니의 직접 인용이 아닌 **우리의 현대적 해석**으로 생성하도록 설정했습니다. 전체 책을 등록한 뒤에도 원문 장문 복제나 확인하지 않은 인용을 생성하도록 요청하지 않습니다.

## Buffer 연결과 게시

OpenAI 키는 AI 단계용입니다. 실제 Instagram 예약에는 별도로 다음이 필요합니다.

- Buffer에 연결한 Instagram 프로페셔널 계정
- 개인 Buffer API 키와 해당 Instagram 채널 ID
- 제작한 이미지·영상 파일의 공개 HTTPS URL

[Buffer 첫 게시 안내](https://developers.buffer.com/guides/your-first-post.html)에 계정·채널 조회 방법이 있습니다. `.env`에 키와 채널 ID를 넣고 ‘연결 설정 → 연결 확인’을 실행하세요. 공개 파일 호스팅 자동 업로드는 현재 포함하지 않습니다. 직접 호스팅한 URL을 입력하며, 게시 전 공개 파일과 검수한 파일의 SHA-256을 비교합니다. 변환된 이미지나 다른 버전이면 전송을 막습니다.

예약 시각은 한국 시간으로 입력하며 미국 동부 시간을 함께 표시합니다. 서버에는 UTC로 보관합니다. 검수한 버전만 예약할 수 있고 수정하면 제작 결과와 승인을 해제합니다. 게시 요청이 이미 있는 초안은 고정됩니다. 예약 이후 변경·취소는 Buffer에서 처리하세요.

전송 응답이 끊기면 `unknown`으로 기록하고 자동으로 다시 게시하지 않습니다. Buffer에서 실제 생성 여부를 확인하고 해당 게시 ID를 입력해 연결합니다. 채널과 캡션이 일치해야 합니다. 거절되거나 실패한 게시 요청도 자동 재전송하지 않습니다. 예약 접수만으로 게시 완료라고 표시하지 않으며, 상태 조회에서 `sent`를 확인해야 합니다.

성과 조회는 현재 수동 버튼으로 실행합니다. Buffer가 반환한 지표만 저장하며 미제공 값과 0을 구분합니다. 지표 갱신은 지연될 수 있습니다. 국가별 독자 비중, 실시간 지표, Instagram 음악·공동작업자 기능은 이 버전에서 지원하지 않습니다.

## 로컬 데이터와 자동 수집

- `data/studio.db`: 뉴스, 책 원문, 참고 구간, 초안, 검수 버전, 작업·게시·성과 기록
- `data/artifacts/`: 제작한 이미지·영상·ZIP·캡션·출처 목록
- `.env`: 사용자 키 (Git 제외)
- `knowledge/seed.json`: 추적 가능한 공개 자료의 임시 요약

백업은 앱을 종료한 뒤 `.env`와 `data` 폴더를 안전한 장소에 복사하면 됩니다. `RSS_INTERVAL_MINUTES=60`이면 실행 중에 60분마다 자동 수집합니다. 0은 수동이며 최소 자동 간격은 15분입니다. 기본값은 수동입니다. AI 생성과 예약은 자동 실행되지 않습니다.

## 검증

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd run build
```

브라우저 검증 스크립트 `scripts/browser_smoke.py`는 별도 Playwright Python 설치와 Microsoft Edge, 실행 중인 로컬 앱을 사용합니다. 유료 API 호출은 하지 않습니다. 개발 시 백엔드는 8765, Vite는 5173에서 실행합니다.

2026-11-13 확인 결과:

- 실제 BBC Business 54건, CNBC US 30건, 연준 15건 수집: 총 99건
- 로컬 테스트 14개: RSS 중복, 자료 범위·장/페이지·우선순위, 근거 검사, AI 불완전 응답 처리, 검수 무효화, 키 비노출, Excel 수식 처리, 작업 실패, 실제 PNG·MP4 생성, Buffer 중복 방지·상태 구분
- 프런트엔드 TypeScript 검사·빌드 통과
- 브라우저: 7개 화면, 자료 검색, 뉴스 확인창, 390px 모바일 화면, JavaScript 오류 없음
- **OpenAI 실호출, Buffer 실계정 연결·실제 Instagram 게시·실제 성과 조회는 미검증**: 키·계정·공개 미디어 URL이 아직 없습니다. 해당 API의 응답 처리는 격리된 대체 응답으로 검증했습니다. 테스트용 콘텐츠를 실제 초안 목록에 넣거나 실제 게시하지 않았습니다.

## 구현에 확인한 공식 문서

- [OpenAI GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), [Reasoning](https://developers.openai.com/api/docs/guides/reasoning), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Buffer 게시·예약](https://developers.buffer.com/guides/posts-and-scheduling.html), [이미지 게시와 공개 URL](https://developers.buffer.com/examples/create-image-post.html), [게시 지표 조회](https://developers.buffer.com/examples/get-post-metrics.html), [API 입력·출력 명세](https://developers.buffer.com/reference.html)
- [연준 RSS](https://www.federalreserve.gov/feeds/feeds.htm), [feedparser](https://feedparser.readthedocs.io/en/latest/), [FastAPI](https://fastapi.tiangolo.com/), [FFmpeg](https://ffmpeg.org/ffmpeg.html), [SQLite 사용 범위](https://www.sqlite.org/whentouse.html)

뉴스 우선순위 기준·검색 구간 크기·영상 형식·실행 간격은 이 앱의 운영 설계입니다. 이 설정이 미국 노출이나 팔로워 증가를 보장한다는 의미는 아닙니다.
