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
спрашивает personal access token с правом `repo` один раз за запуск.
Проверить без публикации: `release_github.cmd -DryRun`.

Вручную через веб-интерфейс:

1. Страница репозитория → **Releases** (правая колонка) → **Draft a new release**.
2. **Choose a tag** → выбрать существующий тег `v<версия>` (например `v0.7.3.1`).
3. Заголовок: `Voice2Text <версия>`.
4. Описание: скопировать раздел текущей версии из `CHANGELOG.md`.
5. **Attach binaries** → `dist-installer\Voice2Text-<версия>-installer.zip`
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
