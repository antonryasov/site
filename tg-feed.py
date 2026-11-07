# -*- coding: utf-8 -*-
"""Живая лента последних постов из Telegram-канала.

Как работает: при сборке сайта (build.py) модуль забирает публичную страницу
`https://t.me/s/<канал>`, вытаскивает последние посты с текстом и превращает их
в карточки в типографике сайта. Внешних скриптов и виджетов на странице нет —
лента приезжает уже готовым HTML.

Обновить ленту без сборки всего сайта:
    python3 parts/tg_feed.py --patch index.html
Посмотреть, что подтягивается:
    python3 parts/tg_feed.py --print
"""

import datetime
import io
import re
import sys
import urllib.request
from html import unescape

CHANNEL = 'nu_takoe_ot_antona'
URL = 'https://t.me/s/' + CHANNEL
CHANNEL_URL = 'https://t.me/' + CHANNEL
# посты короче этого не попадают в ленту: там обычно мемы, картинки без текста и форварды
MIN_LEN = 140
START = '<!--TG_FEED_START-->'
END = '<!--TG_FEED_END-->'

MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
          'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']


# ---------------------------------------------------------------- сеть и разбор

def fetch_html(timeout=10):
    req = urllib.request.Request(URL, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; site-build/1.0)',
        'Accept-Language': 'ru,en;q=0.8',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def _clean(html_fragment):
    s = re.sub(r'<br\s*/?>', '\n', html_fragment)
    s = re.sub(r'<[^>]+>', '', s)
    s = unescape(s)
    s = s.replace('\u200b', '')
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


def parse_posts(html_text):
    posts = []
    for block in html_text.split('tgme_widget_message_wrap')[1:]:
        m = re.search(r'data-post="([^"]+)"', block)
        if not m:
            continue
        post_id = m.group(1)
        m = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        dt = None
        if m:
            try:
                dt = datetime.datetime.fromisoformat(m.group(1).replace('Z', '+00:00'))
            except ValueError:
                dt = None
        m = re.search(r'tgme_widget_message_views">([^<]*)<', block)
        views = _clean(m.group(1)) if m else ''
        m = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        text = _clean(m.group(1)) if m else ''
        if 'Please open Telegram to view this post' in text:
            text = ''
        if len(text) < MIN_LEN:
            continue
        posts.append({
            'id': post_id,
            'url': 'https://t.me/' + post_id,
            'dt': dt,
            'views': views,
            'text': text,
        })
    posts.sort(key=lambda p: int(p['id'].split('/')[-1]), reverse=True)
    return posts


def fetch_posts(limit=3, exclude_prefixes=()):
    posts = parse_posts(fetch_html())
    seen = []
    for p in posts:
        flat = ' '.join(p['text'].split())
        if any(flat.startswith(pref[:60]) for pref in exclude_prefixes):
            continue
        if any(flat[:60] == ' '.join(x['text'].split())[:60] for x in seen):
            continue
        seen.append(p)
        if len(seen) >= limit:
            break
    return seen


# ---------------------------------------------------------------- рендер

def _esc(t):
    return t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def fmt_date(dt):
    if not dt:
        return 'пост в канале'
    today = datetime.date.today()
    if dt.date() == today:
        return 'сегодня, %02d:%02d' % (dt.hour, dt.minute)
    if (today - dt.date()).days == 1:
        return 'вчера'
    return '%d %s' % (dt.day, MONTHS[dt.month - 1]) + ('' if dt.year == today.year else ' %d' % dt.year)


def fmt_views(v):
    v = (v or '').strip()
    n = re.sub(r'[^\d]', '', v)
    if not n:
        return ''
    n = int(n)
    if n >= 1000:
        return ('%.1f тыс. просмотров' % (n / 1000.0)).replace('.', ',')
    word = 'просмотр' if n % 10 == 1 and n % 100 != 11 else (
        'просмотра' if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else 'просмотров')
    return '%d %s' % (n, word)


def clip(text, limit=420):
    flat = ' '.join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    cut = cut[:cut.rfind(' ')] if ' ' in cut else cut
    return cut.rstrip(' ,.;:—-') + '…'


def render_cards(posts):
    if not posts:
        return fallback_html()
    out = []
    for p in posts:
        meta = [x for x in (fmt_date(p['dt']), fmt_views(p['views'])) if x]
        out.append(
            '<a class="tgp-live-post" href="%s" target="_blank" rel="noopener">'
            '<span class="tgp-live-meta">%s</span>'
            '<span class="tgp-live-text">%s</span>'
            '<span class="tgp-live-go">Открыть в Telegram <b>↗</b></span>'
            '</a>' % (_esc(p['url']),
                      ' · '.join(meta) or 'пост в канале',
                      _esc(clip(p['text'])))
        )
    return ''.join(out)


def fallback_html():
    return ('<div class="tgp-live-empty">'
            'Лента подтягивается автоматически при публикации сайта. '
            'Свежие посты всегда в канале: '
            '<a href="%s" target="_blank" rel="noopener">Ну такое от Антона ↗</a>'
            '</div>' % CHANNEL_URL)


# ---------------------------------------------------------------- CLI

def patch_file(path):
    src = io.open(path, encoding='utf-8').read()
    if START not in src or END not in src:
        print('нет маркеров ленты в', path)
        return 1
    try:
        cards = render_cards(fetch_posts(limit=3))
    except Exception as e:  # нет сети — оставляем как было
        print('не удалось получить ленту (%s), файл не изменён' % type(e).__name__)
        return 2
    pre, rest = src.split(START, 1)
    _, post = rest.split(END, 1)
    io.open(path, 'w', encoding='utf-8').write(pre + START + cards + END + post)
    print('лента обновлена в', path)
    return 0


if __name__ == '__main__':
    if '--patch' in sys.argv:
        target = sys.argv[sys.argv.index('--patch') + 1] if len(sys.argv) > sys.argv.index('--patch') + 1 else 'index.html'
        sys.exit(patch_file(target))
    if '--print' in sys.argv:
        for p in fetch_posts(limit=5):
            print('---', p['id'], fmt_date(p['dt']), fmt_views(p['views']))
            print(clip(p['text'], 200))
        sys.exit(0)
    print(__doc__)
