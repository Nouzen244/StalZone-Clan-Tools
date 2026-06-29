"""
Веб-сервер сайта StalZone (aiohttp), встроенный в процесс бота.

Поднимается вместе с ботом и отдаёт:
  - SPA (bot/web/index.html) на `/`
  - JSON API на `/api/*`, работающий с той же SQLite-базой, что и бот.

Источник истины — бот. Вход на сайт только через одноразовый код из команды
`!site` (без паролей). Права на запись — у офицера и выше (MANAGEMENT_RANKS).
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from aiohttp import web

from ranks import (
    RANK_ORDER, RANK_NAMES, RANK_ICONS, MANAGEMENT_RANKS, DEFAULT_RANK,
    normalize_rank,
)

logger = logging.getLogger('WebServer')

WEB_DIR = Path(__file__).parent / 'web'
SESSION_COOKIE = 'sc_session'

# Статусы посещаемости сайта: 1=присутствовал, 0=отсутствовал, 2=уважительно
EXCUSED_MARK = 'У/П'


# ============================================
# УТИЛИТЫ
# ============================================

def json_response(data, status=200):
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False))


def permissions_for(rank: str) -> dict:
    """Набор прав для UI на основе звания (бот — источник истины)."""
    is_management = rank in MANAGEMENT_RANKS          # офицер+
    is_leadership = rank in ('leader', 'colonel')     # лидер/полковник
    return {
        'rank': rank,
        'canViewAll': True,
        'canEditActivity': is_management,
        'canEditActivityData': is_management,
        'canManageMembers': is_management,
        'canPromote': is_management,
        'canDemote': is_management,
        'canKick': is_management,
        'canInvite': is_management,
        'canCreateEvents': is_management,
        'canCreateMaps': is_management,
        'canDeleteAnyBuild': is_management,
        'canDeleteAnyGallery': is_management,
        'canEditClan': is_leadership,
        'canCreateRoles': is_leadership,
        'canTransferLeadership': rank == 'leader',
        'maxPromoteRank': 'leader' if rank == 'leader' else ('officer' if is_management else None),
    }


def status_from_row(present, excused) -> int:
    """kv_attendance (present/excused) -> статус сайта (0/1/2)."""
    if excused == EXCUSED_MARK:
        return 2
    return 1 if present else 0


def attendance_fields_from_status(status):
    """статус сайта -> (present, excused) для kv_attendance."""
    if status == 1:
        return 1, None
    if status == 2:
        return 0, EXCUSED_MARK
    return 0, None  # status == 0


# ============================================
# СЕРВЕР
# ============================================

class WebServer:
    def __init__(self, bot):
        self.bot = bot
        self.app = web.Application()
        self.runner = None
        self.site = None
        self._setup_routes()

    def _setup_routes(self):
        app = self.app
        app.router.add_get('/', self.handle_index)
        app.router.add_get('/login', self.handle_login_link)

        app.router.add_post('/api/auth/redeem', self.api_redeem)
        app.router.add_post('/api/logout', self.api_logout)
        app.router.add_get('/api/me', self.api_me)

        app.router.add_get('/api/members', self.api_members_list)
        app.router.add_post('/api/members', self.api_member_add)
        app.router.add_patch('/api/members/{id}', self.api_member_update)
        app.router.add_delete('/api/members/{id}', self.api_member_delete)

        app.router.add_get('/api/activity', self.api_activity)
        app.router.add_post('/api/attendance', self.api_attendance_set)
        app.router.add_post('/api/dates', self.api_date_add)
        app.router.add_delete('/api/dates/{date}', self.api_date_delete)

        app.router.add_get('/api/kv', self.api_kv)
        app.router.add_get('/api/kv/day', self.api_kv_day)
        app.router.add_post('/api/kv/cancel', self.api_kv_cancel)
        app.router.add_delete('/api/kv/cancel/{date}', self.api_kv_restore)

        app.router.add_get('/api/squads', self.api_squads_list)
        app.router.add_post('/api/squads', self.api_squad_create)
        app.router.add_patch('/api/squads/{id}', self.api_squad_update)
        app.router.add_delete('/api/squads/{id}', self.api_squad_delete)

        app.router.add_get('/api/store/{key}', self.api_store_get)
        app.router.add_post('/api/store/{key}', self.api_store_put)
        app.router.add_put('/api/store/{key}', self.api_store_put)

        # Статика (favicon, ассеты сайта, если появятся)
        if WEB_DIR.exists():
            app.router.add_static('/static/', WEB_DIR, show_index=False)

    # ----- жизненный цикл -----

    async def start(self, host: str, port: int):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, host, port)
        await self.site.start()
        logger.info(f"🌐 Веб-сервер слушает http://{host}:{port}")

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
            logger.info("🌐 Веб-сервер остановлен")

    # ----- аутентификация -----

    async def _session(self, request):
        token = request.cookies.get(SESSION_COOKIE)
        return await self.bot.get_session(token)

    async def _require_session(self, request):
        session = await self._session(request)
        if not session:
            raise web.HTTPUnauthorized(text=json.dumps({'error': 'unauthorized'}),
                                       content_type='application/json')
        return session

    def _rank_of(self, session) -> str:
        return self.bot.get_rank_for_discord_id(session['guild_id'], session['discord_id'])

    async def _require_management(self, request):
        session = await self._require_session(request)
        rank = self._rank_of(session)
        if rank not in MANAGEMENT_RANKS:
            raise web.HTTPForbidden(text=json.dumps({'error': 'forbidden', 'rank': rank}),
                                    content_type='application/json')
        return session

    def _set_session_cookie(self, response, token):
        response.set_cookie(
            SESSION_COOKIE, token,
            max_age=30 * 24 * 3600,
            httponly=True, samesite='Lax', path='/'
        )

    # ----- страницы -----

    async def handle_index(self, request):
        index = WEB_DIR / 'index.html'
        if not index.exists():
            return web.Response(text="index.html не найден в bot/web/", status=500)
        # SPA не кэшируем, чтобы обновления подхватывались сразу (без F5-кэша)
        return web.FileResponse(index, headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
        })

    async def handle_login_link(self, request):
        """Магическая ссылка из бота: /login?code=XXXXXX -> сессия -> редирект на /."""
        code = request.query.get('code', '')
        token = await self.bot.redeem_login_code(code)
        if not token:
            # Невалидный/просроченный код — отправляем на сайт с пометкой
            raise web.HTTPFound('/?login=invalid')
        response = web.HTTPFound('/')
        self._set_session_cookie(response, token)
        return response

    # ----- API: auth -----

    async def api_redeem(self, request):
        data = await request.json()
        token = await self.bot.redeem_login_code(data.get('code', ''))
        if not token:
            return json_response({'error': 'invalid_code'}, status=400)
        me = await self._build_me(await self.bot.get_session(token))
        response = json_response({'ok': True, 'me': me})
        self._set_session_cookie(response, token)
        return response

    async def api_logout(self, request):
        token = request.cookies.get(SESSION_COOKIE)
        await self.bot.delete_session(token)
        response = json_response({'ok': True})
        response.del_cookie(SESSION_COOKIE, path='/')
        return response

    async def _build_me(self, session):
        if not session:
            return None
        guild_id = session['guild_id']
        discord_id = session['discord_id']
        rank = self.bot.get_rank_for_discord_id(guild_id, discord_id)
        # Позывной — из ростера, иначе из Discord
        callsign = None
        async with self.bot.db.execute(
            "SELECT callsign FROM clan_members WHERE guild_id = ? AND discord_id = ?",
            (guild_id, discord_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                callsign = row[0]
        if not callsign:
            guild = self.bot.get_guild(guild_id)
            member = guild.get_member(discord_id) if guild else None
            callsign = member.display_name if member else str(discord_id)
        return {
            'discord_id': str(discord_id),
            'guild_id': str(guild_id),
            'callsign': callsign,
            'rank': rank,
            'rank_name': RANK_NAMES.get(rank, rank),
            'rank_icon': RANK_ICONS.get(rank, ''),
            'permissions': permissions_for(rank),
        }

    async def api_me(self, request):
        session = await self._session(request)
        if not session:
            return json_response({'error': 'unauthorized'}, status=401)
        return json_response(await self._build_me(session))

    # ----- API: участники -----

    async def _roster(self, guild_id):
        members = []
        async with self.bot.db.execute(
            "SELECT id, discord_id, callsign, rank, notes, manual FROM clan_members "
            "WHERE guild_id = ? ORDER BY callsign COLLATE NOCASE",
            (guild_id,)
        ) as cursor:
            async for row in cursor:
                members.append({
                    'id': row[0],
                    'discord_id': str(row[1]) if row[1] is not None else None,
                    'callsign': row[2],
                    'rank': row[3] or DEFAULT_RANK,
                    'rank_name': RANK_NAMES.get(row[3] or DEFAULT_RANK),
                    'notes': row[4],
                    'manual': bool(row[5]),
                })
        return members

    async def api_members_list(self, request):
        session = await self._require_session(request)
        return json_response({'members': await self._roster(session['guild_id'])})

    async def api_member_add(self, request):
        # Участники берутся только из Discord (бот синхронизирует автоматически).
        # Ручное добавление запрещено.
        await self._require_management(request)
        return json_response(
            {'error': 'manual_add_disabled',
             'message': 'Участники добавляются автоматически из Discord. Вручную добавить нельзя.'},
            status=400)

    async def _get_member(self, guild_id, member_id):
        async with self.bot.db.execute(
            "SELECT id, discord_id, callsign, rank, manual FROM clan_members WHERE guild_id = ? AND id = ?",
            (guild_id, member_id)
        ) as cursor:
            return await cursor.fetchone()

    async def api_member_update(self, request):
        session = await self._require_management(request)
        guild_id = session['guild_id']
        member_id = int(request.match_info['id'])
        row = await self._get_member(guild_id, member_id)
        if not row:
            return json_response({'error': 'not_found'}, status=404)
        _, discord_id, callsign, rank, manual = row
        data = await request.json()

        if 'callsign' in data:
            new_callsign = (data['callsign'] or '').strip()
            if new_callsign:
                await self.bot.db.execute(
                    "UPDATE clan_members SET callsign = ?, callsign_custom = 1, updated_at = CURRENT_TIMESTAMP "
                    "WHERE guild_id = ? AND id = ?",
                    (new_callsign, guild_id, member_id)
                )

        if 'notes' in data:
            await self.bot.db.execute(
                "UPDATE clan_members SET notes = ?, updated_at = CURRENT_TIMESTAMP WHERE guild_id = ? AND id = ?",
                (data['notes'], guild_id, member_id)
            )

        if 'rank' in data:
            new_rank = normalize_rank(data['rank'])
            if not new_rank:
                return json_response({'error': 'bad_rank'}, status=400)
            if discord_id is not None:
                # Для Discord-участника звание — через механизм бота (как !setrank)
                await self.bot.set_member_rank(guild_id, discord_id, new_rank, session['discord_id'])
            else:
                await self.bot.db.execute(
                    "UPDATE clan_members SET rank = ?, updated_at = CURRENT_TIMESTAMP WHERE guild_id = ? AND id = ?",
                    (new_rank, guild_id, member_id)
                )

        await self.bot.db.commit()
        return json_response({'ok': True, 'members': await self._roster(guild_id)})

    async def api_member_delete(self, request):
        session = await self._require_management(request)
        guild_id = session['guild_id']
        member_id = int(request.match_info['id'])
        row = await self._get_member(guild_id, member_id)
        if not row:
            return json_response({'error': 'not_found'}, status=404)
        if not row[4]:  # manual == 0 -> Discord-участник, удалять нельзя
            return json_response({'error': 'cannot_delete_discord_member'}, status=400)
        await self.bot.db.execute(
            "DELETE FROM clan_members WHERE guild_id = ? AND id = ?", (guild_id, member_id)
        )
        await self.bot.db.commit()
        return json_response({'ok': True, 'members': await self._roster(guild_id)})

    # ----- API: активность (посещаемость КВ) -----

    async def _get_dates(self, guild_id):
        """Все даты-колонки: объединение дат kv_attendance и вручную добавленных."""
        dates = set()
        async with self.bot.db.execute(
            "SELECT DISTINCT date FROM kv_attendance WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            async for row in cursor:
                if row[0]:
                    dates.add(row[0])
        async with self.bot.db.execute(
            "SELECT value_json FROM web_kv WHERE guild_id = ? AND key = 'activity_dates'", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                try:
                    for d in json.loads(row[0]):
                        dates.add(d)
                except Exception:
                    pass
        return sorted(dates)

    async def _manual_attendance(self, guild_id):
        async with self.bot.db.execute(
            "SELECT value_json FROM web_kv WHERE guild_id = ? AND key = 'manual_attendance'", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            try:
                return json.loads(row[0])
            except Exception:
                return {}
        return {}

    async def api_activity(self, request):
        session = await self._require_session(request)
        guild_id = session['guild_id']
        dates = await self._get_dates(guild_id)
        roster = await self._roster(guild_id)

        # Записи Discord-участников из kv_attendance
        att_by_uid = {}
        async with self.bot.db.execute(
            "SELECT user_id, date, present, excused FROM kv_attendance WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            async for uid, date, present, excused in cursor:
                att_by_uid.setdefault(uid, {})[date] = status_from_row(present, excused)

        manual_att = await self._manual_attendance(guild_id)

        for m in roster:
            records = {}
            if m['discord_id'] is not None:
                records = att_by_uid.get(int(m['discord_id']), {})
            else:
                records = manual_att.get(str(m['id']), {})
            m['records'] = records

        return json_response({'dates': dates, 'members': roster})

    async def api_attendance_set(self, request):
        session = await self._require_management(request)
        guild_id = session['guild_id']
        data = await request.json()
        member_id = data.get('member_id')
        date = (data.get('date') or '').strip()
        status = data.get('status')  # 0/1/2 или None (очистить)
        if not member_id or not date:
            return json_response({'error': 'member_and_date_required'}, status=400)

        row = await self._get_member(guild_id, int(member_id))
        if not row:
            return json_response({'error': 'member_not_found'}, status=404)
        _, discord_id, callsign, rank, manual = row

        if discord_id is not None:
            await self._set_kv_attendance(guild_id, discord_id, callsign, rank, date, status)
        else:
            await self._set_manual_attendance(guild_id, int(member_id), date, status)

        # Гарантируем, что дата отображается как колонка
        if status is not None:
            await self._ensure_date(guild_id, date)

        return json_response({'ok': True})

    async def _ensure_date(self, guild_id, date):
        dates = await self._stored_dates(guild_id)
        if date not in dates:
            dates.append(date)
            await self._kv_put(guild_id, 'activity_dates', sorted(dates))

    async def _set_kv_attendance(self, guild_id, discord_id, callsign, rank, date, status):
        schedule = self.bot.get_schedule_for_date(date)
        schedule_id = schedule['id'] if schedule else 0
        kv_time = f"{schedule['start_time']}-{schedule['end_time']}" if schedule else None

        if status is None:
            await self.bot.db.execute(
                "DELETE FROM kv_attendance WHERE guild_id = ? AND date = ? AND user_id = ? AND schedule_id = ?",
                (guild_id, date, discord_id, schedule_id)
            )
            await self.bot.db.commit()
            return

        present, excused = attendance_fields_from_status(status)
        await self.bot.db.execute('''
            INSERT INTO kv_attendance
            (guild_id, schedule_id, date, kv_time, user_id, discord_name, role_type, present, excused)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, date, user_id, schedule_id) DO UPDATE SET
                present = excluded.present,
                excused = excluded.excused,
                discord_name = excluded.discord_name,
                role_type = excluded.role_type,
                updated_at = CURRENT_TIMESTAMP
        ''', (guild_id, schedule_id, date, kv_time, discord_id, callsign, rank, present, excused))
        await self.bot.db.commit()

    async def _set_manual_attendance(self, guild_id, member_id, date, status):
        store = await self._manual_attendance(guild_id)
        key = str(member_id)
        member_records = store.get(key, {})
        if status is None:
            member_records.pop(date, None)
        else:
            member_records[date] = status
        store[key] = member_records
        await self._kv_put(guild_id, 'manual_attendance', store)

    async def api_date_add(self, request):
        session = await self._require_management(request)
        guild_id = session['guild_id']
        data = await request.json()
        date = (data.get('date') or '').strip()
        if not date:
            return json_response({'error': 'date_required'}, status=400)
        dates = await self._stored_dates(guild_id)
        if date not in dates:
            dates.append(date)
            await self._kv_put(guild_id, 'activity_dates', sorted(dates))
        return json_response({'ok': True, 'dates': await self._get_dates(guild_id)})

    async def api_date_delete(self, request):
        session = await self._require_management(request)
        guild_id = session['guild_id']
        date = request.match_info['date']
        # Убираем из ручного списка дат
        dates = await self._stored_dates(guild_id)
        if date in dates:
            dates.remove(date)
            await self._kv_put(guild_id, 'activity_dates', dates)
        # И чистим записи посещаемости за эту дату
        await self.bot.db.execute(
            "DELETE FROM kv_attendance WHERE guild_id = ? AND date = ?", (guild_id, date)
        )
        await self.bot.db.commit()
        manual = await self._manual_attendance(guild_id)
        changed = False
        for recs in manual.values():
            if date in recs:
                recs.pop(date, None)
                changed = True
        if changed:
            await self._kv_put(guild_id, 'manual_attendance', manual)
        return json_response({'ok': True, 'dates': await self._get_dates(guild_id)})

    async def _stored_dates(self, guild_id):
        async with self.bot.db.execute(
            "SELECT value_json FROM web_kv WHERE guild_id = ? AND key = 'activity_dates'", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            try:
                return list(json.loads(row[0]))
            except Exception:
                return []
        return []

    # ----- API: расписание КВ (авто) + отмена дня -----

    async def api_kv(self, request):
        session = await self._require_session(request)
        guild_id = session['guild_id']
        try:
            days = int(request.query.get('days', '21'))
        except ValueError:
            days = 21
        days = max(1, min(days, 60))

        cancels = await self.bot.get_kv_cancellations(guild_id)
        today = datetime.now(self.bot.timezone).date()
        events = []
        for i in range(days):
            d = today + timedelta(days=i)
            ds = d.strftime('%Y-%m-%d')
            sched = self.bot.get_schedule_for_date(ds)
            if not sched:
                continue
            c = cancels.get(ds)
            events.append({
                'date': ds,
                'weekday': d.weekday(),
                'name': self.bot.get_event_name(guild_id, ds, sched),
                'start': sched['start_time'],
                'end': sched['end_time'],
                'cancelled': bool(c),
                'reason': (c or {}).get('reason', ''),
            })
        return json_response({'weekly': self.bot.get_weekly_schedule(), 'events': events})

    async def api_kv_cancel(self, request):
        session = await self._require_management(request)
        guild_id = session['guild_id']
        data = await request.json()
        date = (data.get('date') or '').strip()
        reason = (data.get('reason') or '').strip()
        if not date:
            return json_response({'error': 'date_required'}, status=400)
        await self.bot.set_kv_cancelled(guild_id, date, reason, session['discord_id'])
        sched = self.bot.get_schedule_for_date(date)
        ev = self.bot.get_event_name(guild_id, date, sched) if sched else 'КВ'
        actor = self.bot.web_display_name(guild_id, session['discord_id'])
        desc = f"**{ev}** на **{date}** отменено."
        if reason:
            desc += f"\n📋 Причина: {reason}"
        desc += f"\n👤 Отменил: {actor}"
        await self.bot.announce_kv_event(guild_id, "❌ КВ отменено", desc)
        return json_response({'ok': True})

    async def api_kv_restore(self, request):
        session = await self._require_management(request)
        guild_id = session['guild_id']
        date = request.match_info['date']
        existed = await self.bot.clear_kv_cancelled(guild_id, date)
        if existed:
            sched = self.bot.get_schedule_for_date(date)
            ev = self.bot.get_event_name(guild_id, date, sched) if sched else 'КВ'
            actor = self.bot.web_display_name(guild_id, session['discord_id'])
            await self.bot.announce_kv_event(
                guild_id, "✅ КВ восстановлено",
                f"**{ev}** на **{date}** снова в силе.\n👤 Вернул: {actor}")
        return json_response({'ok': True})

    async def api_kv_day(self, request):
        """Детальный отчёт за день по этапам (как в боте)."""
        session = await self._require_session(request)
        guild_id = session['guild_id']
        date = (request.query.get('date') or '').strip()
        if not date:
            return json_response({'error': 'date_required'}, status=400)
        report = await self.bot.get_kv_day_report(guild_id, date)
        if report is None:
            return json_response({'error': 'bad_date'}, status=400)
        # звания -> имена для удобства фронта
        for m in report['members']:
            m['rank_name'] = RANK_NAMES.get(m['rank'], m['rank'])
        report['cancelled'] = await self.bot.is_kv_cancelled(guild_id, report['date'])
        return json_response(report)

    # ----- API: отряды -----

    async def _squads(self, guild_id):
        return await self.bot.web_kv_get(guild_id, 'squads', []) or []

    async def _valid_member_ids(self, guild_id):
        """Множество id участников ростера (только из Discord можно добавлять)."""
        ids = set()
        async with self.bot.db.execute(
            "SELECT id FROM clan_members WHERE guild_id = ? AND discord_id IS NOT NULL", (guild_id,)
        ) as cursor:
            async for row in cursor:
                ids.add(row[0])
        return ids

    async def api_squads_list(self, request):
        session = await self._require_session(request)
        return json_response({'squads': await self._squads(session['guild_id'])})

    def _clean_squad_members(self, raw, valid_ids):
        out = []
        for x in (raw or []):
            try:
                mid = int(x)
            except (ValueError, TypeError):
                continue
            if mid in valid_ids and mid not in out:
                out.append(mid)
        return out[:5]  # максимум 5 человек

    def _ids_in_other_squads(self, squads, keep_id):
        """Кто уже состоит в другом отряде (игрок может быть только в одном)."""
        taken = set()
        for sq in squads:
            if sq.get('id') != keep_id:
                for m in sq.get('members', []):
                    taken.add(m)
        return taken

    async def api_squad_create(self, request):
        session = await self._require_management(request)
        guild_id = session['guild_id']
        data = await request.json()
        name = (data.get('name') or '').strip()
        if not name:
            return json_response({'error': 'name_required'}, status=400)
        valid = await self._valid_member_ids(guild_id)
        members = self._clean_squad_members(data.get('members'), valid)
        squads = await self._squads(guild_id)
        # боец, уже состоящий в другом отряде, добавлен быть не может
        taken = self._ids_in_other_squads(squads, None)
        members = [m for m in members if m not in taken]
        new_id = (max([s.get('id', 0) for s in squads], default=0) + 1)
        squads.append({'id': new_id, 'name': name, 'members': members})
        await self.bot.web_kv_set(guild_id, 'squads', squads)
        return json_response({'ok': True, 'squads': squads})

    async def api_squad_update(self, request):
        session = await self._require_management(request)
        guild_id = session['guild_id']
        sid = int(request.match_info['id'])
        data = await request.json()
        squads = await self._squads(guild_id)
        sq = next((s for s in squads if s.get('id') == sid), None)
        if not sq:
            return json_response({'error': 'not_found'}, status=404)
        if 'name' in data:
            nm = (data['name'] or '').strip()
            if nm:
                sq['name'] = nm
        if 'members' in data:
            valid = await self._valid_member_ids(guild_id)
            cleaned = self._clean_squad_members(data['members'], valid)
            taken = self._ids_in_other_squads(squads, sid)
            sq['members'] = [m for m in cleaned if m not in taken]
        await self.bot.web_kv_set(guild_id, 'squads', squads)
        return json_response({'ok': True, 'squads': squads})

    async def api_squad_delete(self, request):
        session = await self._require_management(request)
        guild_id = session['guild_id']
        sid = int(request.match_info['id'])
        squads = [s for s in await self._squads(guild_id) if s.get('id') != sid]
        await self.bot.web_kv_set(guild_id, 'squads', squads)
        return json_response({'ok': True, 'squads': squads})

    # ----- API: общее KV-хранилище -----

    async def _kv_put(self, guild_id, key, value):
        await self.bot.db.execute('''
            INSERT INTO web_kv (guild_id, key, value_json, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id, key) DO UPDATE SET
                value_json = excluded.value_json, updated_at = CURRENT_TIMESTAMP
        ''', (guild_id, key, json.dumps(value, ensure_ascii=False)))
        await self.bot.db.commit()

    async def api_store_get(self, request):
        session = await self._require_session(request)
        guild_id = session['guild_id']
        key = request.match_info['key']
        async with self.bot.db.execute(
            "SELECT value_json FROM web_kv WHERE guild_id = ? AND key = ?", (guild_id, key)
        ) as cursor:
            row = await cursor.fetchone()
        value = json.loads(row[0]) if row else None
        return json_response({'key': key, 'value': value})

    async def api_store_put(self, request):
        # Запись разделов сайта — для офицеров+ (просмотр доступен всем вошедшим)
        session = await self._require_management(request)
        guild_id = session['guild_id']
        key = request.match_info['key']
        data = await request.json()
        await self._kv_put(guild_id, key, data.get('value'))
        return json_response({'ok': True})
