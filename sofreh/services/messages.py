"""Telegram message bodies.

Written in Persian: this lands on the user's phone next to the dining app, and
the food names are Persian anyway. Everything interpolated is HTML-escaped,
because food titles are free text from the server.
"""

from html import escape

from django.conf import settings

# Reading a day name off dayTranslated keeps us out of calendar conversions.
PERSIAN_DAYS = {
    "Saturday": "شنبه",
    "Sunday": "یکشنبه",
    "Monday": "دوشنبه",
    "Tuesday": "سه‌شنبه",
    "Wednesday": "چهارشنبه",
    "Thursday": "پنجشنبه",
    "Friday": "جمعه",
}

# Keep the per-day line readable on a phone.
MAX_FOODS_PER_DAY = 4


def _site_url():
    return settings.SOFREH["API_BASE_URL"]


def _day_line(day):
    name = PERSIAN_DAYS.get(day.day, day.day)
    foods = [escape(food) for food in day.foods[:MAX_FOODS_PER_DAY]]
    if len(day.foods) > MAX_FOODS_PER_DAY:
        foods.append("…")
    return f"• <b>{escape(name)}</b> {escape(day.date)}: " + "، ".join(foods)


def reservation_open(watch, week_start, result):
    """The message the whole project exists to send.

    Leads with the watch name, because several watches run at once -- the
    lunch self and the dorm self each send their own message, and the first
    line has to say which one you are looking at.
    """
    week_line = f"📅 هفته‌ی شنبه <b>{escape(str(week_start))}</b>"
    if result.meal_names:
        week_line += " · " + escape("، ".join(result.meal_names))

    lines = [
        "🍽 <b>رزرو غذا باز شد!</b>",
        f"📍 <b>{escape(watch.name)}</b>",
        "",
        week_line,
    ]

    if result.reservable_count:
        lines.append(
            f"✅ {result.reservable_count} مورد قابل رزرو از "
            f"{result.program_count} برنامه"
        )
    else:
        # The week is published but every program is still rule-blocked; say so
        # plainly instead of implying a booking is waiting.
        lines.append(
            f"ℹ️ {result.program_count} برنامه منتشر شده "
            "(هنوز موردی قابل رزرو نیست)"
        )

    if result.days:
        lines.append("")
        lines.extend(_day_line(day) for day in result.days)

    lines += ["", f"🔗 {escape(_site_url())}"]
    return "\n".join(lines)


def auth_problem(account, detail):
    """Sent once when the stored tokens stop working."""
    return "\n".join(
        [
            "⚠️ <b>Sofreh: توکن سماد کار نمی‌کند</b>",
            "",
            "درخواست با خطای <b>401</b> برگشت و تازه‌سازی توکن هم موفق نبود.",
            "برای ادامه‌ی مانیتورینگ باید access/refresh توکن جدید را در پنل ادمین بگذاری.",
            "",
            f"<code>{escape(detail[:500])}</code>",
            "",
            f"<i>حساب: {escape(account.label)}</i>",
        ]
    )


def api_problem(watch, week_start, result):
    """Sent when the endpoint answers in a way we did not plan for."""
    detail = result.detail or result.api_message or "بدون توضیح"
    return "\n".join(
        [
            "⚠️ <b>Sofreh: پاسخ غیرمنتظره از سماد</b>",
            f"📍 <b>{escape(watch.name)}</b>",
            "",
            f"📅 هفته‌ی <b>{escape(str(week_start))}</b>",
            f"وضعیت: <code>{escape(str(result.outcome))}</code>"
            + (f" / HTTP {result.http_status}" if result.http_status else ""),
            "",
            f"<code>{escape(str(detail)[:500])}</code>",
        ]
    )


def token_expiring(account, days_left, expires_at):
    """Sent once, while there is still time to do something about it.

    The access token renews itself; the refresh token does not. When it goes,
    the watcher goes blind, so this warning arrives before that happens rather
    than after.
    """
    if days_left <= 0:
        when = "<b>همین حالا منقضی شده</b>"
    elif days_left == 1:
        when = "<b>فردا</b> منقضی می‌شود"
    else:
        when = f"تا <b>{days_left} روز</b> دیگر منقضی می‌شود"

    return "\n".join(
        [
            "⏳ <b>Sofreh: رفرش‌توکن سماد رو به اتمام است</b>",
            "",
            f"رفرش‌توکن {when} (تاریخ: {escape(f'{expires_at:%Y-%m-%d}')}).",
            "بعد از آن، تمدید خودکار کار نمی‌کند و باید دوباره از سایت سماد "
            "لاگین کنی و توکن‌های جدید را در پنل ادمین بگذاری.",
            "",
            "تا آن موقع همه چیز عادی کار می‌کند — این فقط یک یادآوری است.",
            "",
            f"<i>حساب: {escape(account.label)}</i>",
        ]
    )


def recovered(watch, week_start):
    """Sent when polling works again after a problem, so silence means health."""
    return "\n".join(
        [
            "✅ <b>Sofreh دوباره وصل شد</b>",
            f"📍 <b>{escape(watch.name)}</b>",
            "",
            f"📅 در حال پایش هفته‌ی <b>{escape(str(week_start))}</b>",
        ]
    )
