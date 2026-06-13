# decant — QUICKSTART (для тестов)

Всё лежит в корне репозитория (`web-decant\`). Команды — из PowerShell, **из этой папки**.
Везде ниже `decant` = враппер `decant.cmd` (он зовёт `.venv\Scripts\python.exe decant.py …`).

> **macOS / Linux:** то же самое, отличия только в обвязке:
> - venv: `python3 -m venv .venv`; python: `.venv/bin/python`; враппер: `./decant` (после `chmod +x decant`), не `decant.cmd`.
> - перед первым запуском один раз: `.venv/bin/python -m playwright install chromium` (на macOS дефолтный движок — встроенный Chromium, т.к. Edge обычно нет; есть Chrome — `export DECANT_BROWSER=chrome`).
> - `.profile/` и `.token` свои на каждой машине — на mac логинься заново (Сценарий C).
> - Демон, букмарклет и порядок A→B→C — идентичны.

---

## 1. Карта файлов: что есть что

### Исходники (это и редактируем)
| Файл | Роль |
|---|---|
| `decant.py` | CLI и вся логика: подкоманды `get` / `login` / `serve` / `bookmarklet` / `current`. Здесь же экстракция (`to_markdown`, `tidy`), сессия (`fetch`), вывод (`build_frontmatter`, `slugify`). |
| `serve.py` | Локальный демон (HTTP на 127.0.0.1:8765): приём от букмарклета, токен-гейт, сохранение, `/current`. Переиспользует функции из `decant.py`. |
| `decant.cmd` | Враппер, чтобы не писать длинный путь до venv. |
| `requirements.txt` | Зависимости (playwright, trafilatura, markdownify). |
| `README.md` / `QUICKSTART.md` | Описание и этот гайд. |

### Генерируется автоматически (состояние, можно удалять для чистоты теста)
| Путь | Когда создаётся | Зачем |
|---|---|---|
| `.venv\` | при установке | изолированный Python 3.14 + пакеты |
| `.profile\` | при первом `login`/`get`/re-fetch | **браузерная сессия** (cookies, 2FA). Удалишь — слетит логин. |
| `.token` | при первом `serve`/`bookmarklet` | секрет демона (он же вшит в букмарклет). Удалишь — старый букмарклет станет невалидным, нужно сгенерировать заново. |
| `captures\` | при каждом захвате | сюда падают `<slug>.md`. |
| `.last_capture.md` | при каждом захвате | «последняя страница» — её читает `decant current`. |
| `__pycache__\`, `_test_out\` | побочное | кэш Python и папка старого смоук-теста; не важны. |

> Чтобы «начать тест с нуля»: удали `captures\`, `.last_capture.md`. Чтобы сбросить ещё и логин — `.profile\`. Чтобы сбросить токен — `.token`.

---

## 2. Предусловие (уже сделано один раз)
```pwsh
# python -m venv .venv ; .venv\Scripts\python -m pip install -r requirements.txt
```
Проверка, что живо:
```pwsh
.\decant.cmd get "https://example.com"
```
Ожидание: в stdout markdown с frontmatter. Файл при этом НЕ пишется (без `--out`).

---

## 3. Сценарий A — сдираем одну страницу (без демона)
**Зачем:** проверить чистоту экстракции на конкретном URL.
```pwsh
.\decant.cmd get "https://en.wikipedia.org/wiki/Web_scraping" --out .\captures
```
**Что трогается:** создаётся `.profile\` (Edge headless рендерит), пишется `captures\<slug>.md`, markdown дублируется в stdout.
**Как смотреть результат:** открой свежий файл в `captures\`.
Полезные флаги: `--no-headless` (увидеть окно), `--raw` (отдать сырой HTML), `--engine firefox`.

---

## 4. Сценарий B — демон + букмарклет (поток «при серфинге»)
**Зачем:** ловить текущую страницу одним кликом, без консоли; потом читать её как «текущую».

**Шаг 1. Поднять демон** (займёт терминал — открой второй или сверни):
```pwsh
.\decant.cmd serve
```
Выведет адрес, путь `captures\` и **готовую строку букмарклета**. Трогает: создаёт/читает `.token`.

**Шаг 2. Поставить букмарклет в Firefox** (один раз):
- получить строку: `.\decant.cmd bookmarklet`
- Firefox → правый клик по панели закладок → «Добавить закладку» → в поле **URL** вставить строку целиком (с `javascript:`), имя «Rip».

**Шаг 3. Поймать страницу:** открой обычную страницу → жми «Rip».
- Ожидание: всплывает `decant ✓ <title>`.
- **Что трогается:** демон пишет `captures\<slug>.md` и `.last_capture.md`.
- На Confluence (жёсткий CSP) вместо alert откроется вкладка-подтверждение демона — это нормальный fallback (демон до-качивает сам, см. Сценарий C).

**Шаг 4. Прочитать «текущую» страницу:**
```pwsh
.\decant.cmd current
```
Отдаёт `.last_capture.md` в stdout — это и есть то, что я читаю в чате, когда ты говоришь «ответь по этой странице».

**Проверка демона без браузера** (curl-стиль):
```pwsh
$t = Get-Content .\.token
Invoke-WebRequest "http://127.0.0.1:8765/health" -UseBasicParsing       # -> ok
Invoke-WebRequest "http://127.0.0.1:8765/current?t=$t" -UseBasicParsing  # -> markdown
```

**Остановить демон:** Ctrl+C в его терминале.

---

## 5. Сценарий C — за авторизацией (Confluence и пр.)
**Зачем:** доступ к страницам за SSO/2FA через сессию демона.

**Шаг 1. Логин один раз** (откроется реальное окно):
```pwsh
.\decant.cmd login https://твой-confluence.example.com
```
Залогинься (2FA включительно) → Enter в терминале. **Трогает:** наполняет `.profile\` cookies.

**Шаг 2. Дальше всё как в A/B**, сессия переиспользуется headless:
```pwsh
.\decant.cmd get "https://твой-confluence.../display/SPACE/Page" --out .\captures
```
Если сессия протухла, `get` напишет «hit a login wall» → повтори Шаг 1.

---

## 6. Шпаргалка
- **Порт:** `8765` (env `DECANT_PORT`).
- **Папка захватов:** `captures\` (env `DECANT_OUT`).
- **Профиль сессии:** `.profile\` (env `DECANT_PROFILE`), движок — Edge (`--channel msedge`); Firefox-движок: `--engine firefox`.
- **Токен:** `.token` — один на машину, вшит в букмарклет.
- **Порядок при первом тесте:** A (одна страница) → B (демон+букмарклет) → C (логин и реальный Confluence).
