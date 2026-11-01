# Как опубликовать релиз

Короткая памятка для сопровождающего: куда кладётся установщик и что видит пользователь.

## 1. Выложить код

```bash
git status                  # рабочее дерево чистое
git push origin main --tags
```

Теги `v*` приходят вместе с `--tags`. Именно по тегу создаётся релиз.

## 2. Собрать установщик

```bat
release.cmd
```

Скрипт собирает приложение, подписывает exe, упаковывает `payload.zip`, собирает и
подписывает установщик. Результат: `dist-installer\Voice2Text-<версия>-installer.zip`.
Проверка версии внутри: файл `version.txt` в папке `dist-installer`.
В git эта папка не попадает (`.gitignore`) — архив прикладывается к релизу.

Если нужно по шагам: `build.cmd`, затем
`.venv\Scripts\python.exe installer\build_installer.py`.

## 3. Создать релиз на GitHub

Одной командой:

```bat
push.cmd              :: выложить код и теги
release_github.cmd    :: опубликовать Release с архивом установщика
```

`release_github.cmd` работает через GitHub CLI (`gh auth login`), а если `gh` нет —
берёт personal access token. Токен можно сохранить один раз, чтобы не вводить каждый раз.

### Токен: сохранить один раз

```
release_github.cmd            :: спросит токен и предложит сохранить
release_github.cmd -SaveToken :: сохранить без вопроса
release_github.cmd -Forget    :: удалить сохранённый токен
release_github.cmd -DryRun    :: ничего не отправлять, только показать версию и архив
```

Сохранённый токен лежит в `%LOCALAPPDATA%\voice2text\github.token`, зашифрован через DPAPI
для текущей учётной записи Windows: другой пользователь и другая машина его не прочитают.
В репозиторий он не попадает. Порядок поиска: параметр `-Token` → переменная
`GITHUB_TOKEN` → сохранённый файл → запрос вручную. Переменная окружения видна всем
процессам вашей учётной записи, поэтому файл с DPAPI надёжнее.

Какой токен подходит:

- **classic**: scope `repo` (для приватного репозитория) или `public_repo` (для публичного);
- **fine-grained**: Repository access → нужный репозиторий, Permissions → **Contents:
  Read and write** (без этого GitHub отвечает `404` даже на существующий репозиторий).

Если скрипт пишет `Репозиторий … не найден или токену не выдан доступ` — проверьте имя
(`-Repo owner/name`) и права токена; неверный сохранённый токен удаляется `-Forget`.

Вручную через веб-интерфейс:

1. Страница репозитория → **Releases** (правая колонка) → **Draft a new release**.
2. **Choose a tag** → выбрать существующий тег `v<версия>` (например `v0.7.3.1`).
3. Заголовок: `Voice2Text <версия>`.
4. Описание: скопировать раздел текущей версии из `CHANGELOG.md`.
5. **Attach binaries** → `dist-installer\Voice2Text-<версия>-installer.zip` и рядом
   `…-installer.zip.sha256` (приложение сверяет хеш перед установкой; `release_github.cmd`
   загружает оба файла сам)
   (файл больше 100 МБ в git не кладётся, только в Assets релиза).
6. **Publish release**.

Через GitHub CLI (если установлен `gh` и выполнен `gh auth login`):

```bash
gh release create v0.7.3.1 dist-installer/Voice2Text-0.7.3.1-installer.zip \
  --title "Voice2Text 0.7.3.1" --notes-file CHANGELOG.md
```

## 4. Проверить как пользователь

1. Открыть `https://github.com/<логин>/voice2text/releases/latest`.
2. Скачать архив из **Assets**, распаковать, запустить `setup.exe`.
3. Убедиться, что окно показывает нужную версию и горячая клавиша `Ctrl+Shift+Space` работает.

## Чего делать не нужно

- Не коммитить `dist-installer`, `dist`, `dist-final`, `dist2` — это артефакты сборки.
- Не создавать тег и релиз с разными версиями: имя архива, тег, `version.py` и `CHANGELOG.md`
  должны совпадать.
