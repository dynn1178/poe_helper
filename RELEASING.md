# 배포 절차

새 버전을 사용자에게 전달하는 전체 과정입니다.

프로그램·업데이터·다운로드 페이지 세 곳이 **같은 이름과 버전**을 전제로 맞물려 있어서,
한 군데만 어긋나도 조용히 깨집니다. 그래서 그 부분은 `release.py`가 먼저 검사합니다.

---

## 한 번만 해두면 되는 준비

```bash
# 1) 저장소 연결 (아직 git 저장소가 아니라면)
git init
git branch -M main
git remote add origin https://github.com/dynn1178/poe_helper.git

# 2) GitHub CLI 로그인 — 릴리즈 업로드에 사용합니다
winget install GitHub.cli     # 또는 https://cli.github.com
gh auth login

# 3) 빌드 환경
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**Vercel**: 저장소를 연결하면 끝입니다. 빌드 명령은 없고,
루트의 `vercel.json`이 `web/` 폴더를 그대로 배포합니다.
이후 `main`에 푸시할 때마다 자동으로 다시 배포됩니다.

---

## 배포하기

```bash
.venv\Scripts\activate
py release.py 1.1.0
```

`release.py`가 순서대로 처리합니다.

1. **사전 검사** — `git`/`gh` 존재, `build.spec`의 산출물 이름과
   `version.ASSET_NAME` 일치, 다운로드 페이지 링크가 현재 저장소를 가리키는지,
   작업 트리가 깨끗한지, 태그가 비어 있는지, 올리려는 버전이 실제로 더 높은지
2. `poehelper/version.py`의 `__version__`을 새 버전으로 수정
3. PyInstaller 빌드 → `dist/KuanPoeHelper.exe`
4. **여기서 멈추고 직접 실행해 확인하도록 안내** (아래 체크리스트)
5. 커밋 → `v1.1.0` 태그 → 푸시
6. `gh release create`로 릴리즈 생성 + exe 업로드
   (릴리즈 노트는 지난 태그 이후 커밋 메시지로 자동 작성)

먼저 확인만 하고 싶으면:

```bash
py release.py --check          # 검사만
py release.py 1.1.0 --dry-run  # 빌드까지 하고 푸시/업로드는 안 함
```

---

## 4단계에서 확인할 것

빌드된 `dist\KuanPoeHelper.exe`를 **깨끗한 폴더에 복사해서** 실행하세요.
개발 폴더에서 실행하면 기존 `config.json`을 읽기 때문에 첫 실행 상태를 확인할 수 없습니다.

- [ ] 창이 뜨고 제목이 `Kuan POE Helper v1.1.0`
- [ ] 관리자 권한(UAC) 요청이 뜬다
- [ ] 탭이 전부 열린다 (특히 게임 단축키, 좌표 캘리브레이션)
- [ ] 단축키가 게임에서 동작한다
- [ ] 기타 탭 → 자동 업데이트 → **지금 확인** → "최신 버전입니다"
- [ ] `config.json`, `poehelper.log`가 exe 옆에 생성된다

업데이트 경로 자체를 확인하려면, 이전 버전 exe를 따로 두고 새 릴리즈를 올린 뒤
그 이전 버전을 실행해 보세요 — 업데이트 창이 뜨고, 받고, 스스로 재시작해야 합니다.

---

## 반드시 지켜야 하는 것

**릴리즈 자산 이름은 항상 `KuanPoeHelper.exe`.**
다운로드 페이지는 GitHub의 영구 링크
`releases/latest/download/KuanPoeHelper.exe`를 쓰고, 업데이터도 이 이름으로 자산을 찾습니다.
이름을 바꾸면 페이지 버튼과 자동 업데이트가 **동시에** 깨집니다.
(`release.py`가 `build.spec`와 대조해 막아줍니다.)

**태그는 `v` + `__version__`.**
업데이터는 릴리즈 태그를 exe에 박힌 버전과 비교합니다. `version.py`를 올리지 않고
태그만 올리면 아무에게도 업데이트가 안내되지 않습니다. (`release.py`가 대신 수정합니다.)

**사전 릴리즈는 `-rc1` 같은 접미사로.**
`1.2.0-rc1`은 `1.2.0`보다 낮게 취급되므로 정식판 사용자를 끌어가지 않습니다.

---

## 버전 번호 기준

| 올리는 자리 | 언제 |
|---|---|
| 패치 `1.1.**1**` | 버그 수정만 |
| 마이너 `1.**2**.0` | 기능 추가 |
| 메이저 `**2**.0.0` | 설정 형식이 바뀌어 이전 버전으로 되돌릴 수 없을 때 |

설정 파일은 알 수 없는 항목을 보존하고 기본값을 병합하므로,
버전을 올린다고 사용자 설정이 사라지지는 않습니다.
업데이트 설치 직전에 `config.json` 사본도 따로 남깁니다.

---

## 다운로드 페이지만 고칠 때

`web/` 안의 내용은 릴리즈와 무관합니다. 푸시하면 Vercel이 바로 반영합니다.

```bash
git add web/ && git commit -m "docs: 소개 페이지 수정" && git push
```

스크린샷 추가는 `web/shots/README.md`를 참고하세요.

---

## 문제가 생기면

| 증상 | 원인과 조치 |
|---|---|
| 업데이트 알림이 안 뜬다 | 태그가 `__version__`보다 높은지 확인. `py -c "from poehelper import version as v; print(v.is_newer('1.1.0'))"` |
| "릴리즈에 파일이 첨부되어 있지 않습니다" | 자산 이름이 `KuanPoeHelper.exe`가 아님. 릴리즈에서 이름을 고쳐 다시 업로드 |
| 다운로드 버튼이 404 | 릴리즈가 draft 상태이거나 자산 이름 불일치. `gh release list`로 확인 |
| 업데이트 후 프로그램이 안 켜짐 | 교체 스크립트가 실패한 경우 받은 파일을 대신 실행합니다. `%TEMP%\KuanPoeHelper-<버전>.exe` 확인 |
| 백신이 지운다 | 키보드 후킹 프로그램의 흔한 오탐. 코드 서명 인증서가 없으면 완전히 피하기 어렵습니다 |

잘못 올린 릴리즈 되돌리기:

```bash
gh release delete v1.1.0 --yes
git push --delete origin v1.1.0
git tag -d v1.1.0
```

이미 받아간 사용자에게는 되돌아가지 않으므로, 더 높은 번호로 새 릴리즈를 내는 편이 안전합니다.
