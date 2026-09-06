# -*- coding: utf-8 -*-
"""
Парсер полного архива Telegram-канала @dvorzhiki в статический index.html
для размещения на GitHub Pages.

Особенности:
  * Только стандартная библиотека Python (urllib, re, html, json, time).
  * Пагинация через параметр ?before=<id> для сбора ПОЛНОГО архива.
  * Реальные даты публикаций (ISO + человекочитаемая русская дата).
  * Сортировка постов по ID по убыванию (новые сверху).
  * Исключение служебных постов Telegram (смена аватарки, названия и т.п.).
  * Идемпотентность: при неизменном контенте файл не перезаписывается
    (важно для GitHub Actions, чтобы не создавать лишних коммитов).
"""

import urllib.request
import re
import html
import json
import time

# --- Конфигурация -----------------------------------------------------------

CHANNEL = "dvorzhiki"                 # имя канала (без @)
BASE_URL = f"https://t.me/s/{CHANNEL}"  # web-версия канала для парсинга
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
OUTPUT_FILE = "index.html"            # файл результата

# --- Вспомогательные функции ------------------------------------------------

def fetch_page(before_id=None):
    """
    Загружает HTML страницы канала.
    При наличии before_id добавляет параметр ?before=<id> для листания истории.
    Возвращает текст HTML или None при ошибке сети.
    """
    url = BASE_URL
    if before_id is not None:
        url = f"{BASE_URL}?before={before_id}"

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"  [ошибка сети] {exc}")
        return None


def split_message_blocks(page_html):
    """
    Разбивает HTML страницы на отдельные блоки сообщений.
    Возвращает список строк — каждый элемент это HTML одного поста.
    """
    # Блок сообщения начинается с class="tgme_widget_message_wrap
    parts = page_html.split('class="tgme_widget_message_wrap')
    # Первый элемент — это всё, что до первого сообщения (шапка страницы)
    return parts[1:]


def extract_post_id(block):
    """
    Извлекает числовой ID поста из ссылки вида t.me/dvorzhiki/<id>.
    Возвращает int или None.
    """
    m = re.search(r'href="https://t\.me/{}/\d+"'.format(CHANNEL), block)
    if not m:
        # Запасной вариант: ищем любой href с числом в конце
        m = re.search(r'href="[^"]*t\.me/{}/\d+"'.format(CHANNEL), block)
    if not m:
        return None
    # Из найденной ссылки достаём число
    id_match = re.search(r'/(\d+)"', m.group(0))
    return int(id_match.group(1)) if id_match else None


def extract_post_link(block):
    """
    Извлекает полную ссылку на пост.
    """
    m = re.search(r'href="(https://t\.me/{}/\d+)"'.format(CHANNEL), block)
    if m:
        return m.group(1)
    return f"https://t.me/{CHANNEL}"


def extract_date(block):
    """
    Извлекает дату публикации из <time datetime="...">.
    Возвращает кортеж (date_iso, date_label) или (None, None).
    """
    m = re.search(r'<time[^>]*datetime="([^"]+)"', block)
    if not m:
        return None, None
    date_iso = m.group(1).strip()
    date_label = format_russian_date(date_iso)
    return date_iso, date_label


def format_russian_date(date_iso):
    """
    Преобразует ISO-дату в человекочитаемую русскую дату.
    Принимает как "2025-08-31", так и полный формат с временем
    "2025-08-31T14:17:24+00:00" (берётся только дата).
    Пример: "2025-08-31" -> "31 авг 2025".
    """
    if not date_iso:
        return date_iso

    # Обрезаем всё, что идёт после даты (время и часовой пояс)
    date_part = date_iso.split("T")[0].strip()

    try:
        year, month, day = date_part.split("-")[:3]
        month = int(month)
        day = int(day)
    except (ValueError, AttributeError):
        return date_iso

    months_ru = [
        "янв", "фев", "мар", "апр", "мая", "июн",
        "июл", "авг", "сен", "окт", "ноя", "дек",
    ]
    if 1 <= month <= 12:
        return f"{day} {months_ru[month - 1]} {year}"
    return date_iso


def extract_text_html(block):
    """
    Извлекает HTML текста поста из блока tgme_widget_message_text.
    Аккуратно превращает <br> в <p> и убирает лишние вложенные обёртки.
    Возвращает строку HTML или пустую строку.
    """
    # Ищем блок текста сообщения
    m = re.search(
        r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
        block, re.DOTALL,
    )
    if not m:
        return ""
    raw = m.group(1)

    # Убираем вложенные теги-обёртки, которые Telegram иногда дублирует
    raw = re.sub(r'<div class="tgme_widget_message_text[^"]*"[^>]*>', "", raw)
    raw = raw.replace("</div>", "")

    # Нормализуем переносы строк: <br>, <br/>, <br />
    raw = re.sub(r"<br\s*/?>", "\n", raw)

    # Разбиваем на абзацы по переносам строк
    lines = [ln.strip() for ln in raw.split("\n")]
    paragraphs = [ln for ln in lines if ln]

    if not paragraphs:
        return ""

    # Собираем абзацы в <p>...</p>
    out = []
    for para in paragraphs:
        out.append(f"<p>{para}</p>")
    return "".join(out)


def clean_text_for_search(text_html):
    """
    Очищает HTML-текст поста до простого текста для поиска (data-text).
    """
    if not text_html:
        return ""
    # Убираем все теги
    text = re.sub(r"<[^>]+>", "", text_html)
    # Декодируем HTML-сущности
    text = html.unescape(text)
    # Схлопываем пробелы и переносы
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_photo_url(block):
    """
    Извлекает URL первого медиа (фото/видео) поста.
    Ищет в блоке tgme_widget_message_photo или background-image.
    Возвращает URL или None. Пропускает эмодзи-спрайты telegram.org.
    """
    # Сначала ищем блок фото с реальным CDN-URL (cdn*.telesco.pe или cdn*.telegram-cdn.org)
    photo_block = re.search(
        r'<a class="tgme_widget_message_photo_wrap[^"]*"[^>]*style="background-image:url\(\'([^\']+)\'\)"',
        block,
    )
    if photo_block:
        url = photo_block.group(1)
        if "telegram.org/img/emoji" not in url:
            return url

    # Запасной вариант: любой background-image, кроме эмодзи
    bg = re.search(r"background-image:url\('([^']+)'\)", block)
    if bg:
        url = bg.group(1)
        if "telegram.org/img/emoji" not in url:
            return url

    # Ищем видео-обложку
    video = re.search(
        r'<video[^>]*poster="([^"]+)"', block,
    )
    if video:
        url = video.group(1)
        if "telegram.org/img/emoji" not in url:
            return url

    return None


def is_service_post(text):
    """
    Определяет, является ли пост служебным (смена аватарки, названия канала
    и т.п.), чтобы исключить его из архива. Принимает очищенный текст поста.
    """
    if not text:
        return False
    # Маркеры служебных изменений, которые Telegram добавляет в такие посты
    markers = [
        "channel created",
        "channel photo updated",
        "channel name changed",
        "channel info changed",
        "channel video updated",
        "channel removed photo",
        "channel pinned message",
        "channel history was cleared",
        "канал создан",
        "фото канала обновлено",
        "название канала изменено",
        "информация о канале изменена",
    ]
    low = text.lower().strip()
    for marker in markers:
        if low == marker or low.startswith(marker + " "):
            return True
    return False


def parse_posts(page_html):
    """
    Разбирает HTML страницы на список словарей постов.
    Возвращает список постов (может быть пустым).
    """
    posts = []
    for block in split_message_blocks(page_html):
        post_id = extract_post_id(block)
        if post_id is None:
            continue

        link = extract_post_link(block)
        date_iso, date_label = extract_date(block)
        text_html = extract_text_html(block)
        search_text = clean_text_for_search(text_html)
        photo_url = extract_photo_url(block)

        # Пропускаем посты без текста и без медиа (служебные)
        if not text_html and not photo_url:
            continue

        # Пропускаем служебные посты Telegram (смена аватарки, названия и т.п.)
        if is_service_post(search_text):
            continue

        posts.append({
            "id": post_id,
            "link": link,
            "date_iso": date_iso,
            "date_label": date_label,
            "text_html": text_html,
            "search_text": search_text,
            "photo_url": photo_url,
        })
    return posts


def collect_all_posts():
    """
    Собирает полный архив постов с пагинацией через ?before=<id>.
    Возвращает список всех постов.
    """
    all_posts = []
    seen_ids = set()
    before_id = None
    page_num = 0
    empty_pages = 0  # счётчик пустых страниц подряд

    print(f"Начинаю сбор архива канала @{CHANNEL}...")

    while True:
        page_num += 1
        print(f"  Загрузка страницы {page_num} (before={before_id})...")
        page_html = fetch_page(before_id)

        if page_html is None:
            # Ошибка сети — пробуем ещё раз через паузу
            time.sleep(3)
            page_html = fetch_page(before_id)
            if page_html is None:
                print("  Не удалось загрузить страницу. Прерываю пагинацию.")
                break

        posts = parse_posts(page_html)

        if not posts:
            empty_pages += 1
            print(f"  Пустая страница (пустых подряд: {empty_pages}).")
            # Защита от бесконечного цикла: 2 пустые страницы подряд — стоп
            if empty_pages >= 2:
                break
            # Если пустая страница, но постов ещё не собрали — стоп
            if not all_posts:
                break
            # Иначе пробуем ещё раз (возможно временный сбой)
            before_id = None if before_id is None else before_id
            time.sleep(2)
            continue

        empty_pages = 0

        # Добавляем только новые посты (защита от повторяющихся ID)
        new_posts = []
        for post in posts:
            if post["id"] not in seen_ids:
                seen_ids.add(post["id"])
                new_posts.append(post)

        if new_posts:
            all_posts.extend(new_posts)
            print(f"    +{len(new_posts)} постов (всего: {len(all_posts)})")
        else:
            # Все ID уже встречались — достигли начала архива
            print("  Повторяющиеся ID — достигнут конец архива.")
            break

        # Минимальный ID на текущей странице — точка отсчёта для следующей
        min_id = min(p["id"] for p in posts)
        if before_id is not None and min_id >= before_id:
            # ID не уменьшились — пагинация не продвигается, стоп
            print("  Пагинация не продвигается (ID не уменьшились). Стоп.")
            break

        before_id = min_id

        # Пауза 1-2 секунды между запросами, чтобы не получить блокировку
        time.sleep(1.5)

    print(f"Сбор завершён. Всего собрано постов: {len(all_posts)}")
    return all_posts


def sort_posts(posts):
    """
    Сортирует посты по ID по убыванию (новые сверху).
    """
    return sorted(posts, key=lambda p: p["id"], reverse=True)


# --- Генерация HTML ---------------------------------------------------------

# Сколько первых абзацев показывать в превью (остальное под «Читать дальше»)
PREVIEW_PARAGRAPHS = 2


def split_paragraphs(text_html):
    """
    Разбивает HTML-текст поста на отдельные абзацы <p>...</p>.
    Возвращает список строк (каждый элемент — один абзац целиком).
    """
    if not text_html:
        return []
    # Ищем все абзацы <p>...</p> (с учётом возможных атрибутов)
    return re.findall(r"<p[^>]*>.*?</p>", text_html, re.DOTALL)


def build_post_card(post):
    """
    Строит HTML-разметку одной карточки поста.
    Показывает фото и первые несколько абзацев, остальное скрыто
    под кнопкой «Читать дальше».
    """
    post_id = post["id"]
    link = post["link"]
    date_iso = post.get("date_iso") or ""
    date_label = post.get("date_label") or "Архив"
    text_html = post.get("text_html") or ""
    search_text = post.get("search_text") or ""
    photo_url = post.get("photo_url")

    # Экранируем текст для атрибута data-text (чтобы не ломать HTML)
    data_text = html.escape(search_text, quote=True)

    # Блок медиа, если есть реальное фото/видео
    photo_html = ""
    if photo_url:
        photo_html = (
            f'<div class="post-photo">'
            f'<img src="{photo_url}" alt="Фото из публикации" loading="lazy">'
            f'</div>'
        )

    # Дата с атрибутом datetime
    datetime_attr = f' datetime="{date_iso}"' if date_iso else ""

    # --- Разбиваем текст на превью и скрытую часть ---
    paragraphs = split_paragraphs(text_html)

    if len(paragraphs) <= PREVIEW_PARAGRAPHS:
        # Короткий пост — показываем целиком, без кнопки
        preview_html = "".join(paragraphs)
        more_html = ""
        toggle_btn = ""
    else:
        preview_html = "".join(paragraphs[:PREVIEW_PARAGRAPHS])
        more_html = "".join(paragraphs[PREVIEW_PARAGRAPHS:])
        toggle_btn = (
            f'<button class="read-more-btn" onclick="toggleReadMore({post_id})" '
            f'id="readMoreBtn-{post_id}">Читать дальше &darr;</button>'
        )

    # Собираем содержимое .post-content: превью + (если есть) скрытая часть + кнопка
    content_parts = [preview_html]
    if more_html:
        content_parts.append(
            f'<div class="post-more" id="postMore-{post_id}" style="display:none;">'
            f'{more_html}</div>'
        )
    if toggle_btn:
        content_parts.append(toggle_btn)
    content_html = "\n        ".join(content_parts)

    return f'''<article class="post-card" data-post-id="{post_id}" data-text="{data_text}">
    <div class="post-meta">
        <span class="post-date"{datetime_attr}>{date_label}</span>
        <div class="post-meta-right">
            <a href="{link}" target="_blank" class="post-link">Оригинал в Telegram &nearr;</a>
        </div>
    </div>
    {photo_html}
    <div class="post-content">
        {content_html}
    </div>
</article>'''


def generate_html(posts):
    """
    Генерирует полный HTML-документ со всеми постами.
    """
    total = len(posts)
    posts_html = "\n".join(build_post_card(p) for p in posts)

    return f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Блог в Телеграм — Архив публикаций</title>
    <link rel="icon" type="image/png" href="images/favicon.png">
    <link rel="shortcut icon" type="image/png" href="images/favicon.png">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #ffffff;
            --card-bg: #f8fafc;
            --card-border: #e2e8f0;
            --accent: #ca8a04;
            --accent-hover: #a16207;
            --text-main: #0f172a;
            --text-muted: #475569;
            --link: #0284c7;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: 'Inter', system-ui, -apple-system, sans-serif; background-color: var(--bg); color: var(--text-main); line-height: 1.6; }}
        .top-title-sec {{ text-align: center; padding: 48px 20px 24px; }}
        .top-title-sec h1 {{ font-size: 2.2rem; font-weight: 800; letter-spacing: 0.5px; text-transform: uppercase; color: var(--text-main); }}
        .top-title-sec p {{ font-size: 1.05rem; color: var(--text-muted); margin-top: 8px; }}
        .container {{ max-width: 760px; margin: 0 auto 60px; padding: 0 20px; position: relative; }}
        .controls {{ background: var(--card-bg); padding: 16px 20px; border-radius: 16px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06); margin-bottom: 30px; border: 1px solid var(--card-border); }}
        .controls-top {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }}
        .post-count {{ font-size: 0.9rem; color: var(--text-muted); font-weight: 500; }}
        .post-count b {{ color: var(--accent); }}
        .search-input {{ width: 100%; padding: 10px 16px; border: 1px solid var(--card-border); border-radius: 8px; font-family: inherit; font-size: 0.95rem; outline: none; background: var(--bg); }}
        .search-input:focus {{ border-color: var(--accent); }}
        .posts-list {{ display: flex; flex-direction: column; gap: 24px; }}
        .post-card {{ background: var(--card-bg); border-radius: 16px; padding: 28px 32px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06); border: 1px solid var(--card-border); overflow: hidden; }}
        .post-meta {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; font-size: 0.85rem; color: var(--text-muted); border-bottom: 1px solid var(--card-border); padding-bottom: 12px; gap: 10px; }}
        .post-meta-right {{ display: flex; align-items: center; gap: 12px; }}
        .post-date {{ font-weight: 500; background: rgba(202, 138, 4, 0.12); color: var(--accent); padding: 4px 10px; border-radius: 6px; white-space: nowrap; }}
        .post-link {{ color: var(--link); text-decoration: none; font-weight: 500; }}
        .post-link:hover {{ text-decoration: underline; }}
        .post-photo {{ margin: -28px -32px 20px -32px; background: #f1f5f9; max-height: 480px; display: flex; align-items: center; justify-content: center; overflow: hidden; }}
        .post-photo img {{ width: 100%; height: auto; object-fit: cover; display: block; }}
        .post-content {{ font-size: 1.05rem; line-height: 1.75; color: var(--text-main); word-wrap: break-word; }}
        .post-content p {{ margin-bottom: 1em; }}
        .post-content p:last-child {{ margin-bottom: 0; }}
        .post-content a {{ color: var(--link); }}
        .post-content blockquote {{ border-left: 3px solid var(--accent); margin: 1em 0; padding: 0.5em 1em; background: rgba(202, 138, 4, 0.08); border-radius: 0 6px 6px 0; }}
        .read-more-btn {{ display: block; margin: 16px 0 0; background: transparent; color: var(--accent); border: 1px solid var(--accent); padding: 8px 18px; border-radius: 20px; font-family: inherit; font-size: 0.9rem; font-weight: 500; cursor: pointer; transition: all 0.2s; }}
        .read-more-btn:hover {{ background: var(--accent-hover); color: #fff; }}
        .post-more {{ margin-top: 1em; }}
        footer {{ text-align: center; padding: 40px 20px; color: var(--text-muted); font-size: 0.85rem; border-top: 1px solid var(--card-border); margin-top: 60px; }}
        .no-results {{ text-align: center; padding: 40px 20px; color: var(--text-muted); font-size: 1.1rem; display: none; }}
        @media (max-width: 600px) {{
            .post-card {{ padding: 20px; }}
            .post-photo {{ margin: -20px -20px 16px -20px; }}
            .post-meta {{ flex-direction: column; align-items: flex-start; }}
        }}
    </style>
</head>
<body>
    <div class="top-title-sec">
        <h1>DVORZHIK — БЛОГ</h1>
        <p>Архив публикаций из канала @dvorzhiki</p>
    </div>
    <main class="container">
        <div class="controls">
            <div class="controls-top">
                <span class="post-count">Всего публикаций: <b id="postCount">{total}</b></span>
            </div>
            <input type="text" id="searchInput" class="search-input" placeholder="Поиск по публикациям..." onkeyup="filterPosts()">
        </div>
        <div class="posts-list" id="postsContainer">
            {posts_html}
        </div>
        <div class="no-results" id="noResults">Ничего не найдено</div>
    </main>
    <footer>
        <p>&copy; 2026 Александр Дворжицкий. Опубликовано на GitHub Pages.</p>
    </footer>
    <script>
        // Разворачивание/сворачивание полного текста поста
        function toggleReadMore(id) {{
            const more = document.getElementById('postMore-' + id);
            const btn = document.getElementById('readMoreBtn-' + id);
            if (!more || !btn) return;
            if (more.style.display === 'none') {{
                more.style.display = '';
                btn.innerHTML = 'Свернуть &uarr;';
            }} else {{
                more.style.display = 'none';
                btn.innerHTML = 'Читать дальше &darr;';
            }}
        }}

        // Поиск по тексту постов
        function filterPosts() {{
            let input = document.getElementById('searchInput').value.toLowerCase();
            let posts = document.getElementsByClassName('post-card');
            let visible = 0;
            for (let i = 0; i < posts.length; i++) {{
                let text = (posts[i].getAttribute('data-text') || '').toLowerCase();
                let content = posts[i].querySelector('.post-content').textContent.toLowerCase();
                let match = text.includes(input) || content.includes(input);
                if (match) {{
                    posts[i].style.display = "";
                    visible++;
                }} else {{
                    posts[i].style.display = "none";
                }}
            }}
            document.getElementById('noResults').style.display = visible === 0 ? 'block' : 'none';
        }}
    </script>
</body>
</html>
'''


def main():
    """
    Главная функция: собирает посты, генерирует HTML и сохраняет файл.
    """
    # 1. Собираем полный архив
    all_posts = collect_all_posts()

    if not all_posts:
        print("Не удалось собрать ни одного поста. Проверьте доступ к Telegram.")
        return

    # 2. Сортируем посты по ID по убыванию (новые сверху)
    all_posts = sort_posts(all_posts)

    # 3. Генерируем HTML
    full_html = generate_html(all_posts)

    # 4. Идемпотентная запись: если файл уже содержит тот же контент — не пишем
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing = f.read()
        if existing == full_html:
            print(f"Файл {OUTPUT_FILE} не изменился — пропускаю запись (идемпотентность).")
            return
    except FileNotFoundError:
        pass  # файла ещё нет — создаём

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(full_html)

    print(f"Готово! Создан файл {OUTPUT_FILE} с {len(all_posts)} постами.")


if __name__ == "__main__":
    main()
