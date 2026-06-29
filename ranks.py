"""
Иерархия званий StalZone (фиксированная). Другой иерархии быть не может.
Звание участника определяется так: ручное назначение в боте (приоритет),
иначе по роли Discord, иначе 'private' (Рядовой).
"""

# От высшего к низшему
RANK_ORDER = ['leader', 'colonel', 'officer', 'sergeant', 'fighter', 'private']

RANK_NAMES = {
    'leader': 'Лидер',
    'colonel': 'Полковник',
    'officer': 'Офицер',
    'sergeant': 'Сержант',
    'fighter': 'Боец',
    'private': 'Рядовой',
}

RANK_ICONS = {
    'leader': '👑',
    'colonel': '🎖️',
    'officer': '⚔️',
    'sergeant': '🛡️',
    'fighter': '🔫',
    'private': '🪖',
}

# Звания с правами управления (officer+)
MANAGEMENT_RANKS = ['leader', 'colonel', 'officer']

DEFAULT_RANK = 'private'

# Алиасы ввода (рус/англ) -> внутренний ключ
RANK_ALIASES = {
    'leader': 'leader', 'лидер': 'leader', 'глава': 'leader',
    'colonel': 'colonel', 'полковник': 'colonel', 'полк': 'colonel',
    'officer': 'officer', 'офицер': 'officer',
    'sergeant': 'sergeant', 'сержант': 'sergeant', 'серж': 'sergeant',
    'fighter': 'fighter', 'боец': 'fighter',
    'private': 'private', 'рядовой': 'private', 'ряд': 'private',
}


def normalize_rank(text: str):
    """Приводит ввод пользователя к ключу звания или None."""
    if not text:
        return None
    return RANK_ALIASES.get(text.lower().strip())


def rank_name(rank: str) -> str:
    return RANK_NAMES.get(rank, rank)


def rank_icon(rank: str) -> str:
    return RANK_ICONS.get(rank, '')


def rank_label(rank: str) -> str:
    """Иконка + название, напр. '👑 Лидер'."""
    return f"{RANK_ICONS.get(rank, '')} {RANK_NAMES.get(rank, rank)}".strip()
