"""
QuickReplies — быстрые ответы покупателям (плагин для FunPay Cardinal v0.1.17+)

⚡ Хранит шаблоны ответов и позволяет отправлять их покупателям в 3 касания:
выбрал чат → выбрал шаблон → подтвердил. Больше никакого ручного набора
одних и тех же фраз «товар в пути», «спасибо за покупку» и т.д.

Возможности:
 • До 30 именованных шаблонов с поддержкой переменной $username (ник покупателя).
 • Отправка любого шаблона в любой чат FunPay прямо из Telegram
   (список чатов подтягивается штатным методом `get_chats`).
 • Предпросмотр перед отправкой — защита от промаха.
 • Добавление / переименование / редактирование / удаление шаблонов через меню.
 • Шаблоны хранятся на диске — переживают рестарт Кардинала.
 • 3 готовых шаблона уже добавлены после установки.

Команды Telegram:
 • /quick — открыть меню плагина.

Установка: положить этот файл в папку plugins/ и перезапустить Кардинал.
Автор: yungspider
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import TYPE_CHECKING

from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B, CallbackQuery, Message
from telebot.apihelper import ApiTelegramException

try:
    from tg_bot import CBT, static_keyboards as skb, utils as tg_utils
except Exception:
    CBT = None
    skb = None
    tg_utils = None

if TYPE_CHECKING:
    from cardinal import Cardinal

logger = logging.getLogger("FPC.QuickReplies")

# ══════════════════════════════ МЕТАДАННЫЕ ПЛАГИНА ══════════════════════════════
NAME = "QuickReplies (Fast Answer Templates)"
VERSION = "1.0.0"
DESCRIPTION = ("Шаблоны быстрых ответов покупателям: отправка готового текста в чат FunPay "
               "в 3 касания из Telegram, до 30 шаблонов, переменная $username, "
               "предпросмотр перед отправкой. Команда: /quick")
CREDITS = "yungspider"
UUID = "c1a3ded6-4d5b-411b-adc9-15522a41dec4"
SETTINGS_PAGE = True

# ══════════════════════════════ НАСТРОЙКИ ПЛАГИНА ═══════════════════════════════
PLUGIN_DIR = os.path.join("storage", "plugins", "quick_replies")
SETTINGS_PATH = os.path.join(PLUGIN_DIR, "settings.json")

MAX_TEMPLATES = 30
NAME_LIMIT = 40
TEXT_LIMIT = 1500
PAGE_SIZE = 8          # чатов/шаблонов на одной клавиатуре

DEFAULT_TEMPLATES = [
    {"id": 1, "name": "👋 Приветствие",
     "text": "Здравствуйте, $username! Спасибо за интерес к магазину. Чем могу помочь?"},
    {"id": 2, "name": "📦 Товар в пути",
     "text": "$username, заказ принят в работу! Вышлю товар в течение 10 минут. Если задержусь — напишу вам первым."},
    {"id": 3, "name": "⭐ После покупки",
     "text": "$username, спасибо за покупку! Буду очень благодарен за отзыв. Обращайтесь снова — постоянным покупателям скидка 😉"},
]

DEFAULT_SETTINGS = {
    "enabled": True,
    "templates": DEFAULT_TEMPLATES,
    "stats": {"sent": 0},          # сколько шаблонов отправлено покупателям
}

# ══════════════════════════════ CALLBACK-КОДЫ ═══════════════════════════════════
CB_HOME = "qr:home"
CB_ADD = "qr:add"
CB_SEND = "qr:send"
CB_TPL = "qr:tpl"                    # + :<tpl_id>
CB_EDIT = "qr:edit"                  # + :<tpl_id>
CB_RENAME = "qr:rename"              # + :<tpl_id>
CB_DEL = "qr:del"                    # + :<tpl_id>
CB_DEL_YES = "qr:delyes"             # + :<tpl_id>
CB_CHAT = "qr:chat"                  # + :<chat_id>
CB_PICK = "qr:pick"                  # + :<chat_id>:<tpl_id>
CB_CONF = "qr:conf"                  # + :<chat_id>:<tpl_id>
CB_NOOP = "qr:noop"
ST_ADD_NAME = "qr:st:name"
ST_ADD_TEXT = "qr:st:text"
ST_EDIT_TEXT = "qr:st:edittext"
ST_RENAME = "qr:st:rename"

_settings_lock = threading.Lock()
_cardinal: "Cardinal | None" = None


# ══════════════════════════════ УТИЛИТЫ ═════════════════════════════════════════
def _load_settings() -> dict:
    with _settings_lock:
        data = {}
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                logger.error("QuickReplies: не удалось прочитать настройки, использую значения по умолчанию.")
                logger.debug("TRACEBACK", exc_info=True)
                data = {}
        settings = json.loads(json.dumps(DEFAULT_SETTINGS))
        if isinstance(data, dict):
            settings.update({k: v for k, v in data.items() if k in settings})
        settings["enabled"] = bool(settings["enabled"])
        if not isinstance(settings["templates"], list):
            settings["templates"] = []
        if not isinstance(settings["stats"], dict):
            settings["stats"] = {"sent": 0}
        settings["stats"].setdefault("sent", 0)
        return settings


def _save_settings(settings: dict) -> None:
    with _settings_lock:
        try:
            os.makedirs(PLUGIN_DIR, exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.error("QuickReplies: не удалось сохранить настройки.")
            logger.debug("TRACEBACK", exc_info=True)


def _normalize_templates(raw) -> list:
    """Приводит список шаблонов к валидному виду: уникальные int-id, непустые name/text."""
    result, used_ids = [], set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            tid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        name = str(item.get("name") or "").strip()[:NAME_LIMIT]
        text = str(item.get("text") or "").strip()[:TEXT_LIMIT]
        if not name or not text or tid in used_ids:
            continue
        used_ids.add(tid)
        result.append({"id": tid, "name": name, "text": text})
        if len(result) >= MAX_TEMPLATES:
            break
    return result


def _next_id(templates: list) -> int:
    return max([int(t["id"]) for t in templates], default=0) + 1


def _find_template(settings: dict, tpl_id) -> dict | None:
    try:
        tpl_id = int(tpl_id)
    except (TypeError, ValueError):
        return None
    for t in settings.get("templates") or []:
        if int(t["id"]) == tpl_id:
            return t
    return None


def _add_template(settings: dict, name: str, text: str):
    """Добавляет шаблон. Возвращает id или None (достигнут лимит / пустые данные)."""
    name = str(name or "").strip()[:NAME_LIMIT]
    text = str(text or "").strip()[:TEXT_LIMIT]
    if not name or not text:
        return None
    templates = _normalize_templates(settings.get("templates"))
    if len(templates) >= MAX_TEMPLATES:
        return None
    new_id = _next_id(templates)
    templates.append({"id": new_id, "name": name, "text": text})
    settings["templates"] = templates
    _save_settings(settings)
    return new_id


def _fill(template_text: str, username: str) -> str:
    """Подставляет переменные шаблона. Пока доступна $username — ник покупателя."""
    return str(template_text or "").replace("$username", str(username or "друг"))


def _short(text: str, limit: int = 40) -> str:
    text = str(text or "").replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _escape(text) -> str:
    if tg_utils:
        return tg_utils.escape(str(text))
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _plugin_enabled(crd: "Cardinal") -> bool:
    try:
        return crd.plugins[UUID].enabled
    except Exception:
        return False


def send_template(crd: "Cardinal", chat_id, tpl_id, username: str = "") -> bool:
    """
    Отправляет шаблон tpl_id в чат chat_id через FunPay.
    Возвращает True при успехе. Учитывает статистику.
    """
    settings = _load_settings()
    if not settings["enabled"]:
        logger.info("QuickReplies: плагин выключен — отправка отменена.")
        return False
    tpl = _find_template(settings, tpl_id)
    if tpl is None:
        logger.warning(f"QuickReplies: шаблон #{tpl_id} не найден.")
        return False
    text = _fill(tpl["text"], username)
    try:
        if str(chat_id).isdigit():
            chat_id = int(chat_id)
        crd.account.send_message(chat_id, text)
    except Exception:
        logger.error(f"QuickReplies: не удалось отправить шаблон «{tpl['name']}» в чат {chat_id}.")
        logger.debug("TRACEBACK", exc_info=True)
        return False
    settings["stats"]["sent"] = int(settings["stats"].get("sent", 0)) + 1
    _save_settings(settings)
    logger.info(f"QuickReplies: шаблон «{tpl['name']}» отправлен в чат {chat_id}.")
    return True


def _get_chats(crd: "Cardinal") -> dict:
    """Возвращает {chat_id: ChatShortcut} (с обновлением с FunPay, при ошибке — кэш)."""
    try:
        return crd.account.get_chats(update=True) or {}
    except Exception:
        logger.debug("QuickReplies: get_chats(update=True) не удался, беру кэш.", exc_info=True)
        try:
            return crd.account.get_chats() or {}
        except Exception:
            return {}


# ══════════════════════════ TELEGRAM-ИНТЕРФЕЙС ══════════════════════════════════
def _home_text(crd: "Cardinal") -> str:
    settings = _load_settings()
    state = "ВКЛ 🟢" if settings["enabled"] else "ВЫКЛ 🔴"
    templates = _normalize_templates(settings["templates"])
    lines = [f"⚡ <b>{_escape(NAME)}</b> <i>v{VERSION}</i>\n",
             f"Шаблоны ответов отправляются покупателям в чат FunPay прямо из Telegram.\n",
             f"Плагин: <b>{state}</b>",
             f"📝 Шаблонов: <b>{len(templates)}/{MAX_TEMPLATES}</b>",
             f"📨 Отправлено всего: <b>{settings['stats'].get('sent', 0)}</b>\n",
             "<b>Как отправить:</b> «📨 Отправить покупателю» → чат → шаблон → подтвердить.",
             "<b>Переменная:</b> <code>$username</code> — ник покупателя."]
    return "\n".join(lines)


def _home_kb() -> K:
    settings = _load_settings()
    kb = K(row_width=1)
    kb.add(B("📨 Отправить покупателю", None, CB_SEND))
    kb.add(B("➕ Добавить шаблон", None, CB_ADD))
    for t in _normalize_templates(settings["templates"])[:PAGE_SIZE * 3]:
        kb.add(B(f"📝 {_short(t['name'], 34)}", None, f"{CB_TPL}:{t['id']}"))
    kb.add(B("🔄 Обновить меню", None, CB_HOME))
    if CBT:
        kb.add(B("🔙 Назад к плагину", None, f"{CBT.EDIT_PLUGIN}:{UUID}:0"))
    return kb


def _tpl_kb(tpl_id: int) -> K:
    kb = K(row_width=2)
    kb.add(B("✏️ Изменить текст", None, f"{CB_EDIT}:{tpl_id}"),
           B("🏷 Переименовать", None, f"{CB_RENAME}:{tpl_id}"))
    kb.add(B("🗑 Удалить", None, f"{CB_DEL}:{tpl_id}"))
    kb.add(B("🔙 Назад", None, CB_HOME))
    return kb


def _answer(bot, c: CallbackQuery, text: str | None = None, alert: bool = False) -> None:
    try:
        bot.answer_callback_query(c.id, text, show_alert=alert)
    except Exception:
        pass


def _edit_or_send(bot, c: CallbackQuery, text: str, kb: K | None) -> None:
    try:
        bot.edit_message_text(text, c.message.chat.id, c.message.id, parse_mode="HTML", reply_markup=kb)
    except ApiTelegramException as e:
        if "message is not modified" not in str(e):
            try:
                bot.send_message(c.message.chat.id, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                logger.debug("TRACEBACK", exc_info=True)
    except Exception:
        try:
            bot.send_message(c.message.chat.id, text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            logger.debug("TRACEBACK", exc_info=True)


def init(crd: "Cardinal", *args) -> None:
    """Инициализация плагина (BIND_TO_PRE_INIT)."""
    global _cardinal
    _cardinal = crd
    settings = _load_settings()
    # нормализация/первичное создание шаблонов
    settings["templates"] = _normalize_templates(settings["templates"])
    _save_settings(settings)

    tg = crd.telegram
    if tg is not None:
        bot = tg.bot

        def open_home(c: CallbackQuery) -> None:
            _answer(bot, c)
            _edit_or_send(bot, c, _home_text(crd), _home_kb())

        # ───────────────────── просмотр шаблона ─────────────────────
        def open_tpl(c: CallbackQuery) -> None:
            settings = _load_settings()
            try:
                tpl_id = int(c.data.split(":")[-1])
            except (ValueError, IndexError):
                return _answer(bot, c, "❌ Неверный id шаблона.", alert=True)
            tpl = _find_template(settings, tpl_id)
            if tpl is None:
                _answer(bot, c, "❌ Шаблон не найден.", alert=True)
                return _edit_or_send(bot, c, _home_text(crd), _home_kb())
            _answer(bot, c)
            _edit_or_send(bot, c,
                          f"📝 <b>{_escape(tpl['name'])}</b> <i>(#{tpl['id']})</i>\n\n"
                          f"<code>{_escape(tpl['text'][:800])}</code>\n\n"
                          f"Переменная <code>$username</code> при отправке заменится на ник покупателя.",
                          _tpl_kb(tpl_id))

        # ───────────────────── добавление шаблона ─────────────────────
        def act_add(c: CallbackQuery) -> None:
            settings = _load_settings()
            if len(_normalize_templates(settings["templates"])) >= MAX_TEMPLATES:
                return _answer(bot, c, f"❌ Достигнут лимит шаблонов ({MAX_TEMPLATES}).", alert=True)
            result = bot.send_message(
                c.message.chat.id,
                "🏷 Введите <b>название</b> нового шаблона (до 40 символов).\n"
                "Например: <code>Товар закончился</code>",
                parse_mode="HTML", reply_markup=skb.CLEAR_STATE_BTN() if skb else None)
            tg.set_state(c.message.chat.id, result.id, c.from_user.id, ST_ADD_NAME)
            _answer(bot, c)

        def st_add_name(m: Message) -> None:
            name = str(m.text or "").strip()[:NAME_LIMIT]
            if not name:
                return bot.reply_to(m, "❌ Пустое название. Попробуйте еще раз.")
            result = bot.send_message(
                m.chat.id,
                f"📝 Шаблон <b>{_escape(name)}</b>.\n\nТеперь введите <b>текст</b> ответа "
                f"(до {TEXT_LIMIT} символов).\nПеременная: <code>$username</code> — ник покупателя.",
                parse_mode="HTML", reply_markup=skb.CLEAR_STATE_BTN() if skb else None)
            tg.set_state(m.chat.id, result.id, m.from_user.id, ST_ADD_TEXT, {"name": name})
            bot.delete_message(m.chat.id, m.id)

        def st_add_text(m: Message) -> None:
            state = tg.get_state(m.chat.id, m.from_user.id) or {}
            name = str((state.get("data") or {}).get("name") or "")[:NAME_LIMIT]
            tg.clear_state(m.chat.id, m.from_user.id, True)
            text = str(m.text or "").strip()[:TEXT_LIMIT]
            if not text:
                return bot.reply_to(m, "❌ Пустой текст — шаблон не добавлен.")
            settings = _load_settings()
            new_id = _add_template(settings, name, text)
            if new_id is None:
                return bot.reply_to(m, f"❌ Не удалось добавить шаблон (лимит {MAX_TEMPLATES} или пустые данные).")
            bot.reply_to(m, f"✅ Шаблон «{_escape(name)}» добавлен (#{new_id}).", parse_mode="HTML")

        # ───────────────────── редактирование / удаление ─────────────────────
        def act_edit(c: CallbackQuery) -> None:
            tpl_id = c.data.split(":")[-1]
            result = bot.send_message(
                c.message.chat.id,
                f"✏️ Введите <b>новый текст</b> шаблона #{_escape(tpl_id)}.\n"
                f"Переменная: <code>$username</code> — ник покупателя.",
                parse_mode="HTML", reply_markup=skb.CLEAR_STATE_BTN() if skb else None)
            tg.set_state(c.message.chat.id, result.id, c.from_user.id, ST_EDIT_TEXT, {"id": tpl_id})
            _answer(bot, c)

        def st_edit_text(m: Message) -> None:
            state = tg.get_state(m.chat.id, m.from_user.id) or {}
            tpl_id = (state.get("data") or {}).get("id")
            tg.clear_state(m.chat.id, m.from_user.id, True)
            text = str(m.text or "").strip()[:TEXT_LIMIT]
            if not text:
                return bot.reply_to(m, "❌ Пустой текст не сохранен.")
            settings = _load_settings()
            tpl = _find_template(settings, tpl_id)
            if tpl is None:
                return bot.reply_to(m, "❌ Шаблон не найден.")
            tpl["text"] = text
            _save_settings(settings)
            bot.reply_to(m, f"✅ Текст шаблона «{_escape(tpl['name'])}» обновлен.", parse_mode="HTML")

        def act_rename(c: CallbackQuery) -> None:
            tpl_id = c.data.split(":")[-1]
            result = bot.send_message(
                c.message.chat.id,
                f"🏷 Введите <b>новое название</b> шаблона #{_escape(tpl_id)}.",
                parse_mode="HTML", reply_markup=skb.CLEAR_STATE_BTN() if skb else None)
            tg.set_state(c.message.chat.id, result.id, c.from_user.id, ST_RENAME, {"id": tpl_id})
            _answer(bot, c)

        def st_rename(m: Message) -> None:
            state = tg.get_state(m.chat.id, m.from_user.id) or {}
            tpl_id = (state.get("data") or {}).get("id")
            tg.clear_state(m.chat.id, m.from_user.id, True)
            name = str(m.text or "").strip()[:NAME_LIMIT]
            if not name:
                return bot.reply_to(m, "❌ Пустое название не сохранено.")
            settings = _load_settings()
            tpl = _find_template(settings, tpl_id)
            if tpl is None:
                return bot.reply_to(m, "❌ Шаблон не найден.")
            tpl["name"] = name
            _save_settings(settings)
            bot.reply_to(m, f"✅ Шаблон #{tpl['id']} переименован в «{_escape(name)}».", parse_mode="HTML")

        def act_del(c: CallbackQuery) -> None:
            tpl_id = c.data.split(":")[-1]
            settings = _load_settings()
            tpl = _find_template(settings, tpl_id)
            if tpl is None:
                _answer(bot, c, "❌ Шаблон не найден.", alert=True)
                return _edit_or_send(bot, c, _home_text(crd), _home_kb())
            kb = K(row_width=2)
            kb.add(B("✅ Да, удалить", None, f"{CB_DEL_YES}:{tpl_id}"), B("❌ Отмена", None, f"{CB_TPL}:{tpl_id}"))
            _answer(bot, c)
            _edit_or_send(bot, c, f"🗑 Удалить шаблон «{_escape(tpl['name'])}»?", kb)

        def act_del_yes(c: CallbackQuery) -> None:
            tpl_id = c.data.split(":")[-1]
            settings = _load_settings()
            tpl = _find_template(settings, tpl_id)
            if tpl is not None:
                settings["templates"] = [t for t in settings["templates"] if int(t["id"]) != int(tpl_id)]
                _save_settings(settings)
                _answer(bot, c, f"🗑 Шаблон «{tpl['name']}» удален.")
            else:
                _answer(bot, c, "❌ Шаблон не найден.", alert=True)
            _edit_or_send(bot, c, _home_text(crd), _home_kb())

        # ───────────────────── отправка покупателю ─────────────────────
        def act_send(c: CallbackQuery) -> None:
            _answer(bot, c, "⏳ Загружаю чаты с FunPay…")
            chats = _get_chats(crd)
            if not chats:
                return _edit_or_send(bot, c, "❌ Не удалось получить список чатов с FunPay. Попробуйте позже.",
                                     _home_kb())
            items = list(chats.items())[:PAGE_SIZE * 4]
            kb = K(row_width=1)
            for chat_id, chat in items:
                chat_name = str(getattr(chat, "name", "") or chat_id)
                unread = "🟠 " if getattr(chat, "unread", False) else ""
                kb.add(B(f"{unread}👤 {_short(chat_name, 30)}", None, f"{CB_CHAT}:{chat_id}"))
            kb.add(B("🔙 Назад", None, CB_HOME))
            _edit_or_send(bot, c, f"📨 Выберите чат покупателя (<b>{len(items)}</b> последних):\n"
                                  f"🟠 — есть непрочитанные сообщения", kb)

        def pick_chat(c: CallbackQuery) -> None:
            chat_id = c.data.split(":")[-1]
            chats = _get_chats(crd)
            chat = chats.get(int(chat_id)) if chat_id.isdigit() else chats.get(chat_id)
            chat_name = str(getattr(chat, "name", "") or chat_id) if chat is not None else chat_id
            settings = _load_settings()
            templates = _normalize_templates(settings["templates"])
            if not templates:
                _answer(bot, c, "❌ Сначала добавьте хотя бы один шаблон.", alert=True)
                return _edit_or_send(bot, c, _home_text(crd), _home_kb())
            kb = K(row_width=1)
            for t in templates[:PAGE_SIZE * 3]:
                kb.add(B(f"📝 {_short(t['name'], 34)}", None, f"{CB_PICK}:{chat_id}:{t['id']}"))
            kb.add(B("🔙 Назад", None, CB_SEND))
            _answer(bot, c)
            _edit_or_send(bot, c, f"📨 Чат: <b>{_escape(chat_name)}</b>\nВыберите шаблон для отправки:", kb)

        def pick_tpl(c: CallbackQuery) -> None:
            _, _, chat_id, tpl_id = c.data.split(":", 3)
            settings = _load_settings()
            tpl = _find_template(settings, tpl_id)
            if tpl is None:
                _answer(bot, c, "❌ Шаблон не найден.", alert=True)
                return _edit_or_send(bot, c, _home_text(crd), _home_kb())
            chats = _get_chats(crd)
            chat = chats.get(int(chat_id)) if str(chat_id).isdigit() else None
            chat_name = str(getattr(chat, "name", "") or "")
            preview = _fill(tpl["text"], chat_name or chat_id)
            kb = K(row_width=2)
            kb.add(B("✅ Отправить", None, f"{CB_CONF}:{chat_id}:{tpl_id}"),
                   B("❌ Отмена", None, CB_SEND))
            _answer(bot, c)
            _edit_or_send(bot, c,
                          f"👁 <b>Предпросмотр</b> для чата <b>{_escape(chat_name or chat_id)}</b>:\n\n"
                          f"<code>{_escape(preview[:800])}</code>", kb)

        def confirm_send(c: CallbackQuery) -> None:
            _, _, chat_id, tpl_id = c.data.split(":", 3)
            chats = _get_chats(crd)
            chat = chats.get(int(chat_id)) if str(chat_id).isdigit() else None
            chat_name = str(getattr(chat, "name", "") or "")
            ok = send_template(crd, chat_id, tpl_id, username=chat_name)
            if ok:
                _answer(bot, c, f"✅ Отправлено в чат {chat_name or chat_id}.")
                _edit_or_send(bot, c, _home_text(crd), _home_kb())
            else:
                _answer(bot, c, "❌ Не удалось отправить. Смотрите логи.", alert=True)

        def _state_eq(m: Message, state: str) -> bool:
            try:
                return tg.check_state(m.chat.id, m.from_user.id, state)
            except Exception:
                return False

        def cmd_open(m: Message) -> None:
            bot.send_message(m.chat.id, _home_text(crd), parse_mode="HTML", reply_markup=_home_kb())

        def noop(c: CallbackQuery) -> None:
            _answer(bot, c)

        tg.cbq_handler(open_home, lambda c: c.data.startswith(f"{CBT.PLUGIN_SETTINGS}:{UUID}") if CBT else False)
        tg.cbq_handler(open_home, lambda c: c.data == CB_HOME)
        tg.cbq_handler(open_tpl, lambda c: c.data.startswith(f"{CB_TPL}:"))
        tg.cbq_handler(act_add, lambda c: c.data == CB_ADD)
        tg.cbq_handler(act_send, lambda c: c.data == CB_SEND)
        tg.cbq_handler(pick_chat, lambda c: c.data.startswith(f"{CB_CHAT}:"))
        tg.cbq_handler(pick_tpl, lambda c: c.data.startswith(f"{CB_PICK}:"))
        tg.cbq_handler(confirm_send, lambda c: c.data.startswith(f"{CB_CONF}:"))
        tg.cbq_handler(act_edit, lambda c: c.data.startswith(f"{CB_EDIT}:"))
        tg.cbq_handler(act_rename, lambda c: c.data.startswith(f"{CB_RENAME}:"))
        tg.cbq_handler(act_del, lambda c: c.data.startswith(f"{CB_DEL}:") and not c.data.startswith(f"{CB_DEL_YES}:"))
        tg.cbq_handler(act_del_yes, lambda c: c.data.startswith(f"{CB_DEL_YES}:"))
        tg.cbq_handler(noop, lambda c: c.data == CB_NOOP)
        tg.msg_handler(st_add_name, func=lambda m: _state_eq(m, ST_ADD_NAME))
        tg.msg_handler(st_add_text, func=lambda m: _state_eq(m, ST_ADD_TEXT))
        tg.msg_handler(st_edit_text, func=lambda m: _state_eq(m, ST_EDIT_TEXT))
        tg.msg_handler(st_rename, func=lambda m: _state_eq(m, ST_RENAME))
        tg.msg_handler(cmd_open, commands=["quick"])

        try:
            crd.add_telegram_commands(UUID, [("quick", "Быстрые ответы покупателям по шаблонам.", True)])
        except Exception:
            logger.debug("TRACEBACK", exc_info=True)

        logger.info(f"QuickReplies v{VERSION}: Telegram-меню зарегистрировано (команда /quick).")
    else:
        logger.info(f"QuickReplies v{VERSION}: Telegram выключен — шаблоны доступны через send_template().")


def on_delete(crd: "Cardinal", c=None) -> None:
    """Вызывается при удалении плагина через ПУ (BIND_TO_DELETE)."""
    logger.info("QuickReplies: плагин выгружен.")


# ══════════════════════ ПРИВЯЗКА К СОБЫТИЯМ КАРДИНАЛА ═══════════════════════════
BIND_TO_PRE_INIT = [init]
BIND_TO_DELETE = on_delete
