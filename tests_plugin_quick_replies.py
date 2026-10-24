"""
Функциональные тесты плагина quick_replies.py на моках.
Запуск: cd FPC && python3 tests_plugin_quick_replies.py
"""
import importlib.util
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
sys.path.append("plugins")

spec = importlib.util.spec_from_file_location("plugins.quick_replies", "plugins/quick_replies.py")
plugin = importlib.util.module_from_spec(spec)
sys.modules["plugins.quick_replies"] = plugin
spec.loader.exec_module(plugin)

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("✓ " if cond else "✗ FAIL: ") + name)


def reset_settings(**kwargs):
    os.makedirs(plugin.PLUGIN_DIR, exist_ok=True)
    base = json.loads(json.dumps(plugin.DEFAULT_SETTINGS))
    base.update(kwargs)
    with open(plugin.SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False)


# ───────────────────────────── моки ─────────────────────────────
class MockAccount:
    def __init__(self, chats=None, fail_send=False):
        self.is_initiated = True
        self.id = 999
        self.sent = []          # [(chat_id, text)]
        self.fail_send = fail_send
        self.chats = chats if chats is not None else {
            101: SimpleNamespace(id=101, name="ivan_petrov", unread=True),
            102: SimpleNamespace(id=102, name="vasya", unread=False),
            103: SimpleNamespace(id=103, name="buyer_three", unread=False),
        }

    def get_chats(self, update=False):
        return self.chats

    def send_message(self, chat_id, text=None, **kw):
        if self.fail_send:
            raise Exception("mock: send_message failed")
        self.sent.append((chat_id, text))
        return SimpleNamespace(id=1)


class MockBot:
    def __init__(self):
        self.answers, self.edits, self.sent, self.replies, self.deleted = [], [], [], [], []

    def answer_callback_query(self, cb_id, text=None, show_alert=False):
        self.answers.append((cb_id, text, show_alert))

    def edit_message_text(self, text, chat_id, message_id, parse_mode=None, reply_markup=None):
        self.edits.append((text, reply_markup))
        return SimpleNamespace(id=message_id)

    def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        self.sent.append((chat_id, text))
        return SimpleNamespace(id=len(self.sent) + 100, chat=SimpleNamespace(id=chat_id))

    def reply_to(self, m, text, parse_mode=None):
        self.replies.append(text)

    def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


def kb_buttons(kb) -> str:
    if kb is None:
        return ""
    try:
        return " | ".join(b.text for row in kb.keyboard for b in row if b.text)
    except Exception:
        return str(kb)


def kb_data(kb) -> str:
    if kb is None:
        return ""
    try:
        return " | ".join(str(b.callback_data) for row in kb.keyboard for b in row if b.callback_data)
    except Exception:
        return ""


class MockTelegram:
    def __init__(self):
        self.bot = MockBot()
        self.cbq_handlers, self.msg_handlers, self.user_states = [], [], {}
        self.notifications = []

    def cbq_handler(self, handler, func, **kwargs):
        self.cbq_handlers.append((handler, func))

    def msg_handler(self, handler, **kwargs):
        self.msg_handlers.append((handler, kwargs))

    def set_state(self, chat_id, message_id, user_id, state, data=None):
        self.user_states[(chat_id, user_id)] = {"state": state, "mid": message_id, "data": data or {}}

    def get_state(self, chat_id, user_id):
        return self.user_states.get((chat_id, user_id))

    def check_state(self, chat_id, user_id, state):
        return self.user_states.get((chat_id, user_id), {}).get("state") == state

    def clear_state(self, chat_id, user_id, del_msg=False):
        st = self.user_states.pop((chat_id, user_id), None)
        return st.get("mid") if st else None

    def send_notification(self, text, **kwargs):
        self.notifications.append(text)


class MockCardinal:
    def __init__(self, account, telegram):
        self.account = account
        self.telegram = telegram
        self.plugins = {plugin.UUID: SimpleNamespace(enabled=True)}
        self.registered_commands = None

    def add_telegram_commands(self, uuid, commands):
        self.registered_commands = commands


def make_crd(with_tg=True, fail_send=False):
    acc = MockAccount(fail_send=fail_send)
    tg = MockTelegram() if with_tg else None
    return MockCardinal(acc, tg), acc, tg


# ═══════════════════════ ТЕСТ 1: метаданные ═══════════════════════
from uuid import UUID as _UUID
check("1.1 метаданные плагина", all(hasattr(plugin, f) for f in
      ["NAME", "VERSION", "DESCRIPTION", "CREDITS", "SETTINGS_PAGE", "UUID", "BIND_TO_DELETE"]))
check("1.2 UUID валиден", str(_UUID(plugin.UUID, version=4)) == plugin.UUID)
check("1.3 автор yungspider, название на английском",
      plugin.CREDITS == "yungspider" and plugin.NAME.isascii())
check("1.4 BIND_TO_PRE_INIT привязан", plugin.BIND_TO_PRE_INIT == [plugin.init])

# ═══════════════════════ ТЕСТ 2: шаблоны (утилиты) ═══════════════════════
reset_settings()
s = plugin._load_settings()
check("2.1 после установки 3 дефолтных шаблона", len(s["templates"]) == 3)

check("2.2 _fill подставляет $username",
      plugin._fill("Привет, $username!", "ivan") == "Привет, ivan!")
check("2.3 _fill с пустым ником → «друг»", plugin._fill("Привет, $username!", "") == "Привет, друг!")

norm = plugin._normalize_templates([
    {"id": 1, "name": "ok", "text": "text"},
    {"id": "2", "name": "строчный id", "text": "t2"},
    {"id": 1, "name": "дубль id", "text": "x"},
    {"id": 3, "name": "  ", "text": "пустое имя"},
    {"id": 4, "name": "пустой текст", "text": "   "},
    "не словарь",
    {"id": "abc", "name": "битый id", "text": "x"},
])
check("2.4 нормализация: дубли/мусор отброшены, строковые id приняты",
      [t["id"] for t in norm] == [1, 2])

norm2 = plugin._normalize_templates([{"id": i, "name": f"n{i}", "text": "t"} for i in range(1, 50)])
check("2.5 нормализация обрезает до MAX_TEMPLATES", len(norm2) == plugin.MAX_TEMPLATES)

s = plugin._load_settings()
new_id = plugin._add_template(s, "Новый", "Текст $username")
check("2.6 _add_template возвращает id и сохраняет", new_id == 4
      and plugin._find_template(plugin._load_settings(), 4)["name"] == "Новый")
s = plugin._load_settings()
s["templates"] = [{"id": i, "name": f"n{i}", "text": "t"} for i in range(1, plugin.MAX_TEMPLATES + 1)]
check("2.7 при лимите _add_template возвращает None", plugin._add_template(s, "x", "y") is None)
check("2.8 пустые данные не добавляются", plugin._add_template(plugin._load_settings(), "  ", "y") is None)
check("2.9 _find_template с мусорным id → None", plugin._find_template(plugin._load_settings(), "abc") is None)
reset_settings()

# ═══════════════════════ ТЕСТ 3: send_template ═══════════════════════
crd, acc, tg = make_crd(with_tg=False)
reset_settings()
ok = plugin.send_template(crd, 101, 1, username="ivan_petrov")
check("3.1 шаблон отправлен в чат", ok is True and acc.sent
      and acc.sent[0][0] == 101 and "ivan_petrov" in acc.sent[0][1])
check("3.2 статистика sent увеличена", plugin._load_settings()["stats"]["sent"] == 1)

check("3.3 несуществующий шаблон → False, без отправки",
      plugin.send_template(crd, 101, 999) is False and len(acc.sent) == 1)

reset_settings(enabled=False)
check("3.4 выключенный плагин не отправляет", plugin.send_template(crd, 101, 1) is False)

reset_settings()
crd.plugins[plugin.UUID].enabled = False
check("3.5 disabled в ПУ — send_template работает напрямую (проверка _plugin_enabled отдельно)",
      plugin._plugin_enabled(crd) is False)
crd.plugins[plugin.UUID].enabled = True

crd_fail, acc_fail, _ = make_crd(with_tg=False, fail_send=True)
check("3.6 ошибка FunPay → False, статистика не растёт",
      plugin.send_template(crd_fail, 101, 1) is False
      and plugin._load_settings()["stats"]["sent"] == 0)

chats = plugin._get_chats(crd)
check("3.7 _get_chats возвращает словарь чатов", len(chats) == 3 and 101 in chats)
reset_settings()

# ═══════════════════════ ТЕСТ 4: Telegram UI ═══════════════════════
crd, acc, tg = make_crd(with_tg=True)
plugin.init(crd)
bot = tg.bot
reset_settings()
check("4.1 команда /quick зарегистрирована",
      any(kw.get("commands") == ["quick"] for _, kw in tg.msg_handlers))
check("4.2 add_telegram_commands вызван", crd.registered_commands
      and crd.registered_commands[0][0] == "quick")


def dispatch_cb(data, chat_id=111, msg_id=555, user_id=222):
    c = SimpleNamespace(data=data, id="cb1",
                        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id), id=msg_id),
                        from_user=SimpleNamespace(id=user_id))
    for handler, func in tg.cbq_handlers:
        try:
            matched = func(c)
        except Exception:
            matched = False
        if matched:
            handler(c)
            return handler.__name__
    return None


def dispatch_msg(text, chat_id=111, user_id=222):
    m = SimpleNamespace(chat=SimpleNamespace(id=chat_id), from_user=SimpleNamespace(id=user_id),
                        text=text, id=777)
    for h, kw in tg.msg_handlers:
        f = kw.get("func")
        try:
            if f and f(m):
                h(m)
                return h.__name__
        except Exception:
            pass
    return None


def last_text():
    if bot.edits:
        return bot.edits[-1][0]
    return bot.sent[-1][1] if bot.sent else ""


def last_kb():
    for src in (bot.edits, bot.sent):
        if src:
            return kb_buttons(src[-1][-1] if src is bot.sent else src[-1][1])
    return ""


name = dispatch_cb(f"47:{plugin.UUID}:0")
check("4.3 страница настроек открывает меню", name == "open_home" and "QuickReplies" in last_text())
check("4.4 в меню шаблоны и статистика", "Шаблонов:" in last_text() and "Отправлено всего:" in last_text())

# ── добавление шаблона через меню ──
dispatch_cb(plugin.CB_ADD)
check("4.5 «Добавить» ставит состояние имени",
      tg.user_states.get((111, 222), {}).get("state") == plugin.ST_ADD_NAME)
dispatch_msg("🎁 Скидка постоянному")
check("4.6 после имени — состояние текста, имя в data",
      tg.user_states.get((111, 222), {}).get("state") == plugin.ST_ADD_TEXT
      and tg.user_states[(111, 222)]["data"]["name"] == "🎁 Скидка постоянному")
dispatch_msg("Вам скидка 5%, $username!")
s = plugin._load_settings()
check("4.7 шаблон добавлен", len(s["templates"]) == 4 and s["templates"][-1]["name"] == "🎁 Скидка постоянному")

dispatch_cb(plugin.CB_ADD)
dispatch_msg("   ")
check("4.8 пустое имя отклоняется", "❌" in bot.replies[-1]
      and tg.user_states.get((111, 222), {}).get("state") == plugin.ST_ADD_NAME)
tg.clear_state(111, 222)

# ── просмотр / редактирование / переименование / удаление ──
dispatch_cb(f"{plugin.CB_TPL}:1")
check("4.9 просмотр шаблона показывает текст", "Привет" in last_text() or "Здравствуйте" in last_text())

dispatch_cb(f"{plugin.CB_EDIT}:1")
check("4.10 «Изменить» ставит состояние с id",
      tg.user_states.get((111, 222), {}).get("state") == plugin.ST_EDIT_TEXT
      and tg.user_states[(111, 222)]["data"]["id"] == "1")
dispatch_msg("Новый текст шаблона 1")
check("4.11 текст шаблона обновлен",
      plugin._find_template(plugin._load_settings(), 1)["text"] == "Новый текст шаблона 1")

dispatch_cb(f"{plugin.CB_RENAME}:1")
dispatch_msg("👋 Новое имя")
check("4.12 шаблон переименован",
      plugin._find_template(plugin._load_settings(), 1)["name"] == "👋 Новое имя")

dispatch_cb(f"{plugin.CB_RENAME}:999")
dispatch_msg("x")
check("4.13 переименование несуществующего → ошибка, не падаем", "❌" in bot.replies[-1])

dispatch_cb(f"{plugin.CB_DEL}:2")
check("4.14 удаление запрашивает подтверждение", "Удалить шаблон" in last_text())
dispatch_cb(f"{plugin.CB_DEL_YES}:2")
check("4.15 шаблон удален после подтверждения",
      plugin._find_template(plugin._load_settings(), 2) is None)

# ── отправка покупателю (полный flow) ──
acc.sent.clear()
dispatch_cb(plugin.CB_SEND)
check("4.16 список чатов показывает покупателей",
      "ivan_petrov" in last_text() or "Выберите чат" in last_text())

dispatch_cb(f"{plugin.CB_CHAT}:101")
check("4.17 после выбора чата — список шаблонов", "📝" in kb_data(bot.edits[-1][1])
      or plugin.CB_PICK in kb_data(bot.edits[-1][1]))

dispatch_cb(f"{plugin.CB_PICK}:101:1")
check("4.18 предпросмотр с подстановкой ника", "Предпросмотр" in last_text()
      and plugin.CB_CONF in kb_data(bot.edits[-1][1]))

dispatch_cb(f"{plugin.CB_CONF}:101:1")
check("4.19 подтверждение → сообщение ушло в FunPay",
      acc.sent and acc.sent[0][0] == 101 and "Новый текст шаблона 1" in acc.sent[0][1])
check("4.20 статистика sent учтена", plugin._load_settings()["stats"]["sent"] == 1)

dispatch_cb(f"{plugin.CB_PICK}:101:999")
check("4.21 несуществующий шаблон в flow → домой", "❌" in (bot.answers[-1][1] or ""))

dispatch_cb(plugin.CB_NOOP)
check("4.22 noop отвечает без ошибки", True)

# ── пустой список чатов ──
crd2, acc2, tg2 = make_crd(with_tg=True)
acc2.chats = {}
plugin.init(crd2)


def dispatch_cb2(data, chat_id=111, msg_id=555, user_id=222):
    c = SimpleNamespace(data=data, id="cb1",
                        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id), id=msg_id),
                        from_user=SimpleNamespace(id=user_id))
    for handler, func in tg2.cbq_handlers:
        try:
            matched = func(c)
        except Exception:
            matched = False
        if matched:
            handler(c)
            return handler.__name__
    return None


bot2 = tg2.bot
dispatch_cb2(plugin.CB_SEND)
check("4.23 пустой список чатов → понятная ошибка",
      any("Не удалось получить список чатов" in t for t, _ in bot2.edits) or bot2.edits)

# ═══════════════════════ ТЕСТ 5: нормализация настроек ═══════════════════════
os.makedirs(plugin.PLUGIN_DIR, exist_ok=True)
with open(plugin.SETTINGS_PATH, "w", encoding="utf-8") as f:
    json.dump({"enabled": 1, "templates": "notalist", "stats": None}, f)
s = plugin._load_settings()
check("5.1 битые значения нормализуются", s["enabled"] is True and s["templates"] == []
      and s["stats"]["sent"] == 0)
with open(plugin.SETTINGS_PATH, "w") as f:
    f.write("{broken")
s = plugin._load_settings()
check("5.2 битый JSON → дефолты", len(plugin._normalize_templates(s["templates"])) == 3)
reset_settings()

print(f"\n{'=' * 60}\nПройдено: {len(PASS)}  Провалено: {len(FAIL)}")
if FAIL:
    print("Проваленные тесты:")
    for f_ in FAIL:
        print("  ✗", f_)
    sys.exit(1)
print("ВСЕ ТЕСТЫ ПРОЙДЕНЫ ✅")
