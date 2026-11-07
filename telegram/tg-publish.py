#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Публикация постов в Telegram-канал из очереди.

Откуда берётся токен (по порядку):
  1. переменная окружения TG_BOT_TOKEN
  2. файл .env рядом со скриптом (строка TG_BOT_TOKEN=123:AA...)
  3. файл .env в корне проекта

Команды:
  python3 tg-publish.py --check          # проверить бота и права в канале
  python3 tg-publish.py --list           # что в очереди
  python3 tg-publish.py --next --dry-run # показать, что уйдёт сейчас
  python3 tg-publish.py --next           # опубликовать всё, что уже пора
  python3 tg-publish.py --id 03          # опубликовать конкретный пост
  python3 tg-publish.py --id 03 --dry-run

--next публикует все посты, у которых статус «готов» и (publish_after пустой
или уже наступил по Москве), но не больше --max за один запуск (по умолчанию 5).
Так первые пять постов уходят разом, а серия — по одному в день: у каждого
поста серии publish_after = дата + 20:00 по Москве.

Очередь — телеграм/очередь.json (рядом со скриптом или в корне проекта).
После публикации статус поста меняется на «опубликован», сохраняется message_id и дата.

Токен в репозиторий не попадает: .env лежит в .gitignore, для GitHub Actions
токен хранится в Secrets под именем TG_BOT_TOKEN.
"""

import argparse
import datetime
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_CHANNEL = '@nu_takoe_ot_antona'
QUEUE_CANDIDATES = [
    os.path.join(HERE, 'очередь.json'),
    os.path.join(HERE, 'queue.json'),
    os.path.join(HERE, 'telegram', 'очередь.json'),
    os.path.join(HERE, 'telegram', 'queue.json'),
    os.path.join(ROOT, 'telegram', 'очередь.json'),
    os.path.join(ROOT, 'telegram', 'queue.json'),
]
ENV_CANDIDATES = [
    os.path.join(HERE, '.env'),
    os.path.join(ROOT, '.env'),
    os.path.join(os.getcwd(), '.env'),
]


# ---------------------------------------------------------------- служебное

def die(msg, code=2):
    print(msg)
    sys.exit(code)


def read_token():
    tok = os.environ.get('TG_BOT_TOKEN', '').strip()
    if tok:
        return tok
    for path in ENV_CANDIDATES:
        if os.path.isfile(path):
            for line in io.open(path, encoding='utf-8'):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                m = re.match(r'^(?:export\s+)?TG_BOT_TOKEN\s*=\s*(.+)$', line)
                if m:
                    return m.group(1).strip().strip('"\'')
    return ''


def queue_path():
    for path in QUEUE_CANDIDATES:
        if os.path.isfile(path):
            return path
    return QUEUE_CANDIDATES[0]


def load_queue(path):
    if not os.path.isfile(path):
        die('Не нашёл очередь постов: %s' % path)
    data = json.load(io.open(path, encoding='utf-8'))
    if 'posts' not in data:
        die('В очереди нет поля posts: %s' % path)
    return data


def save_queue(path, data):
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2))
        f.write('\n')
    os.replace(tmp, path)


def api(token, method, **params):
    url = 'https://api.telegram.org/bot%s/%s' % (token, method)
    payload = json.dumps(params).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode('utf-8'))
        except Exception:
            return {'ok': False, 'description': 'HTTP %s' % e.code}
    except Exception as e:
        return {'ok': False, 'description': '%s: %s' % (type(e).__name__, e)}


def hint_for(err):
    e = (err or '').lower()
    if 'chat not found' in e:
        return 'Бот не добавлен в канал. Добавьте его администратором: канал → Управление → Администраторы → Добавить.'
    if 'not enough rights' in e:
        return 'Бот в канале, но без права «Публикация сообщений». Включите это право в настройках администратора.'
    if 'bot is not a member' in e:
        return 'Бот не в канале. Добавьте его администратором канала.'
    if 'unauthorized' in e:
        return 'Токен неверный или отозван. Возьмите актуальный в @BotFather → /mybots → API Token.'
    return ''


MSK = datetime.timezone(datetime.timedelta(hours=3))


def now_msk():
    return datetime.datetime.now(MSK)


def today_msk():
    return now_msk().date()


def is_due(post):
    if post.get('status') != 'готов':
        return False
    after = (post.get('publish_after') or '').strip()
    if not after:
        return True
    try:
        return datetime.date.fromisoformat(after[:10]) <= today_msk()
    except ValueError:
        return True


def sort_key(post):
    """Пустой publish_after — это «сразу», он идёт первым."""
    return ((post.get('publish_after') or '').strip(), post.get('id') or '')


def due_posts(queue, limit=5):
    posts = [p for p in queue['posts'] if is_due(p)]
    posts.sort(key=sort_key)
    return posts[:limit] if limit else posts


# ---------------------------------------------------------------- команды

def cmd_check(token, channel):
    me = api(token, 'getMe')
    if not me.get('ok'):
        print('Бот недоступен: %s' % me.get('description'))
        print(hint_for(me.get('description')))
        return 2
    bot = me['result']
    print('Бот: @%s («%s», id %s)' % (bot.get('username'), bot.get('first_name'), bot.get('id')))

    chat = api(token, 'getChat', chat_id=channel)
    if not chat.get('ok'):
        print('Канал: недоступен — %s' % chat.get('description'))
        print(hint_for(chat.get('description')))
        return 3
    c = chat['result']
    print('Канал: %s (%s, id %s)' % (c.get('title'), c.get('username'), c.get('id')))

    member = api(token, 'getChatMember', chat_id=channel, user_id=bot['id'])
    if not member.get('ok'):
        print('Статус бота в канале: неизвестен — %s' % member.get('description'))
        print('Скорее всего бот не добавлен в канал. Канал → Управление → Администраторы → Добавить → @%s'
              % bot.get('username'))
        return 4
    m = member['result']
    status = m.get('status')
    can_post = bool(m.get('can_post_messages'))
    print('Статус бота в канале: %s%s' % (status, ', право публикации: ' + ('есть' if can_post else 'нет')))
    if status in ('administrator', 'creator') and can_post:
        print('\nВсё готово: можно публиковать — python3 tg-publish.py --next')
        return 0
    print('\nПубликовать пока нельзя.')
    print('Откройте канал → Управление → Администраторы → Добавить администратора → @%s' % bot.get('username'))
    print('и включите право «Публикация сообщений». Токен при этом менять не нужно.')
    return 4


def cmd_list(path):
    q = load_queue(path)
    print('Канал: %s · постов в очереди: %d' % (q.get('channel', DEFAULT_CHANNEL), len(q['posts'])))
    for p in q['posts']:
        mark = {'готов': '·', 'опубликован': '✓', 'черновик': '…', 'снят': '×'}.get(p.get('status'), '?')
        date = (p.get('published_at') or '')[:16].replace('T', ' ') or (p.get('publish_after') or '')
        print('  %s %s  %-12s %-16s %s' % (mark, p['id'], p.get('status') or '?', date, p.get('title', '')[:52]))
    return 0


def send(token, channel, text, dry_run=False):
    if dry_run:
        print('— dry-run — отправили бы в %s:\n' % channel)
        print(text)
        return {'ok': True, 'result': {'message_id': None}}
    return api(token, 'sendMessage', chat_id=channel, text=text,
               disable_web_page_preview=True, disable_notification=False)


def publish(token, channel, path, post, dry_run=False):
    res = send(token, channel, post['text'], dry_run=dry_run)
    if not res.get('ok'):
        print('Не опубликовано: %s' % res.get('description'))
        hint = hint_for(res.get('description'))
        if hint:
            print(hint)
        return 4
    if dry_run:
        print('\n(dry-run) Статус в очереди не менялся.')
        return 0
    mid = res['result'].get('message_id')
    post['status'] = 'опубликован'
    post['message_id'] = mid
    post['published_at'] = now_msk().replace(microsecond=0).isoformat()
    q = load_queue(path)
    for i, p in enumerate(q['posts']):
        if p['id'] == post['id']:
            q['posts'][i] = post
    save_queue(path, q)
    chan = channel.lstrip('@')
    print('Опубликовано: «%s» (message_id %s)' % (post.get('title', post['id']), mid))
    if chan and mid:
        print('Ссылка: https://t.me/%s/%s' % (chan, mid))
    return 0


def main():
    ap = argparse.ArgumentParser(description='Публикация постов в Telegram-канал из очереди')
    ap.add_argument('--check', action='store_true', help='проверить бота и права в канале')
    ap.add_argument('--list', action='store_true', help='показать очередь')
    ap.add_argument('--next', action='store_true', help='опубликовать всё, что уже пора (по одному посту серии в день)')
    ap.add_argument('--max', type=int, default=5, help='сколько постов максимум за один запуск --next (по умолчанию 5)')
    ap.add_argument('--id', help='опубликовать пост с этим id')
    ap.add_argument('--dry-run', action='store_true', help='показать, что уйдёт, без отправки')
    ap.add_argument('--channel', default=None, help='канал (@username), по умолчанию из очереди')
    args = ap.parse_args()

    path = queue_path()
    if args.list:
        return cmd_list(path)

    token = read_token()
    if not token and args.dry_run:
        token = 'DRY-RUN'   # для показа очереди токен не нужен
    if not token:
        print('Нет токена бота.')
        print('Положите его в переменную окружения TG_BOT_TOKEN или в файл .env рядом со скриптом:')
        print('  TG_BOT_TOKEN=123456:AA...')
        print('Как получить токен и подключить бота к каналу — telegram/КАК-ПУБЛИКОВАТЬ.md')
        return 2

    queue = load_queue(path)
    channel = args.channel or queue.get('channel') or DEFAULT_CHANNEL

    if args.check:
        return cmd_check(token, channel)

    if args.id:
        post = next((p for p in queue['posts'] if p['id'] == args.id), None)
        if not post:
            die('В очереди нет поста с id %s' % args.id)
        return publish(token, channel, path, post, dry_run=args.dry_run)

    if args.next:
        posts = due_posts(queue, limit=args.max)
        if not posts:
            nxt = sorted([p for p in queue['posts'] if p.get('status') == 'готов'],
                         key=sort_key)
            print('Сейчас публиковать нечего: все готовые посты уже вышли или ждут своей даты.')
            if nxt:
                print('Ближайший по расписанию: id %s, %s — %s' % (
                    nxt[0]['id'], (nxt[0].get('publish_after') or '')[:16].replace('T', ' '), nxt[0].get('title')))
            return 0
        print('К публикации: %d пост(ов)' % len(posts))
        for post in posts:
            print('— id %s · %s' % (post['id'], post.get('title')))
        print('')
        code = 0
        for post in posts:
            code = publish(token, channel, path, post, dry_run=args.dry_run)
            if code != 0:
                return code
        return code

    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
