#!/usr/bin/env bash
# Загрузка сайта в репозиторий GitHub одной командой — вместе со скрытыми файлами
# (.github/workflows, .nojekyll, .gitignore), которые веб-интерфейс GitHub пропускает.
#
# Положите этот файл в распакованную папку сайта (рядом с index.html) и запустите:
#   bash загрузить-на-github.sh
# или сразу с адресом репозитория:
#   bash загрузить-на-github.sh https://github.com/username/site.git

set -euo pipefail

green() { printf '\033[32m%s\033[0m\n' "$1"; }
yellow() { printf '\033[33m%s\033[0m\n' "$1"; }
red() { printf '\033[31m%s\033[0m\n' "$1"; }

cd "$(dirname "$0")"

if [ ! -f index.html ]; then
  red "Рядом со скриптом нет index.html."
  echo "Положите скрипт в распакованную папку сайта — туда, где лежит index.html."
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  red "Git не установлен."
  echo "На Mac: откройте Терминал и введите: xcode-select --install"
  echo "Либо установите GitHub Desktop — он сам принесёт git."
  exit 1
fi

REPO="${1:-}"
if [ -z "$REPO" ]; then
  printf 'Адрес репозитория (например https://github.com/username/site.git): '
  read -r REPO
fi
if [ -z "$REPO" ]; then
  red "Адрес репозитория не указан."
  exit 1
fi

# --- локальный репозиторий -------------------------------------------------
if [ ! -d .git ]; then
  git init -q -b main
  green "Создан локальный репозиторий (ветка main)."
else
  yellow "Локальный репозиторий уже есть — использую его."
fi

NAME="$(git config user.name || true)"
if [ -z "$NAME" ]; then
  git config user.name "site-upload"
  git config user.email "site-upload@users.noreply.github.com"
  yellow "Подпись коммита не была настроена — поставил временную."
fi

git add -A

# --- проверка, что скрытые файлы действительно в индексе -------------------
count_hidden="$(git diff --cached --name-only | grep -c '^\.' || true)"
if [ "${count_hidden:-0}" -lt 3 ]; then
  red "В коммит попало мало скрытых файлов (${count_hidden:-0})."
  echo "Ожидались минимум: .gitignore, .nojekyll, .github/workflows/*.yml"
  echo "Проверьте, что папка распакована полностью."
  exit 1
fi
green "В коммит войдёт файлов: $(git diff --cached --name-only | wc -l | tr -d ' '), из них скрытых: ${count_hidden}"

git commit -q -m "Сайт, инструменты и очередь постов" || yellow "Новых изменений нет — коммит не нужен."
git branch -M main

# --- удалённый репозиторий -------------------------------------------------
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REPO"
else
  git remote add origin "$REPO"
fi
green "Репозиторий: $REPO"

echo ""
echo "Отправляю. Если попросит логин — введите имя пользователя GitHub,"
echo "а в поле пароля вставьте Personal Access Token (GitHub → Settings → Developer settings"
echo "→ Personal access tokens → Tokens (classic) → Generate new token, права repo)."
echo ""

if git push -u origin main; then
  echo ""
  green "Готово. Файлы в репозитории."
  echo ""
  echo "Дальше:"
  echo "  1. Settings → Secrets and variables → Actions → New repository secret"
  echo "     Имя: TG_BOT_TOKEN, значение — токен бота-публикатора."
  echo "  2. Вкладка Actions → «Опубликовать пост в Telegram» → Run workflow."
  echo "     Это публикует первые пять постов в канал."
  echo "  3. Settings → Pages → Deploy from a branch → main / root."
else
  echo ""
  red "Push не прошёл."
  echo "Если в репозитории уже есть файлы (например, README от GitHub):"
  echo "  git pull --rebase origin main"
  echo "  git push -u origin main"
  echo ""
  echo "Если просит пароль — нужен не пароль от GitHub, а Personal Access Token (см. выше)."
  exit 1
fi
