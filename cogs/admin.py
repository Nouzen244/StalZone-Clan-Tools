"""
Модуль администрирования v3.0
Настройка VC для КВ, роли, расписание
"""

import discord
from discord.ext import commands
import logging
from datetime import datetime
import json
from ranks import RANK_ORDER, RANK_NAMES, RANK_ICONS, normalize_rank, rank_label

logger = logging.getLogger('Admin')


def admin_only():
    async def predicate(ctx: commands.Context) -> bool:
        return ctx.author.guild_permissions.administrator
    return commands.check(predicate)


def officer_or_higher():
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.guild_permissions.administrator:
            return True
        return await ctx.bot.has_permission(ctx.author, 'officer')
    return commands.check(predicate)


class AdminCog(commands.Cog, name="Администрирование"):
    """Команды настройки бота"""
    
    def __init__(self, bot):
        self.bot = bot
        logger.info("⚙️ AdminCog v3.0 загружен")
    
    # ========================================
    # МАСТЕР НАСТРОЙКИ
    # ========================================
    
    @commands.command(name='setup')
    @admin_only()
    async def setup_wizard(self, ctx: commands.Context):
        """Мастер начальной настройки"""
        
        embed = discord.Embed(
            title="🔧 Настройка StalZone Clan Bot v3.2",
            color=discord.Color.blue()
        )
        
        settings = self.bot.guild_settings.get(ctx.guild.id, {})
        
        # Статус настроек
        lines = []
        
        # КВ VC
        kv_vc = settings.get('kv_vc_channel_id')
        if kv_vc:
            ch = ctx.guild.get_channel(kv_vc)
            lines.append(f"✅ VC для КВ: {ch.mention if ch else 'Удалён'}")
        else:
            lines.append("❌ VC для КВ: Не настроен")
        
        report_ch = settings.get('report_channel_id')
        if report_ch:
            ch = ctx.guild.get_channel(report_ch)
            lines.append(f"✅ Канал отчётов: {ch.mention if ch else 'Удалён'}")
        else:
            lines.append("⚠️ Канал отчётов: Не настроен")
        
        # Роли
        roles = self.bot.guild_roles.get(ctx.guild.id, {})
        if roles:
            role_count = sum(len(v) for v in roles.values())
            lines.append(f"✅ Роли: {role_count} настроено")
        else:
            lines.append("❌ Роли: Не настроены")
        
        # Расписание КВ (фиксированное)
        lines.append("📌 Расписание КВ: фиксированное (`!schedule`)")
        
        embed.add_field(name="📋 Текущий статус", value="\n".join(lines), inline=False)
        
        embed.add_field(
            name="📝 Шаги настройки",
            value="""
**1. Настройте канал КВ:**
`!setvc kv #канал-кв` — голосовой канал клановых войн
`!setchannel report #отчёты` — канал КВ-уведомлений

**2. Звания (роль Discord → звание):**
`!setrole лидер @Лидер`
`!setrole офицер @Офицер`
`!setrole рядовой @Рядовой`
_или вручную:_ `!setrank @игрок полковник`

**3. Расписание КВ — фиксированное:**
`!schedule` — посмотреть расписание (изменить нельзя)
            """,
            inline=False
        )
        
        await ctx.send(embed=embed)
    
    # ========================================
    # НАСТРОЙКА ГОЛОСОВЫХ КАНАЛОВ
    # ========================================
    
    @commands.command(name='setvc')
    @admin_only()
    async def set_voice_channel(self, ctx: commands.Context, vc_type: str, channel: discord.VoiceChannel):
        """
        Устанавливает голосовой канал КВ
        !setvc kv #канал - для КВ
        """
        guild_id = ctx.guild.id
        vc_type = vc_type.lower()

        type_map = {
            'kv': ('kv_vc_channel_id', 'КВ'),
            'кв': ('kv_vc_channel_id', 'КВ'),
        }

        if vc_type not in type_map:
            await ctx.send("❌ Неверный тип!\nИспользуйте: `kv`")
            return
        
        db_field, display_name = type_map[vc_type]
        
        try:
            await self.bot.db.execute(f'''
                INSERT INTO guild_settings (guild_id, {db_field})
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    {db_field} = excluded.{db_field},
                    updated_at = CURRENT_TIMESTAMP
            ''', (guild_id, channel.id))
            await self.bot.db.commit()
            
            if guild_id not in self.bot.guild_settings:
                self.bot.guild_settings[guild_id] = {}
            self.bot.guild_settings[guild_id][db_field] = channel.id
            
            embed = discord.Embed(
                title=f"✅ Канал для {display_name} установлен!",
                description=f"**Канал:** {channel.mention}\n"
                           f"**ID:** `{channel.id}`",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Ошибка: {e}")
    
    # ========================================
    # НАСТРОЙКА ТЕКСТОВЫХ КАНАЛОВ
    # ========================================
    
    @commands.command(name='setchannel')
    @admin_only()
    async def set_channel(self, ctx: commands.Context, channel_type: str, channel: discord.TextChannel):
        """
        Устанавливает текстовый канал отчётов (КВ-уведомления)
        !setchannel report #канал - для отчётов
        """
        guild_id = ctx.guild.id

        type_map = {
            'report': ('report_channel_id', 'отчётов'),
            'отчёт': ('report_channel_id', 'отчётов'),
            'reports': ('report_channel_id', 'отчётов'),
        }

        channel_type = channel_type.lower()
        if channel_type not in type_map:
            await ctx.send("❌ Неверный тип!\nИспользуйте: `report`")
            return
        
        db_field, display_name = type_map[channel_type]
        
        try:
            await self.bot.db.execute(f'''
                INSERT INTO guild_settings (guild_id, {db_field})
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    {db_field} = excluded.{db_field},
                    updated_at = CURRENT_TIMESTAMP
            ''', (guild_id, channel.id))
            await self.bot.db.commit()
            
            if guild_id not in self.bot.guild_settings:
                self.bot.guild_settings[guild_id] = {}
            self.bot.guild_settings[guild_id][db_field] = channel.id
            
            embed = discord.Embed(
                title=f"✅ Канал для {display_name} установлен!",
                description=f"**Канал:** {channel.mention}",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Ошибка: {e}")
    
    # ========================================
    # УПРАВЛЕНИЕ РОЛЯМИ
    # ========================================
    
    async def _bind_rank_role(self, ctx: commands.Context, rank: str, role: discord.Role):
        """Привязывает роль Discord к званию (общая логика для всех команд)."""
        guild_id = ctx.guild.id
        try:
            # Проверяем дубликат
            async with self.bot.db.execute(
                'SELECT id FROM guild_roles WHERE guild_id = ? AND role_type = ? AND role_id = ?',
                (guild_id, rank, role.id)
            ) as cursor:
                if await cursor.fetchone():
                    await ctx.send(f"⚠️ Роль {role.mention} уже привязана к званию {RANK_NAMES[rank]}!")
                    return

            await self.bot.db.execute('''
                INSERT INTO guild_roles (guild_id, role_type, role_id, role_name, created_by)
                VALUES (?, ?, ?, ?, ?)
            ''', (guild_id, rank, role.id, role.name, ctx.author.id))
            await self.bot.db.commit()

            # Кэш
            self.bot.guild_roles.setdefault(guild_id, {}).setdefault(rank, []).append(role.id)

            embed = discord.Embed(
                title=f"{RANK_ICONS[rank]} Звание привязано!",
                description=f"Роль {role.mention} → **{RANK_NAMES[rank]}**\n"
                           f"👥 Участников: {len(role.members)}",
                color=role.color if role.color != discord.Color.default() else discord.Color.green()
            )
            await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(f"❌ Ошибка: {e}")

    @commands.command(name='setrole')
    @admin_only()
    async def set_role(self, ctx: commands.Context, role_type: str, role: discord.Role):
        """
        Привязывает роль Discord к званию (универсальная команда).
        Звания: лидер, полковник, офицер, сержант, боец, рядовой
        !setrole офицер @Офицеры
        """
        normalized = normalize_rank(role_type)
        if not normalized:
            types = ", ".join(RANK_NAMES[r] for r in RANK_ORDER)
            await ctx.send(f"❌ Неверное звание!\nДоступные: {types}")
            return
        await self._bind_rank_role(ctx, normalized, role)

    # Отдельная команда на каждое звание: !setleader @роль, !setcolonel @роль и т.д.
    @commands.command(name='setleader', aliases=['setлидер', 'лидер'])
    @admin_only()
    async def set_leader(self, ctx: commands.Context, role: discord.Role):
        """!setleader @роль — роль Discord → Лидер"""
        await self._bind_rank_role(ctx, 'leader', role)

    @commands.command(name='setcolonel', aliases=['setполковник', 'полковник'])
    @admin_only()
    async def set_colonel(self, ctx: commands.Context, role: discord.Role):
        """!setcolonel @роль — роль Discord → Полковник"""
        await self._bind_rank_role(ctx, 'colonel', role)

    @commands.command(name='setofficer', aliases=['setофицер', 'офицер'])
    @admin_only()
    async def set_officer(self, ctx: commands.Context, role: discord.Role):
        """!setofficer @роль — роль Discord → Офицер"""
        await self._bind_rank_role(ctx, 'officer', role)

    @commands.command(name='setsergeant', aliases=['setсержант', 'сержант'])
    @admin_only()
    async def set_sergeant(self, ctx: commands.Context, role: discord.Role):
        """!setsergeant @роль — роль Discord → Сержант"""
        await self._bind_rank_role(ctx, 'sergeant', role)

    @commands.command(name='setfighter', aliases=['setбоец', 'боец'])
    @admin_only()
    async def set_fighter(self, ctx: commands.Context, role: discord.Role):
        """!setfighter @роль — роль Discord → Боец"""
        await self._bind_rank_role(ctx, 'fighter', role)

    @commands.command(name='setprivate', aliases=['setрядовой', 'рядовой'])
    @admin_only()
    async def set_private(self, ctx: commands.Context, role: discord.Role):
        """!setprivate @роль — роль Discord → Рядовой"""
        await self._bind_rank_role(ctx, 'private', role)
    
    @commands.command(name='removerole')
    @admin_only()
    async def remove_role(self, ctx: commands.Context, role_type: str, role: discord.Role):
        """Убирает привязку роли Discord к званию"""
        guild_id = ctx.guild.id

        normalized = normalize_rank(role_type)
        if not normalized:
            await ctx.send("❌ Неверное звание!")
            return

        result = await self.bot.db.execute(
            'DELETE FROM guild_roles WHERE guild_id = ? AND role_type = ? AND role_id = ?',
            (guild_id, normalized, role.id)
        )
        await self.bot.db.commit()

        if result.rowcount == 0:
            await ctx.send("⚠️ Привязка не найдена!")
            return

        # Кэш
        roles = self.bot.guild_roles.get(guild_id, {})
        if normalized in roles and role.id in roles[normalized]:
            roles[normalized].remove(role.id)

        await ctx.send(f"✅ Привязка роли {role.mention} к званию {RANK_NAMES[normalized]} убрана!")

    @commands.command(name='roles', aliases=['роли'])
    @admin_only()
    async def show_roles(self, ctx: commands.Context):
        """Показывает иерархию званий и привязанные роли Discord"""
        guild_id = ctx.guild.id
        roles = self.bot.guild_roles.get(guild_id, {})

        embed = discord.Embed(
            title="🪖 Иерархия званий StalZone",
            description="Звание = ручное назначение (`!setrank`) → роль Discord → Рядовой",
            color=discord.Color.blue()
        )

        for rank in RANK_ORDER:
            role_ids = roles.get(rank, [])
            if role_ids:
                mentions = []
                for rid in role_ids:
                    role = ctx.guild.get_role(rid)
                    if role:
                        mentions.append(f"{role.mention} ({len(role.members)})")
                    else:
                        mentions.append(f"~~Удалена~~ (ID: {rid})")
                value = "\n".join(mentions)
            else:
                value = "_роль Discord не привязана_"
            embed.add_field(name=rank_label(rank), value=value, inline=False)

        embed.set_footer(text="!setrole <звание> @роль  ·  !setrank @игрок <звание>")
        await ctx.send(embed=embed)

    # ========================================
    # РУЧНЫЕ ЗВАНИЯ УЧАСТНИКОВ
    # ========================================

    @commands.command(name='setrank', aliases=['присвоить'])
    @admin_only()
    async def set_rank(self, ctx: commands.Context, member: discord.Member, *, rank: str):
        """
        Назначает звание участнику вручную (приоритетнее ролей Discord).
        !setrank @игрок полковник
        """
        normalized = normalize_rank(rank)
        if not normalized:
            types = ", ".join(RANK_NAMES[r] for r in RANK_ORDER)
            await ctx.send(f"❌ Неверное звание!\nДоступные: {types}")
            return

        await self.bot.set_member_rank(ctx.guild.id, member.id, normalized, ctx.author.id)

        embed = discord.Embed(
            title="✅ Звание назначено!",
            description=f"{member.mention} → {rank_label(normalized)}",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @commands.command(name='delrank', aliases=['снять'])
    @admin_only()
    async def del_rank(self, ctx: commands.Context, member: discord.Member):
        """Убирает ручное звание (звание снова определяется по ролям Discord)"""
        removed = await self.bot.clear_member_rank(ctx.guild.id, member.id)
        if not removed:
            await ctx.send("⚠️ У участника не было ручного звания.")
            return
        rank = self.bot.get_member_role_type(member)
        await ctx.send(f"✅ Ручное звание у {member.mention} убрано. Сейчас: {rank_label(rank)} (по ролям Discord).")

    @commands.command(name='rank', aliases=['ранг'])
    async def show_rank(self, ctx: commands.Context, member: discord.Member = None):
        """Показывает звание участника"""
        target = member or ctx.author
        rank = self.bot.get_member_role_type(target)
        manual = f"{ctx.guild.id}:{target.id}" in self.bot.member_ranks

        embed = discord.Embed(
            title=f"Звание: {target.display_name}",
            description=f"{rank_label(rank)}\n_{'назначено вручную' if manual else 'по ролям Discord'}_",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await ctx.send(embed=embed)
    
    # ========================================
    # РАСПИСАНИЕ КВ
    # ========================================
    
    @commands.group(name='schedule', aliases=['sched', 'расп'], invoke_without_command=True)
    async def schedule(self, ctx: commands.Context):
        """Показывает фиксированное расписание КВ"""
        await ctx.invoke(self.bot.get_command('schedule list'))

    @schedule.command(name='list', aliases=['список'])
    async def schedule_list(self, ctx: commands.Context):
        """Показывает фиксированное расписание КВ"""
        guild_id = ctx.guild.id
        schedules = self.bot.get_guild_schedules(guild_id)
        day_names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

        embed = discord.Embed(
            title="⚔️ Расписание КВ (фиксированное)",
            description="Расписание постоянное — его нельзя изменить или дополнить.",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )

        # Группируем дни по событию (название + время)
        grouped = {}
        order = []
        for s in schedules:
            key = (s['name'], s['start_time'], s['end_time'])
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].extend(s['days_of_week'])

        for (name, start, end) in order:
            days = sorted(grouped[(name, start, end)])
            days_display = ', '.join(day_names[d] for d in days)
            embed.add_field(
                name=name,
                value=f"📅 {days_display}\n⏰ **{start} - {end}**",
                inline=False
            )

        # Текущее КВ
        current = self.bot.get_current_kv_schedule(guild_id)
        if current:
            today_str = datetime.now(self.bot.timezone).strftime('%Y-%m-%d')
            event_name = self.bot.get_event_name(guild_id, today_str, current)
            embed.add_field(
                name="🔴 СЕЙЧАС ИДЁТ",
                value=f"**{event_name}** до {current['end_time']}",
                inline=False
            )

        await ctx.send(embed=embed)
    
    # ========================================
    # СТАТУС
    # ========================================
    
    @commands.command(name='status', aliases=['статус'])
    async def status(self, ctx: commands.Context):
        """Показывает статус бота"""
        guild_id = ctx.guild.id
        settings = self.bot.guild_settings.get(guild_id, {})
        
        embed = discord.Embed(
            title="📊 Статус бота",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # КВ
        kv_vc_id = settings.get('kv_vc_channel_id')
        if kv_vc_id:
            kv_vc = ctx.guild.get_channel(kv_vc_id)
            if kv_vc:
                members = [m for m in kv_vc.members if not m.bot]
                embed.add_field(
                    name="⚔️ Канал КВ",
                    value=f"{kv_vc.mention}\n👥 Сейчас: {len(members)}",
                    inline=True
                )
        else:
            embed.add_field(name="⚔️ Канал КВ", value="❌ Не настроен", inline=True)
        
        # Текущее КВ
        current_kv = self.bot.get_current_kv_schedule(guild_id)
        if current_kv:
            today_str = datetime.now(self.bot.timezone).strftime('%Y-%m-%d')
            event_name = self.bot.get_event_name(guild_id, today_str, current_kv)
            embed.add_field(
                name="🔴 КВ ИДЁТ",
                value=f"**{event_name}**\nДо {current_kv['end_time']}",
                inline=True
            )
        else:
            embed.add_field(name="📅 КВ", value="Расписание фиксированное (`!schedule`)", inline=True)
        
        # Активные сессии
        active = sum(1 for k in self.bot.all_voice_sessions if k.startswith(f"{guild_id}:"))
        embed.add_field(name="🎤 Активных сессий", value=str(active), inline=True)

        await ctx.send(embed=embed)

    # ========================================
    # ВХОД НА САЙТ
    # ========================================

    @commands.command(name='site', aliases=['вход', 'login', 'сайт'])
    @commands.guild_only()
    async def site_login(self, ctx: commands.Context):
        """Выдаёт одноразовую ссылку для входа на сайт клана (в ЛС)."""
        if not getattr(self.bot, 'web_server', None):
            await ctx.send("⚠️ Веб-сайт сейчас не запущен (WEB_ENABLED отключён).")
            return

        # Гарантируем, что пользователь есть в ростере
        await self.bot.upsert_clan_member(ctx.author)

        code = await self.bot.create_login_code(ctx.guild.id, ctx.author.id)
        public = await self.bot.get_public_base_url()  # WEB_PUBLIC_URL → ngrok (авто) → localhost
        link = f"{public}/login?code={code}"

        rank = self.bot.get_member_role_type(ctx.author)
        dm = discord.Embed(
            title="🔑 Вход на сайт StalZone",
            description=(
                f"Нажми на ссылку, чтобы войти на сайт клана:\n\n"
                f"🔗 [Войти на сайт]({link})\n\n"
                f"Или открой сайт и введи код вручную:\n"
                f"**Код:** `{code}`\n\n"
                f"Твоё звание на сайте: **{rank_label(rank)}**"
            ),
            color=discord.Color.green()
        )
        dm.set_footer(text="Ссылка и код одноразовые, действуют 10 минут. Никому их не передавай!")

        try:
            await ctx.author.send(embed=dm)
            await ctx.send("📩 Отправил тебе в ЛС ссылку для входа на сайт.")
        except discord.Forbidden:
            await ctx.send(
                "⚠️ Не могу написать тебе в ЛС. Открой личные сообщения для участников "
                "сервера (Настройки приватности) и повтори `!site`. "
                "Код входа не публикуется в канале в целях безопасности."
            )


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
