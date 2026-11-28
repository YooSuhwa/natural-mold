# Login

A user signs in with email and password on `/login`. Wrong credentials show an inline
error and stay on `/login`; valid credentials land on the personalized dashboard.

## Sub-features

- `login-reject` wrong password shows `이메일 또는 비밀번호가 올바르지 않습니다` and stays on `/login`.
- `login-success` valid credentials redirect to `/` with the heading `<이름>님…`.
- `login-session` the session cookie authenticates `/api/auth/me` afterwards.
- `register` (not scripted yet) `/register` sign-up form with `가입하기`, then the onboarding dialog.

## How to get to it (user POV)

- Open any page while logged out → redirected to `/login`.
- Open `/login` directly.

## Driving it with verify-moldy

Preconditions:

- Baseline run healthy. Journey runs logged out (`options.loggedOut = true`).

- **Open login.** `page.goto('/login')`. `#login-email` and `#login-password` are visible.
- **Reject.** Fill the seed email and a wrong password, click button `로그인` (exact). Text
  `이메일 또는 비밀번호가 올바르지 않습니다` appears; URL still `/login`.
- **Succeed.** Fill the real password, click `로그인`. URL path becomes `/`; heading matching
  `Verify User님` is visible.
- **Second view.** `GET /api/auth/me` with the browser context returns the seed email.
- **Run it.** `node .agents/skills/verify-moldy/scripts/drive.mjs auth-login`.

## Gotchas

- `RATE_LIMIT_ENABLED=false` in the verify stack; repeated wrong passwords in a real
  deployment trigger lockout (`users` lockout columns) — do not port this journey there.
- The success heading includes the display name; the seed user's is `Verify User`.
