# Pre-built 도구 API 키 설정 가이드

Moldy에서 Pre-built 도구를 사용하려면 각 서비스의 API 키를 발급받아 설정해야 합니다.
이 문서는 처음 사용하는 분을 위한 단계별 가이드입니다.

---

## 목차

1. [Naver Search 도구](#1-naver-search-도구)
2. [Google Search 도구](#2-google-search-도구)
3. [Google Chat Send 도구](#3-google-chat-send-도구)
4. [Gmail / Calendar 도구](#4-gmail--calendar-도구)
5. [Moldy에서 키 입력하기](#5-moldy에서-키-입력하기)
6. [문제 해결](#6-문제-해결)

---

## 1. Naver Search 도구

**대상 도구**: Naver Blog Search, Naver News Search, Naver Image Search, Naver Shopping Search, Naver Local Search (총 5개)

**필요한 키**: Client ID, Client Secret

### 발급 절차

1. [네이버 개발자센터](https://developers.naver.com)에 로그인합니다.

2. 상단 메뉴에서 **Application** > **애플리케이션 등록**을 클릭합니다.

3. 애플리케이션 정보를 입력합니다:
   - **애플리케이션 이름**: `Moldy` (원하는 이름)
   - **사용 API**: `검색` 선택

4. **비로그인 오픈 API 서비스 환경**에서:
   - 환경 추가: `WEB 설정` 선택
   - 웹 서비스 URL: Moldy가 실행되는 URL 입력 (예: `http://localhost:3000`)

5. **등록하기**를 클릭합니다.

6. 등록 완료 후 **애플리케이션 정보** 페이지에서 확인:
   - **Client ID** — `NAVER_CLIENT_ID`로 사용
   - **Client Secret** — `NAVER_CLIENT_SECRET`으로 사용

### 참고 사항

- 하루 25,000건 호출 가능 (무료)
- 5개 Naver 도구는 동일한 Client ID / Secret을 공유합니다. 한 번만 설정하면 됩니다.

---

## 2. Google Search 도구

**대상 도구**: Google Search, Google News Search, Google Image Search (총 3개)

**필요한 키**: API Key, Search Engine ID (CSE ID)

### Step 1: API Key 발급

1. [Google Cloud Console](https://console.cloud.google.com)에 로그인합니다.

2. 프로젝트를 선택하거나 새 프로젝트를 생성합니다.

3. **API 및 서비스** > **라이브러리**로 이동합니다.

4. `Custom Search JSON API`를 검색하여 **사용 설정**합니다.

5. **API 및 서비스** > **사용자 인증 정보**로 이동합니다.

6. **+ 사용자 인증 정보 만들기** > **API 키**를 클릭합니다.

7. 생성된 API 키를 복사합니다 — `GOOGLE_API_KEY`로 사용

> **보안 팁**: API 키 제한 설정에서 "API 제한사항"을 `Custom Search JSON API`로 한정하면 안전합니다.

### Step 2: Search Engine ID (CSE ID) 발급

1. [Programmable Search Engine](https://programmablesearchengine.google.com)에 접속합니다.

2. **추가** 버튼을 클릭합니다.

3. 검색 엔진 설정:
   - **검색할 사이트**: `전체 웹 검색`을 선택
   - **검색 엔진 이름**: `Moldy Search` (원하는 이름)

4. **만들기**를 클릭합니다.

5. 생성된 검색 엔진의 **검색 엔진 ID**를 복사합니다 — `GOOGLE_CSE_ID`로 사용

### 참고 사항

- 하루 100건 무료, 초과 시 1,000건당 $5
- 3개 Google Search 도구는 동일한 API Key / CSE ID를 공유합니다.

---

## 3. Google Chat Send 도구

**대상 도구**: Google Chat Send (1개)

**필요한 키**: Webhook URL

### 발급 절차

1. [Google Chat](https://chat.google.com)을 엽니다.

2. 메시지를 보낼 **스페이스**를 선택합니다 (또는 새 스페이스 생성).

3. 스페이스 이름 옆 **드롭다운 화살표** > **앱 및 통합**을 클릭합니다.

4. **+ 웹훅 추가**를 클릭합니다.

5. 웹훅 정보를 입력합니다:
   - **이름**: `Moldy Bot` (원하는 이름)
   - **아바타 URL**: (선택사항, 비워둬도 됩니다)

6. **저장**을 클릭합니다.

7. 생성된 **Webhook URL**을 복사합니다.
   - 형식: `https://chat.googleapis.com/v1/spaces/XXXXX/messages?key=...&token=...`

### 참고 사항

- Webhook은 Google Workspace (유료 계정)에서만 사용 가능합니다.
- 메시지 전송만 가능하며, 수신/읽기는 지원하지 않습니다.

---

## 4. Gmail / Calendar 도구

**대상 도구**: Gmail Read, Gmail Send, Calendar List Events, Calendar Create Event, Calendar Update Event (총 5개)

**필요한 키**: OAuth Client ID, OAuth Client Secret, Refresh Token

이 도구들은 Google OAuth2 인증을 사용합니다. 설정이 다소 복잡하지만, 아래 단계를 따라하면 됩니다.

### Step 1: Google Cloud 프로젝트 설정

1. [Google Cloud Console](https://console.cloud.google.com)에 로그인합니다.

2. 프로젝트를 선택하거나 새 프로젝트를 생성합니다.

3. **API 및 서비스** > **라이브러리**에서 다음 API를 각각 **사용 설정**합니다:
   - `Gmail API`
   - `Google Calendar API`

### Step 2: OAuth 동의 화면 설정

1. **API 및 서비스** > **OAuth 동의 화면**으로 이동합니다.

2. **외부** 사용자 유형을 선택하고 **만들기**를 클릭합니다.

3. 필수 정보를 입력합니다:
   - **앱 이름**: `Moldy`
   - **사용자 지원 이메일**: 본인 이메일
   - **개발자 연락처 이메일**: 본인 이메일

4. **범위 추가** 화면에서 다음 범위를 추가합니다:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.send`
   - `https://www.googleapis.com/auth/calendar`

5. **테스트 사용자**에 본인 Google 계정 이메일을 추가합니다.

6. 요약을 확인하고 **대시보드로 돌아가기**를 클릭합니다.

### Step 3: OAuth 클라이언트 ID 생성

1. **API 및 서비스** > **사용자 인증 정보**로 이동합니다.

2. **+ 사용자 인증 정보 만들기** > **OAuth 클라이언트 ID**를 클릭합니다.

3. 설정:
   - **애플리케이션 유형**: `웹 애플리케이션`
   - **이름**: `Moldy OAuth`
   - **승인된 리디렉션 URI**: `https://developers.google.com/oauthplayground`

4. **만들기**를 클릭합니다.

5. 표시된 정보를 복사합니다:
   - **클라이언트 ID** — `OAuth Client ID`로 사용
   - **클라이언트 보안 비밀번호** — `OAuth Client Secret`으로 사용

### Step 4: Refresh Token 획득

1. [OAuth 2.0 Playground](https://developers.google.com/oauthplayground)에 접속합니다.

2. 오른쪽 상단 **톱니바퀴 아이콘** (OAuth 2.0 configuration)을 클릭합니다.

3. 설정:
   - **Use your own OAuth credentials** 체크
   - **OAuth Client ID**: Step 3에서 복사한 Client ID 입력
   - **OAuth Client Secret**: Step 3에서 복사한 Client Secret 입력

4. 왼쪽 패널에서 API 범위를 선택합니다:
   - **Gmail API v1** 펼치기 → `https://www.googleapis.com/auth/gmail.readonly`와 `https://www.googleapis.com/auth/gmail.send` 체크
   - **Google Calendar API v3** 펼치기 → `https://www.googleapis.com/auth/calendar` 체크

5. **Authorize APIs** 버튼을 클릭합니다.

6. Google 계정 선택 → 권한 허용 (테스트 사용자로 추가한 계정이어야 합니다).

7. **Step 2** 화면에서 **Exchange authorization code for tokens** 버튼을 클릭합니다.

8. 응답에서 **Refresh token** 값을 복사합니다 — `Refresh Token`으로 사용

### 참고 사항

- OAuth 동의 화면이 "테스트" 상태이면 테스트 사용자로 등록된 계정만 사용 가능합니다.
- Refresh Token은 만료되지 않지만, OAuth 동의 화면을 "프로덕션"으로 전환하지 않으면 7일마다 만료될 수 있습니다.
- 5개 Gmail/Calendar 도구는 동일한 OAuth 인증 정보를 공유합니다.

---

## 5. Moldy에서 키 입력하기

모든 API 키 발급이 완료되면 Moldy UI에서 설정합니다.

1. Moldy 앱에 로그인합니다.

2. 사이드바에서 **도구 관리** 페이지로 이동합니다.

3. 설정하려는 도구 카드를 찾습니다.
   - Pre-built 도구는 파란색 `Pre-built` 배지가 표시됩니다.
   - 키가 미설정이면 노란색 경고가 표시됩니다.

4. **키 설정** 버튼을 클릭합니다.

5. 발급받은 키 값을 입력합니다:

   | 도구 그룹 | 입력 필드 |
   |-----------|-----------|
   | Naver Search (5개) | Client ID, Client Secret |
   | Google Search (3개) | API Key, Search Engine ID |
   | Google Chat Send | Webhook URL |
   | Gmail / Calendar (5개) | OAuth Client ID, OAuth Client Secret, Refresh Token |

6. **저장**을 클릭합니다.

7. 저장 완료 후 초록색 체크 아이콘으로 상태가 변경됩니다.

> **참고**: 같은 그룹의 도구는 인증 정보를 공유합니다. 예를 들어 Naver Blog Search에 키를 설정하면 다른 Naver 도구에서도 같은 키가 사용됩니다 — 단, 각 도구별로 개별 설정도 가능합니다.

---

## 6. 문제 해결

### "API 키가 유효하지 않습니다"

- 키 값을 다시 확인하세요. 복사 시 앞뒤 공백이 포함되지 않았는지 확인합니다.
- Google API Key의 경우, 해당 API(Custom Search JSON API, Gmail API 등)가 활성화되어 있는지 확인합니다.

### "권한이 없습니다" (403 에러)

- Naver: 애플리케이션의 "사용 API"에 `검색`이 포함되어 있는지 확인합니다.
- Google: API Key에 API 제한이 걸려 있다면 필요한 API가 허용 목록에 있는지 확인합니다.
- Gmail/Calendar: OAuth 동의 화면의 테스트 사용자에 해당 계정이 등록되어 있는지 확인합니다.

### "Refresh Token이 만료되었습니다"

- OAuth Playground에서 새 Refresh Token을 발급받아 Moldy에 다시 입력하세요.
- 장기 사용 시 Google Cloud Console에서 OAuth 동의 화면을 "프로덕션"으로 전환하면 만료가 방지됩니다.

### Google Chat Webhook이 작동하지 않습니다

- Webhook URL이 정확한지 확인합니다 (전체 URL 복사 필요).
- Google Workspace (유료) 계정인지 확인합니다. 무료 Gmail 계정에서는 Webhook을 사용할 수 없습니다.
