"""
Модуль администрирования v3.0
Настройка VC для КВ, собраний, роли, расписание
"""

import discord
from discord.ext import commands
import logging
from datetime import datetime
import json

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
            title="🔧 Настройка STALCRAFT Clan Bot v3.0",
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
        
        # Собрания VC
        meeting_vc = settings.get('meeting_vc_channel_id')
        if meeting_vc:
            ch = ctx.guild.get_channel(meeting_vc)
            lines.append(f"✅ VC для собраний: {ch.mention if ch else 'Удалён'}")
        else:
            lines.append("⚠️ VC для собраний: Не настроен")
        
        # Каналы
        log_ch = settings.get('log_channel_id')
        if log_ch:
            ch = ctx.guild.get_channel(log_ch)
            lines.append(f"✅ Канал логов: {ch.mention if ch else 'Удалён'}")
        else:
            lines.append("⚠️ Канал логов: Не настроен")
        
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
        
        # Расписание КВ
        schedules = self.bot.guild_schedules.get(ctx.guild.id, [])
        if schedules:
            lines.append(f"✅ Расписание КВ: {len(schedules)} слотов")
        else:
            lines.append("⚠️ Расписание КВ: Не настроено")
        
        embed.add_field(name="📋 Текущий статус", value="\n".join(lines), inline=False)
        
        embed.add_field(
            name="📝 Шаги настройки",
            value="""
**1. Настройте голосовые каналы:**
`!setvc kv #канал-кв` — для клановых войн
`!setvc meeting #канал-собраний` — для собраний

**2. Настройте текстовые каналы:**
`!setchannel log #логи` — логи входов/выходов
`!setchannel report #отчёты` — уведомления

**3. Настройте роли:**
`!setrole leader @Глава`
`!setrole officer @Офицер`
`!setrole member @Рядовой`

**4. Добавьте расписание КВ:**
`!schedule add 20:00-21:30 КВ пн,ср,пт`
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
        Устанавливает голосовой канал
        !setvc kv #канал - для КВ
        !setvc meeting #канал - для собраний
        """
        guild_id = ctx.guild.id
        vc_type = vc_type.lower()
        
        type_map = {
            'kv': ('kv_vc_channel_id', 'КВ'),
            'кв': ('kv_vc_channel_id', 'КВ'),
            'meeting': ('meeting_vc_channel_id', 'собраний'),
            'собрание': ('meeting_vc_channel_id', 'собраний'),
            'собрания': ('meeting_vc_channel_id', 'собраний'),
        }
        
        if vc_type not in type_map:
            await ctx.send("❌ Неверный тип!\nИспользуйте: `kv` или `meeting`")
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
        Устанавливает текстовый канал
        !setchannel log #канал - для логов
        !setchannel report #канал - для отчётов
        """
        guild_id = ctx.guild.id
        
        type_map = {
            'log': ('log_channel_id', 'логов'),
            'лог': ('log_channel_id', 'логов'),
            'logs': ('log_channel_id', 'логов'),
            'report': ('report_channel_id', 'отчётов'),
            'отчёт': ('report_channel_id', 'отчётов'),
            'reports': ('report_channel_id', 'отчётов'),
        }
        
        channel_type = channel_type.lower()
        if channel_type not in type_map:
            await ctx.send("❌ Неверный тип!\nИспользуйте: `log` или `report`")
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
    
    @commands.command(name='setrole')
    @admin_only()
    async def set_role(self, ctx: commands.Context, role_type: str, role: discord.Role):
        """
        Добавляет роль в систему
        Типы: leader, officer, member, special, recruit
        """
        guild_id = ctx.guild.id
        
        type_info = {
            'leader': ('leader', 'Глава', '👑'),
            'глава': ('leader', 'Глава', '👑'),
            'лидер': ('leader', 'Глава', '👑'),
            'officer': ('officer', 'Офицер', '⚔️'),
            'офицер': ('officer', 'Офицер', '⚔️'),
            'зам': ('officer', 'Офицер', '⚔️'),
            'member': ('member', 'Рядовой', '🎖️'),
            'рядовой': ('member', 'Рядовой', '🎖️'),
            'клан': ('member', 'Рядовой', '🎖️'),
            'special': ('special', 'Особая', '⭐'),
            'особый': ('special', 'Особая', '⭐'),
            'recruit': ('recruit', 'Рекрут', '🆕'),
            'рекрут': ('recruit', 'Рекрут', '🆕'),
        }
        
        role_type = role_type.lower()
        if role_type not in type_info:
            types = ", ".join(['leader', 'officer', 'member', 'special', 'recruit'])
            await ctx.send(f"❌ Неверный тип!\nДоступные: `{types}`")
            return
        
        normalized, display_name, emoji = type_info[role_type]
        
        try:
            # Проверяем дубликат
            async with self.bot.db.execute(
                'SELECT id FROM guild_roles WHERE guild_id = ? AND role_type = ? AND role_id = ?',
                (guild_id, normalized, role.id)
            ) as cursor:
                if await cursor.fetchone():
                    await ctx.send(f"⚠️ Роль {role.mention} уже добавлена!")
                    return
            
            await self.bot.db.execute('''
                INSERT INTO guild_roles (guild_id, role_type, role_id, role_name, created_by)
                VALUES (?, ?, ?, ?, ?)
            ''', (guild_id, normalized, role.id, role.name, ctx.author.id))
            await self.bot.db.commit()
            
            # Кэш
            if guild_id not in self.bot.guild_roles:
                self.bot.guild_roles[guild_id] = {}
            if normalized not in self.bot.guild_roles[guild_id]:
                self.bot.guild_roles[guild_id][normalized] = []
            self.bot.guild_roles[guild_id][normalized].append(role.id)
            
            embed = discord.Embed(
                title=f"{emoji} Роль добавлена!",
                description=f"**{role.mention}** → **{display_name}**\n"
                           f"👥 Участников: {len(role.members)}",
                color=role.color if role.color != discord.Color.default() else discord.Color.green()
            )
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Ошибка: {e}")
    
    @commands.command(name='removerole')
    @admin_only()
    async def remove_role(self, ctx: commands.Context, role_type: str, role: discord.Role):
        """Удаляет роль из системы"""
        guild_id = ctx.guild.id
        
        type_map = {
            'leader': 'leader', 'глава': 'leader',
            'officer': 'officer', 'офицер': 'officer',
            'member': 'member', 'рядовой': 'member',
            'special': 'special', 'особый': 'special',
            'recruit': 'recruit', 'рекрут': 'recruit',
        }
        
        normalized = type_map.get(role_type.lower())
        if not normalized:
            await ctx.send("❌ Неверный тип роли!")
            return
        
        result = await self.bot.db.execute(
            'DELETE FROM guild_roles WHERE guild_id = ? AND role_type = ? AND role_id = ?',
            (guild_id, normalized, role.id)
        )
        await self.bot.db.commit()
        
        if result.rowcount == 0:
            await ctx.send("⚠️ Роль не найдена в системе!")
            return
        
        # Кэш
        if guild_id in self.bot.guild_roles and normalized in self.bot.guild_roles[guild_id]:
            if role.id in self.bot.guild_roles[guild_id][normalized]:
                self.bot.guild_roles[guild_id][normalized].remove(role.id)
        
        await ctx.send(f"✅ Роль {role.mention} удалена!")
    
    @commands.command(name='roles', aliases=['роли'])
    @admin_only()
    async def show_roles(self, ctx: commands.Context):
        """Показывает настроенные роли"""
        guild_id = ctx.guild.id
        roles = self.bot.guild_roles.get(guild_id, {})
        
        if not roles:
            await ctx.send("⚠️ Роли не настроены!\nИспользуйте `!setrole <тип> @роль`")
            return
        
        embed = discord.Embed(title="👥 Роли клана", color=discord.Color.blue())
        
        type_info = {
            'leader': '👑 Глава',
            'officer': '⚔️ Офицеры',
            'member': '🎖️ Рядовые',
            'special': '⭐ Особые',
            'recruit': '🆕 Рекруты',
        }
        
        for role_type, role_ids in roles.items():
            if not role_ids:
                continue
            
            title = type_info.get(role_type, role_type)
            mentions = []
            for rid in role_ids:
                role = ctx.guild.get_role(rid)
                if role:
                    mentions.append(f"{role.mention} ({len(role.members)})")
                else:
                    mentions.append(f"~~Удалена~~ (ID: {rid})")
            
            embed.add_field(name=title, value="\n".join(mentions), inline=False)
        
        await ctx.send(embed=embed)
    
    # ========================================
    # РАСПИСАНИЕ КВ
    # ========================================
    
    @commands.group(name='schedule', aliases=['sched', 'расп'], invoke_without_command=True)
    @officer_or_higher()
    async def schedule(self, ctx: commands.Context):
        """Группа команд расписания КВ"""
        await ctx.invoke(self.bot.get_command('schedule list'))
    
    @schedule.command(name='add')
    @officer_or_higher()
    async def schedule_add(self, ctx: commands.Context, time_range: str, *, name: str = "КВ"):
        """
        Добавляет расписание КВ
        !schedule add 20:00-21:30 КВ пн,ср,пт
        """
        guild_id = ctx.guild.id
        
        try:
            if '-' not in time_range:
                raise ValueError("Формат: HH:MM-HH:MM")
            
            start_str, end_str = time_range.split('-')
            datetime.strptime(start_str.strip(), '%H:%M')
            datetime.strptime(end_str.strip(), '%H:%M')
            
            start_time = start_str.strip()
            end_time = end_str.strip()
            
        except ValueError:
            await ctx.send("❌ Неверный формат времени!\nИспользуйте: `HH:MM-HH:MM`")
            return
        
        # Парсим дни недели
        days_map = {
            'пн': 0, 'вт': 1, 'ср': 2, 'чт': 3, 'пт': 4, 'сб': 5, 'вс': 6,
            'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6,
        }
        
        days_of_week = list(range(7))
        
        name_parts = name.rsplit(' ', 1)
        if len(name_parts) == 2:
            possible_days = name_parts[1].lower().replace(' ', '')
            if ',' in possible_days or possible_days in days_map:
                try:
                    if possible_days.replace(',', '').isdigit():
                        days_of_week = [int(d) for d in possible_days.split(',')]
                    else:
                        days_of_week = [days_map[d.strip()] for d in possible_days.split(',')]
                    name = name_parts[0]
                except:
                    pass
        
        days_str = ','.join(map(str, days_of_week))
        
        try:
            cursor = await self.bot.db.execute('''
                INSERT INTO kv_schedule (guild_id, name, start_time, end_time, days_of_week, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (guild_id, name, start_time, end_time, days_str, ctx.author.id))
            await self.bot.db.commit()
            
            schedule_id = cursor.lastrowid
            
            # Кэш
            if guild_id not in self.bot.guild_schedules:
                self.bot.guild_schedules[guild_id] = []
            
            self.bot.guild_schedules[guild_id].append({
                'id': schedule_id,
                'name': name,
                'start_time': start_time,
                'end_time': end_time,
                'days_of_week': days_of_week,
                'notify_before': 15
            })
            
            day_names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
            days_display = ', '.join([day_names[d] for d in days_of_week])
            
            embed = discord.Embed(
                title="✅ Расписание КВ добавлено!",
                color=discord.Color.green()
            )
            embed.add_field(name="📋 Название", value=name, inline=True)
            embed.add_field(name="🕐 Время", value=f"{start_time} - {end_time}", inline=True)
            embed.add_field(name="📅 Дни", value=days_display, inline=True)
            embed.add_field(name="🆔 ID", value=str(schedule_id), inline=True)
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Ошибка: {e}")
    
    @schedule.command(name='list', aliases=['список'])
    async def schedule_list(self, ctx: commands.Context):
        """Показывает расписание КВ"""
        guild_id = ctx.guild.id
        schedules = self.bot.guild_schedules.get(guild_id, [])
        
        embed = discord.Embed(
            title="⚔️ Расписание КВ",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        
        if not schedules:
            embed.description = "Расписание не настроено.\n\n`!schedule add 20:00-21:30 КВ`"
        else:
            day_names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
            
            for s in schedules:
                days_display = ', '.join([day_names[d] for d in s['days_of_week']])
                embed.add_field(
                    name=f"#{s['id']} | {s['name']}",
                    value=f"⏰ **{s['start_time']} - {s['end_time']}**\n📅 {days_display}",
                    inline=False
                )
        
        # Текущее КВ
        current = self.bot.get_current_kv_schedule(guild_id)
        if current:
            embed.add_field(
                name="🔴 СЕЙЧАС ИДЁТ",
                value=f"**{current['name']}** до {current['end_time']}",
                inline=False
            )
        
        await ctx.send(embed=embed)
    
    @schedule.command(name='remove', aliases=['delete', 'удалить'])
    @officer_or_higher()
    async def schedule_remove(self, ctx: commands.Context, schedule_id: int):
        """Удаляет расписание КВ"""
        guild_id = ctx.guild.id
        
        result = await self.bot.db.execute(
            'DELETE FROM kv_schedule WHERE id = ? AND guild_id = ?',
            (schedule_id, guild_id)
        )
        await self.bot.db.commit()
        
        if result.rowcount == 0:
            await ctx.send("❌ Расписание не найдено!")
            return
        
        # Кэш
        if guild_id in self.bot.guild_schedules:
            self.bot.guild_schedules[guild_id] = [
                s for s in self.bot.guild_schedules[guild_id] if s['id'] != schedule_id
            ]
        
        await ctx.send(f"✅ Расписание #{schedule_id} удалено!")
    
    @schedule.command(name='clear', aliases=['очистить'])
    @admin_only()
    async def schedule_clear(self, ctx: commands.Context):
        """Удаляет всё расписание КВ"""
        guild_id = ctx.guild.id
        
        await self.bot.db.execute('DELETE FROM kv_schedule WHERE guild_id = ?', (guild_id,))
        await self.bot.db.commit()
        
        self.bot.guild_schedules[guild_id] = []
        
        await ctx.send("✅ Расписание КВ очищено!")
    
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
            embed.add_field(
                name="🔴 КВ ИДЁТ",
                value=f"**{current_kv['name']}**\nДо {current_kv['end_time']}",
                inline=True
            )
        else:
            schedules = self.bot.guild_schedules.get(guild_id, [])
            if schedules:
                embed.add_field(name="📅 КВ", value=f"{len(schedules)} в расписании", inline=True)
            else:
                embed.add_field(name="📅 КВ", value="Расписание пусто", inline=True)
        
        # Активные сессии
        active = sum(1 for k in self.bot.all_voice_sessions if k.startswith(f"{guild_id}:"))
        embed.add_field(name="🎤 Активных сессий", value=str(active), inline=True)
        
        # Активные рейды
        async with self.bot.db.execute(
            "SELECT COUNT(*) FROM raids WHERE guild_id = ? AND status = 'active'",
            (guild_id,)
        ) as cursor:
            raids = (await cursor.fetchone())[0]
        embed.add_field(name="⚔️ Активных рейдов", value=str(raids), inline=True)
        
        # Собрание
        if guild_id in self.bot.active_meetings:
            embed.add_field(name="📋 Собрание", value="🔴 Идёт", inline=True)
        
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
