"""Trimmed copies of real Samad responses, used to pin the client's behaviour."""

# A week that is open: type INFO, with two days of programs. The two Tuesday
# entries carry reserveRuleViolated=False, i.e. they can actually be booked.
PROGRAMS_OPEN = {
    "type": "INFO",
    "code": 10106000,
    "message": "programs",
    "messageFa": "برنامه های غذایی",
    "messageResource": "PROGRAMS",
    "payload": {
        "userId": 24554,
        "selfWeekPrograms": [
            [
                {
                    "programId": 252746,
                    "date": "2026-09-26",
                    "dayTranslated": "Saturday",
                    "mealTypeName": "ناهار",
                    "foodName": "زرشک‌پلو با مرغ",
                    "foodTypeTitle": "غذای پرهزینه 1",
                    "price": 350000,
                    "reserveRuleViolated": True,
                },
                {
                    "programId": 253151,
                    "date": "2026-09-26",
                    "dayTranslated": "Saturday",
                    "mealTypeName": "ناهار",
                    "foodName": "ساندویچ ژامبون‌گوشت (یونی‌فود)",
                    "foodTypeTitle": "غذای رستوران مکمل 3",
                    "price": 100000,
                    "reserveRuleViolated": True,
                },
            ],
            [
                {
                    "programId": 252869,
                    "date": "2026-09-29",
                    "dayTranslated": "Tuesday",
                    "mealTypeName": "ناهار",
                    "foodName": "چلو‌خورش قیمه‌ سیب زمینی",
                    "foodTypeTitle": "غذای پرهزینه 1",
                    "price": 350000,
                    "reserveRuleViolated": False,
                },
                {
                    "programId": 253196,
                    "date": "2026-09-29",
                    "dayTranslated": "Tuesday",
                    "mealTypeName": "ناهار",
                    "foodName": "ساندویچ دنرکباب‌گوشت‌و‌پنیر (یونی‌فود)",
                    "foodTypeTitle": "غذای رستوران مکمل 3",
                    "price": 450000,
                    "reserveRuleViolated": False,
                },
            ],
        ],
        "userWeekReserves": [],
        "mealTypes": [{"id": 1, "name": "ناهار"}],
    },
}

# The same endpoint for a week that has not been published yet.
PROGRAMS_NOT_OPEN = {
    "type": "ERROR",
    "code": 10108005,
    "message": "self has no program",
    "messageFa": "هیچ برنامه ی غذایی ای تعریف نشده است.",
    "messageResource": "SELF_HAS_NO_PROGRAM",
}

# An error we do not recognise — should be surfaced, not read as "not open".
PROGRAMS_UNKNOWN_ERROR = {
    "type": "ERROR",
    "code": 10100001,
    "message": "something else broke",
    "messageFa": "خطا",
    "messageResource": "SOMETHING_ELSE",
}

TOKEN_REFRESH_OK = {
    "access_token": "header.eyJleHAiOjQ4ODMyNjQwMDB9.sig",
    "token_type": "bearer",
    "refresh_token": "header.eyJleHAiOjQ4ODMyNjQwMDB9.newsig",
    "expires_in": 15400777,
    "scope": "read write",
}
