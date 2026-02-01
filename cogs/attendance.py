"""
Модуль посещаемости v3.2
КВ работает ТОЛЬКО по расписанию
Личная статистика для всех
"""

import discord
from discord.ext import commands, tasks
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from pathlib import Path

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

logger = logging.getLogger('Attendance')


def format_date(date_obj=None, date_str=None) -> str:
    if date_obj:
        return date_obj.strftime('%d-%m-%Y')
    if date_str:
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            return dt.strftime('%d-%m-%Y')
        except:
            return date_str
    return datetime.now().strftime('%d-%m-%Y')

def parse_date(date_str: str) -> Optional[datetime]:
    for fmt in ('%d-%m-%Y', '%d.%m.%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None

def date_for_db(date_obj=None) -> str:
    if date_obj:
        return date_obj.strftime('%Y-%m-%d')
    return datetime.now().strftime('%Y-%m-%d')

def format_duration(seconds: int) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 0:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"


def officer_or_higher():
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.guild_permissions.administrator:
            return True
        return await ctx.bot.has_permission(ctx.author, 'officer')
    return commands.check(predicate)


# ============================================
# VIEWS ДЛЯ РЕДАКТИРОВАНИЯ КВ
# ============================================

class KVMemberSelect(discord.ui.Select):
    """Select Menu для выбора отсутствующего игрока"""
    
    def __init__(self, bot, absent_members: List, date: str, guild_id: int):
        self.bot = bot
        self.date = date
        self.guild_id = guild_id
        
        options = []
        for member, data, role_type in absent_members[:25]:
            excused = data.get('excused')
            label = member.display_name[:100]
            emoji = "📝" if excused == 'У/П' else "❌"
            
            options.append(discord.SelectOption(
                label=label,
                value=str(member.id),
                description=f"Роль: {role_type}" + (" [У/П]" if excused else ""),
                emoji=emoji
            ))
        
        super().__init__(
            placeholder="📋 Выберите игрока для обработки...",
            options=options if options else [discord.SelectOption(label="Нет данных", value="none")],
            min_values=1,
            max_values=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return
        
        member_id = int(self.values[0])
        member = interaction.guild.get_member(member_id)
        
        if not member:
            await interaction.response.send_message("❌ Пользователь не найден!", ephemeral=True)
            return
        
        embed = discord.Embed(
            title=f"📝 Обработка: {member.display_name}",
            description="Пользователь **отсутствовал** на КВ?",
            color=discord.Color.orange()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        
        view = KVStep1View(self.bot, member, self.date, self.guild_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class KVStep1View(discord.ui.View):
    """Шаг 1: Подтверждение отсутствия"""
    
    def __init__(self, bot, member: discord.Member, date: str, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.member = member
        self.date = date
        self.guild_id = guild_id
    
    @discord.ui.button(label="Да, отсутствовал", style=discord.ButtonStyle.danger, emoji="❌")
    async def confirm_absent(self, interaction: discord.Interaction, button):
        embed = discord.Embed(
            title=f"📝 {self.member.display_name}",
            description="Причина **уважительная**?",
            color=discord.Color.orange()
        )
        embed.set_thumbnail(url=self.member.display_avatar.url)
        
        view = KVStep2View(self.bot, self.member, self.date, self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="Нет, был на КВ", style=discord.ButtonStyle.success, emoji="✅")
    async def mark_present(self, interaction: discord.Interaction, button):
        await self.bot.db.execute('''
            UPDATE kv_attendance 
            SET present = 1, excused = NULL, reason = 'Исправлено вручную', processed_by = ?
            WHERE guild_id = ? AND date = ? AND user_id = ?
        ''', (interaction.user.id, self.guild_id, self.date, self.member.id))
        await self.bot.db.commit()
        
        embed = discord.Embed(
            title="✅ Исправлено!",
            description=f"**{self.member.display_name}** отмечен как **присутствующий**",
            color=discord.Color.green()
        )
        embed.add_field(name="📅 Дата", value=format_date(date_str=self.date), inline=True)
        embed.add_field(name="👤 Изменил", value=interaction.user.mention, inline=True)
        
        await interaction.response.edit_message(embed=embed, view=None)
        
        # Лог
        log_embed = discord.Embed(
            title="📝 Изменение посещаемости КВ",
            description=f"**{self.member.display_name}** → ✅ Присутствовал",
            color=discord.Color.blue()
        )
        log_embed.add_field(name="Изменил", value=interaction.user.mention)
        log_embed.add_field(name="Дата КВ", value=format_date(date_str=self.date))
        await self.bot.send_log(interaction.guild, log_embed)


class KVStep2View(discord.ui.View):
    """Шаг 2: Уважительная причина?"""
    
    def __init__(self, bot, member: discord.Member, date: str, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.member = member
        self.date = date
        self.guild_id = guild_id
    
    @discord.ui.button(label="Да, уважительная", style=discord.ButtonStyle.success, emoji="📝")
    async def excused_yes(self, interaction: discord.Interaction, button):
        embed = discord.Embed(
            title=f"📝 {self.member.display_name}",
            description="Выберите **причину** отсутствия:",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=self.member.display_avatar.url)
        
        view = KVStep3View(self.bot, self.member, self.date, self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="Нет, неуважительная", style=discord.ButtonStyle.danger, emoji="⛔")
    async def excused_no(self, interaction: discord.Interaction, button):
        await self.bot.db.execute('''
            UPDATE kv_attendance 
            SET present = 0, excused = NULL, reason = 'Неуважительный пропуск', processed_by = ?
            WHERE guild_id = ? AND date = ? AND user_id = ?
        ''', (interaction.user.id, self.guild_id, self.date, self.member.id))
        await self.bot.db.commit()
        
        embed = discord.Embed(
            title="⛔ Неуважительный пропуск",
            description=f"**{self.member.display_name}** — пропуск без уважительной причины",
            color=discord.Color.red()
        )
        embed.add_field(name="📅 Дата", value=format_date(date_str=self.date), inline=True)
        embed.add_field(name="👤 Обработал", value=interaction.user.mention, inline=True)
        
        await interaction.response.edit_message(embed=embed, view=None)


class KVStep3View(discord.ui.View):
    """Шаг 3: Выбор причины"""
    
    def __init__(self, bot, member: discord.Member, date: str, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.member = member
        self.date = date
        self.guild_id = guild_id
    
    @discord.ui.select(
        placeholder="📋 Выберите причину...",
        options=[
            discord.SelectOption(label="Болезнь", value="Болезнь", emoji="🏥"),
            discord.SelectOption(label="Работа", value="Работа", emoji="💼"),
            discord.SelectOption(label="Семья", value="Семья", emoji="👨‍👩‍👧"),
            discord.SelectOption(label="Технические проблемы", value="Тех.проблемы", emoji="🔧"),
            discord.SelectOption(label="Учёба", value="Учёба", emoji="📚"),
            discord.SelectOption(label="В отъезде", value="Отъезд", emoji="✈️"),
            discord.SelectOption(label="Играл оффлайн", value="Оффлайн", emoji="🎮"),
            discord.SelectOption(label="Другое", value="Другое", emoji="📝"),
        ]
    )
    async def select_reason(self, interaction: discord.Interaction, select):
        reason = select.values[0]
        
        await self.bot.db.execute('''
            UPDATE kv_attendance 
            SET present = 0, excused = 'У/П', reason = ?, processed_by = ?
            WHERE guild_id = ? AND date = ? AND user_id = ?
        ''', (reason, interaction.user.id, self.guild_id, self.date, self.member.id))
        await self.bot.db.commit()
        
        embed = discord.Embed(
            title="📝 У/П — Уважительная причина",
            description=f"**{self.member.display_name}**",
            color=discord.Color.blue()
        )
        embed.add_field(name="📋 Причина", value=reason, inline=True)
        embed.add_field(name="📅 Дата", value=format_date(date_str=self.date), inline=True)
        embed.add_field(name="👤 Обработал", value=interaction.user.mention, inline=True)
        
        await interaction.response.edit_message(embed=embed, view=None)
    
    @discord.ui.button(label="⏭️ Пропустить", style=discord.ButtonStyle.secondary, row=1)
    async def skip(self, interaction: discord.Interaction, button):
        await self.bot.db.execute('''
            UPDATE kv_attendance 
            SET present = 0, excused = 'У/П', reason = 'Не указана', processed_by = ?
            WHERE guild_id = ? AND date = ? AND user_id = ?
        ''', (interaction.user.id, self.guild_id, self.date, self.member.id))
        await self.bot.db.commit()
        
        embed = discord.Embed(
            title="📝 У/П — Причина не указана",
            description=f"**{self.member.display_name}** отмечен с уважительной причиной",
            color=discord.Color.blue()
        )
        
        await interaction.response.edit_message(embed=embed, view=None)


class KVReportView(discord.ui.View):
    """View для отчёта КВ с кнопками редактирования"""
    
    def __init__(self, bot, absent_members: List, date: str, guild_id: int, is_live: bool = False):
        super().__init__(timeout=600)
        self.bot = bot
        self.absent = absent_members
        self.date = date
        self.guild_id = guild_id
        self.is_live = is_live
        
        # Добавляем Select Menu если есть отсутствующие
        if absent_members:
            self.add_item(KVMemberSelect(bot, absent_members, date, guild_id))
    
    @discord.ui.button(label="🔄 Обновить", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button):
        await interaction.response.defer()
        # Вызываем команду !kv заново
        ctx = await self.bot.get_context(interaction.message)
        if self.is_live:
            await ctx.invoke(self.bot.get_command('kv'))
        else:
            await ctx.invoke(self.bot.get_command('kv'), date=format_date(date_str=self.date))


class KVEditView(discord.ui.View):
    """View для редактирования КВ"""
    
    def __init__(self, bot, date: str, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.date = date
        self.guild_id = guild_id
    
    @discord.ui.button(label="📝 Редактировать участника", style=discord.ButtonStyle.primary)
    async def edit_member(self, interaction: discord.Interaction, button):
        modal = KVEditModal(self.bot, self.date, self.guild_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="✅ Отметить всех присутствующими", style=discord.ButtonStyle.success)
    async def mark_all_present(self, interaction: discord.Interaction, button):
        await self.bot.db.execute('''
            UPDATE kv_attendance SET present = 1, processed_by = ?
            WHERE guild_id = ? AND date = ?
        ''', (interaction.user.id, self.guild_id, self.date))
        await self.bot.db.commit()
        
        await interaction.response.send_message("✅ Все участники отмечены как присутствующие!", ephemeral=True)


class KVEditModal(discord.ui.Modal, title="📝 Редактировать участника"):
    """Модальное окно для поиска и редактирования"""
    
    username = discord.ui.TextInput(
        label="Никнейм или ID участника",
        placeholder="Введите ник или Discord ID...",
        required=True,
        max_length=100
    )
    
    def __init__(self, bot, date: str, guild_id: int):
        super().__init__()
        self.bot = bot
        self.date = date
        self.guild_id = guild_id
    
    async def on_submit(self, interaction: discord.Interaction):
        search = self.username.value.strip()
        
        # Поиск участника
        member = None
        
        if search.isdigit():
            member = interaction.guild.get_member(int(search))
        
        if not member:
            search_lower = search.lower()
            for m in interaction.guild.members:
                if search_lower in m.display_name.lower() or search_lower in m.name.lower():
                    member = m
                    break
        
        if not member:
            await interaction.response.send_message(f"❌ Участник **{search}** не найден!", ephemeral=True)
            return
        
        # Показываем меню редактирования
        embed = discord.Embed(
            title=f"📝 Редактирование: {member.display_name}",
            description="Выберите новый статус:",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        
        view = KVDirectEditView(self.bot, member, self.date, self.guild_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class KVDirectEditView(discord.ui.View):
    """Прямое редактирование статуса"""
    
    def __init__(self, bot, member: discord.Member, date: str, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.member = member
        self.date = date
        self.guild_id = guild_id
    
    @discord.ui.button(label="✅ Присутствовал", style=discord.ButtonStyle.success)
    async def mark_present(self, interaction: discord.Interaction, button):
        await self.bot.db.execute('''
            UPDATE kv_attendance 
            SET present = 1, excused = NULL, reason = 'Изменено вручную', processed_by = ?
            WHERE guild_id = ? AND date = ? AND user_id = ?
        ''', (interaction.user.id, self.guild_id, self.date, self.member.id))
        await self.bot.db.commit()
        
        await interaction.response.edit_message(
            content=f"✅ **{self.member.display_name}** → Присутствовал",
            embed=None, view=None
        )
    
    @discord.ui.button(label="❌ Отсутствовал", style=discord.ButtonStyle.danger)
    async def mark_absent(self, interaction: discord.Interaction, button):
        await self.bot.db.execute('''
            UPDATE kv_attendance 
            SET present = 0, excused = NULL, reason = NULL, processed_by = ?
            WHERE guild_id = ? AND date = ? AND user_id = ?
        ''', (interaction.user.id, self.guild_id, self.date, self.member.id))
        await self.bot.db.commit()
        
        await interaction.response.edit_message(
            content=f"❌ **{self.member.display_name}** → Отсутствовал",
            embed=None, view=None
        )
    
    @discord.ui.button(label="📝 У/П (уважительная)", style=discord.ButtonStyle.primary)
    async def mark_excused(self, interaction: discord.Interaction, button):
        embed = discord.Embed(
            title=f"📝 {self.member.display_name}",
            description="Выберите причину:",
            color=discord.Color.blue()
        )
        view = KVStep3View(self.bot, self.member, self.date, self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)


# ============================================
# ОСНОВНОЙ КОГ
# ============================================

class AttendanceCog(commands.Cog, name="Посещаемость"):
    """Команды КВ, собраний и отчётности"""
    
    def __init__(self, bot):
        self.bot = bot
        self.meeting_reminder.start()
        logger.info("📊 AttendanceCog v3.2 загружен")
    
    def cog_unload(self):
        self.meeting_reminder.cancel()
    
    # ========================================
    # КВ - ТОЛЬКО ПО РАСПИСАНИЮ
    # ========================================
    
    @commands.command(name='kv', aliases=['кв'])
    @officer_or_higher()
    async def kv_report(self, ctx: commands.Context, date: str = None):
        """
        Отчёт по КВ
        !kv - текущее КВ (только если идёт по расписанию)
        !kv 17-01-2026 - отчёт за дату
        """
        guild = ctx.guild
        guild_id = guild.id
        settings = self.bot.guild_settings.get(guild_id, {})
        
        # Проверяем настройки
        kv_vc_id = settings.get('kv_vc_channel_id')
        if not kv_vc_id:
            await ctx.send("❌ Канал КВ не настроен!\n`!setvc kv #канал`")
            return
        
        kv_vc = guild.get_channel(kv_vc_id)
        if not kv_vc:
            await ctx.send("❌ Канал КВ не найден!")
            return
        
        now = datetime.now(self.bot.timezone)
        
        # Без даты = текущее КВ
        if not date:
            current_kv = self.bot.get_current_kv_schedule(guild_id)
            
            if not current_kv:
                # Проверяем есть ли вообще расписание
                schedules = self.bot.guild_schedules.get(guild_id, [])
                if not schedules:
                    await ctx.send("❌ Расписание КВ не настроено!\n`!schedule add 20:00-21:30 КВ`")
                else:
                    # Показываем когда следующее КВ
                    day_names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
                    next_kv = "Не определено"
                    
                    for sched in schedules:
                        days = ', '.join([day_names[d] for d in sched['days_of_week']])
                        next_kv = f"{sched['name']} в {sched['start_time']} ({days})"
                        break
                    
                    embed = discord.Embed(
                        title="⚠️ Сейчас нет активного КВ",
                        description=f"**Следующее КВ:** {next_kv}\n\n"
                                   f"Используйте `!kv DD-MM-YYYY` для просмотра отчёта за дату",
                        color=discord.Color.orange()
                    )
                    await ctx.send(embed=embed)
                return
            
            # КВ сейчас идёт - показываем live статус
            await self._show_live_kv(ctx, current_kv, kv_vc, now)
        
        else:
            # С датой = отчёт за прошедшее КВ
            target_date = parse_date(date)
            if not target_date:
                await ctx.send("❌ Неверный формат даты! Используйте: `DD-MM-YYYY`")
                return
            
            await self._show_kv_report(ctx, target_date, kv_vc)
    
    async def _show_live_kv(self, ctx, schedule: dict, kv_vc: discord.VoiceChannel, now: datetime):
        """Показывает live статус текущего КВ"""
        guild = ctx.guild
        guild_id = guild.id
        date_str = date_for_db(now)
        
        # Получаем всех участников сервера (не ботов)
        all_members = [m for m in guild.members if not m.bot]
        
        # Кто сейчас в VC
        members_in_vc = {m.id for m in kv_vc.members if not m.bot}
        
        # Создаём/обновляем записи в БД
        for member in all_members:
            is_present = member.id in members_in_vc
            role_type = self.bot.get_member_role_type(member)
            
            await self.bot.db.execute('''
                INSERT INTO kv_attendance 
                (guild_id, schedule_id, date, kv_time, user_id, discord_name, role_type, present)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, date, user_id, schedule_id) DO UPDATE SET
                    present = MAX(kv_attendance.present, excluded.present),
                    role_type = excluded.role_type
            ''', (
                guild_id, schedule['id'], date_str,
                f"{schedule['start_time']}-{schedule['end_time']}",
                member.id, member.display_name, role_type,
                1 if is_present else 0
            ))
        
        await self.bot.db.commit()
        
        # Формируем отчёт
        present = []
        absent = []
        
        role_icons = {
            'leader': '👑', 'officer': '⚔️', 'special': '⭐',
            'member': '🎖️', 'recruit': '🆕'
        }
        
        for member in all_members:
            role_type = self.bot.get_member_role_type(member)
            role_icon = role_icons.get(role_type, '')
            
            if member.id in members_in_vc:
                # Получаем время в VC
                session_key = f"{guild_id}:{member.id}"
                session = self.bot.all_voice_sessions.get(session_key)
                if session and session.get('channel_id') == kv_vc.id:
                    duration = (now - session['join_time']).total_seconds()
                    present.append((member, role_icon, role_type, int(duration)))
                else:
                    present.append((member, role_icon, role_type, 0))
            else:
                absent.append((member, role_icon, role_type))
        
        # Сортировка
        present.sort(key=lambda x: (-x[3], x[0].display_name.lower()))
        absent.sort(key=lambda x: x[0].display_name.lower())
        
        # Embed
        embed = discord.Embed(
            title=f"⚔️ КВ: {schedule['name']} (LIVE)",
            description=f"🕐 **Время:** {schedule['start_time']} - {schedule['end_time']}\n"
                       f"🎤 **Канал:** {kv_vc.mention}",
            color=discord.Color.red(),
            timestamp=now
        )
        
        # Присутствующие
        if present:
            lines = []
            for member, icon, role_type, duration in present[:15]:
                time_str = f" ({format_duration(duration)})" if duration > 0 else ""
                lines.append(f"🟢{icon} {member.display_name}{time_str}")
            
            if len(present) > 15:
                lines.append(f"... и ещё {len(present) - 15}")
            
            embed.add_field(
                name=f"✅ В канале ({len(present)})",
                value="\n".join(lines) or "—",
                inline=True
            )
        else:
            embed.add_field(name="✅ В канале (0)", value="Пусто", inline=True)
        
        # Отсутствующие
        if absent:
            lines = []
            for member, icon, role_type in absent[:15]:
                lines.append(f"❌{icon} {member.display_name}")
            
            if len(absent) > 15:
                lines.append(f"... и ещё {len(absent) - 15}")
            
            embed.add_field(
                name=f"❌ Отсутствуют ({len(absent)})",
                value="\n".join(lines) or "—",
                inline=True
            )
        
        # Статистика
        total = len(all_members)
        present_pct = (len(present) / total * 100) if total > 0 else 0
        
        embed.add_field(
            name="📊 Статистика",
            value=f"**Всего:** {total}\n"
                  f"**Присутствует:** {len(present)} ({present_pct:.0f}%)\n"
                  f"**Отсутствует:** {len(absent)}",
            inline=False
        )
        
        embed.set_footer(text=f"Обновлено • {ctx.author.display_name}")
        
        # View с кнопками
        absent_data = [(m, {}, rt) for m, _, rt in absent]
        view = KVReportView(self.bot, absent_data, date_str, guild_id, is_live=True)
        
        await ctx.send(embed=embed, view=view)
    
    async def _show_kv_report(self, ctx, target_date: datetime, kv_vc: discord.VoiceChannel):
        """Показывает отчёт за прошедшую дату"""
        guild = ctx.guild
        guild_id = guild.id
        date_str = date_for_db(target_date)
        
        # Проверяем было ли КВ в эту дату
        schedules = self.bot.guild_schedules.get(guild_id, [])
        day_of_week = target_date.weekday()
        
        matching_schedule = None
        for sched in schedules:
            if day_of_week in sched['days_of_week']:
                matching_schedule = sched
                break
        
        if not matching_schedule:
            day_names = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            await ctx.send(f"⚠️ На **{format_date(target_date)}** ({day_names[day_of_week]}) не было КВ по расписанию!")
            return
        
        kv_time = f"{matching_schedule['start_time']}-{matching_schedule['end_time']}"
        
        # Получаем данные из БД
        attendance_data = {}
        async with self.bot.db.execute('''
            SELECT user_id, present, excused, reason, vc_time_seconds, role_type
            FROM kv_attendance
            WHERE guild_id = ? AND date = ?
        ''', (guild_id, date_str)) as cursor:
            async for row in cursor:
                attendance_data[row[0]] = {
                    'present': row[1],
                    'excused': row[2],
                    'reason': row[3],
                    'vc_time': row[4] or 0,
                    'role_type': row[5]
                }
        
        # Если нет данных - синхронизируем с сессиями
        if not attendance_data:
            # Получаем всех кто был в VC КВ в эту дату
            async with self.bot.db.execute('''
                SELECT user_id, SUM(duration_seconds) as total_time
                FROM voice_sessions
                WHERE guild_id = ? AND date = ? AND channel_id = ?
                GROUP BY user_id
            ''', (guild_id, date_str, kv_vc.id)) as cursor:
                sessions = await cursor.fetchall()
            
            vc_users = {row[0]: row[1] for row in sessions}
            
            # Добавляем всех участников сервера
            for member in guild.members:
                if member.bot:
                    continue
                
                role_type = self.bot.get_member_role_type(member)
                was_present = member.id in vc_users and vc_users[member.id] > 60
                vc_time = vc_users.get(member.id, 0)
                
                await self.bot.db.execute('''
                    INSERT OR IGNORE INTO kv_attendance 
                    (guild_id, schedule_id, date, kv_time, user_id, discord_name, role_type, present, vc_time_seconds)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    guild_id, matching_schedule['id'], date_str, kv_time,
                    member.id, member.display_name, role_type,
                    1 if was_present else 0, int(vc_time)
                ))
                
                attendance_data[member.id] = {
                    'present': 1 if was_present else 0,
                    'excused': None,
                    'reason': None,
                    'vc_time': int(vc_time),
                    'role_type': role_type
                }
            
            await self.bot.db.commit()
        
        # Формируем списки
        present = []
        absent = []
        
        role_icons = {
            'leader': '👑', 'officer': '⚔️', 'special': '⭐',
            'member': '🎖️', 'recruit': '🆕'
        }
        
        for member in guild.members:
            if member.bot:
                continue
            
            data = attendance_data.get(member.id, {})
            role_type = data.get('role_type') or self.bot.get_member_role_type(member)
            role_icon = role_icons.get(role_type, '')
            
            if data.get('present'):
                vc_time = data.get('vc_time', 0)
                present.append((member, role_icon, role_type, vc_time))
            else:
                absent.append((member, data, role_type))
        
        # Сортировка
        present.sort(key=lambda x: (-x[3], x[0].display_name.lower()))
        absent.sort(key=lambda x: x[0].display_name.lower())
        
        # Embed
        embed = discord.Embed(
            title=f"⚔️ Отчёт КВ: {matching_schedule['name']}",
            description=f"📅 **Дата:** {format_date(target_date)}\n"
                       f"🕐 **Время:** {kv_time}\n"
                       f"🎤 **Канал:** {kv_vc.mention}",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # Присутствовали
        if present:
            lines = []
            for member, icon, role_type, vc_time in present[:15]:
                time_str = f" ({format_duration(vc_time)})" if vc_time > 0 else ""
                lines.append(f"✅{icon} {member.display_name}{time_str}")
            
            if len(present) > 15:
                lines.append(f"... и ещё {len(present) - 15}")
            
            embed.add_field(
                name=f"✅ Присутствовали ({len(present)})",
                value="\n".join(lines) or "—",
                inline=True
            )
        
        # Отсутствовали
        if absent:
            lines = []
            for member, data, role_type in absent[:15]:
                icon = role_icons.get(role_type, '')
                excused = data.get('excused')
                reason = data.get('reason', '')
                
                if excused == 'У/П':
                    status = f"📝{icon} {member.display_name}"
                    if reason:
                        status += f" — {reason}"
                else:
                    status = f"❌{icon} {member.display_name}"
                
                lines.append(status)
            
            if len(absent) > 15:
                lines.append(f"... и ещё {len(absent) - 15}")
            
            embed.add_field(
                name=f"❌ Отсутствовали ({len(absent)})",
                value="\n".join(lines) or "—",
                inline=True
            )
        
        # Статистика
        total = len(present) + len(absent)
        present_pct = (len(present) / total * 100) if total > 0 else 0
        
        embed.add_field(
            name="📊 Статистика",
            value=f"**Всего:** {total}\n"
                  f"**Были:** {len(present)} ({present_pct:.0f}%)\n"
                  f"**Отсутствовали:** {len(absent)}",
            inline=False
        )
        
        embed.set_footer(text=f"Запросил: {ctx.author.display_name}")
        
        # View с редактированием
        view = KVReportView(self.bot, absent, date_str, guild_id, is_live=False)
        
        await ctx.send(embed=embed, view=view)
    
    # ========================================
    # РЕДАКТИРОВАНИЕ !kvedit
    # ========================================
    
    @commands.command(name='kvedit', aliases=['kedit'])
    @officer_or_higher()
    async def kv_edit(self, ctx: commands.Context, date: str = None, member: discord.Member = None, *, status: str = None):
        """
        Редактировать посещаемость КВ
        !kvedit - меню выбора даты
        !kvedit 17-01-2026 - меню редактирования
        !kvedit 17-01-2026 @user присутствовал
        !kvedit 17-01-2026 @user отсутствовал
        !kvedit 17-01-2026 @user уп "Причина"
        """
        guild_id = ctx.guild.id
        
        if not date:
            # Показать список дат
            async with self.bot.db.execute('''
                SELECT DISTINCT date FROM kv_attendance 
                WHERE guild_id = ? 
                ORDER BY date DESC LIMIT 10
            ''', (guild_id,)) as cursor:
                dates = await cursor.fetchall()
            
            if not dates:
                await ctx.send("❌ Нет данных о КВ!")
                return
            
            embed = discord.Embed(
                title="📝 Редактирование КВ",
                description="Выберите дату:\n\n" + "\n".join([
                    f"• `!kvedit {format_date(date_str=d[0])}`" for d in dates
                ]),
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)
            return
        
        # Парсим дату
        target_date = parse_date(date)
        if not target_date:
            await ctx.send("❌ Неверный формат даты!")
            return
        
        date_str = date_for_db(target_date)
        
        if not member:
            # Показать меню редактирования
            embed = discord.Embed(
                title=f"📝 Редактирование КВ за {format_date(target_date)}",
                description="Используйте команду:\n"
                           "`!kvedit <дата> @user <статус>`\n\n"
                           "**Статусы:**\n"
                           "• `присутствовал` / `был`\n"
                           "• `отсутствовал` / `небыл`\n"
                           "• `уп` / `у/п` — уважительная причина",
                color=discord.Color.blue()
            )
            
            view = KVEditView(self.bot, date_str, guild_id)
            await ctx.send(embed=embed, view=view)
            return
        
        if not status:
            await ctx.send("❌ Укажите статус: `присутствовал`, `отсутствовал`, `уп`")
            return
        
        # Применяем статус
        status_lower = status.lower().split()[0]
        
        if status_lower in ['присутствовал', 'был', 'yes', 'да', '+', '1']:
            await self.bot.db.execute('''
                UPDATE kv_attendance 
                SET present = 1, excused = NULL, reason = 'Изменено вручную', processed_by = ?
                WHERE guild_id = ? AND date = ? AND user_id = ?
            ''', (ctx.author.id, guild_id, date_str, member.id))
            await self.bot.db.commit()
            await ctx.send(f"✅ **{member.display_name}** отмечен как **присутствующий**")
        
        elif status_lower in ['отсутствовал', 'небыл', 'no', 'нет', '-', '0']:
            await self.bot.db.execute('''
                UPDATE kv_attendance 
                SET present = 0, excused = NULL, reason = NULL, processed_by = ?
                WHERE guild_id = ? AND date = ? AND user_id = ?
            ''', (ctx.author.id, guild_id, date_str, member.id))
            await self.bot.db.commit()
            await ctx.send(f"❌ **{member.display_name}** отмечен как **отсутствующий**")
        
        elif status_lower in ['уп', 'у/п', 'excused']:
            # Парсим причину
            reason = "Не указана"
            parts = status.split(maxsplit=1)
            if len(parts) > 1:
                reason = parts[1].strip('"\'')
            
            await self.bot.db.execute('''
                UPDATE kv_attendance 
                SET present = 0, excused = 'У/П', reason = ?, processed_by = ?
                WHERE guild_id = ? AND date = ? AND user_id = ?
            ''', (reason, ctx.author.id, guild_id, date_str, member.id))
            await self.bot.db.commit()
            await ctx.send(f"📝 **{member.display_name}** — У/П ({reason})")
        
        else:
            await ctx.send("❌ Неизвестный статус! Используйте: `присутствовал`, `отсутствовал`, `уп`")
    
    # ========================================
    # ЛИЧНАЯ СТАТИСТИКА !me
    # ========================================
    
    @commands.command(name='me', aliases=['я', 'мой', 'mystats'])
    async def my_stats(self, ctx: commands.Context, member: discord.Member = None):
        """
        Личная статистика
        !me - своя статистика
        !me @user - статистика другого (офицеры+)
        """
        # Если запрашивают чужую статистику - проверяем права
        if member and member != ctx.author:
            if not ctx.author.guild_permissions.administrator:
                if not await self.bot.has_permission(ctx.author, 'officer'):
                    await ctx.send("❌ Только офицеры могут смотреть чужую статистику!")
                    return
        
        target = member or ctx.author
        guild_id = ctx.guild.id
        
        # КВ статистика
        async with self.bot.db.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN present = 1 THEN 1 ELSE 0 END) as present,
                SUM(CASE WHEN excused = 'У/П' THEN 1 ELSE 0 END) as excused,
                SUM(vc_time_seconds) as total_time
            FROM kv_attendance
            WHERE guild_id = ? AND user_id = ?
        ''', (guild_id, target.id)) as cursor:
            kv_stats = await cursor.fetchone()
        
        # Собрания
        async with self.bot.db.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN present = 1 THEN 1 ELSE 0 END) as present
            FROM meeting_attendance
            WHERE guild_id = ? AND user_id = ?
        ''', (guild_id, target.id)) as cursor:
            meeting_stats = await cursor.fetchone()
        
        # Активности (рейды)
        async with self.bot.db.execute('''
            SELECT COUNT(*), SUM(duration_seconds)
            FROM raid_participants
            WHERE user_id = ?
        ''', (target.id,)) as cursor:
            raid_stats = await cursor.fetchone()
        
        # Голосовые сессии
        async with self.bot.db.execute('''
            SELECT COUNT(*), SUM(duration_seconds)
            FROM voice_sessions
            WHERE guild_id = ? AND user_id = ?
        ''', (guild_id, target.id)) as cursor:
            voice_stats = await cursor.fetchone()
        
        # Роль
        role_type = self.bot.get_member_role_type(target)
        role_icons = {'leader': '👑', 'officer': '⚔️', 'special': '⭐', 'member': '🎖️', 'recruit': '🆕'}
        role_names = {'leader': 'Глава', 'officer': 'Офицер', 'special': 'Особая', 'member': 'Рядовой', 'recruit': 'Рекрут'}
        
        embed = discord.Embed(
            title=f"📊 Статистика: {target.display_name}",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        
        embed.add_field(
            name="👤 Роль",
            value=f"{role_icons.get(role_type, '')} {role_names.get(role_type, role_type)}",
            inline=True
        )
        
        # КВ
        if kv_stats and kv_stats[0] > 0:
            total, present, excused, vc_time = kv_stats
            present = present or 0
            excused = excused or 0
            vc_time = vc_time or 0
            pct = (present / total * 100) if total > 0 else 0
            
            embed.add_field(
                name="⚔️ КВ",
                value=f"Посещено: **{present}/{total}** ({pct:.0f}%)\n"
                      f"У/П: **{excused}**\n"
                      f"Время: **{format_duration(vc_time)}**",
                inline=True
            )
            
            # Прогресс-бар
            bar_length = 10
            filled = int(bar_length * pct / 100)
            bar = "█" * filled + "░" * (bar_length - filled)
            embed.add_field(name="📈 КВ прогресс", value=f"`[{bar}]` {pct:.0f}%", inline=True)
        else:
            embed.add_field(name="⚔️ КВ", value="Нет данных", inline=True)
        
        # Собрания
        if meeting_stats and meeting_stats[0] > 0:
            total, present = meeting_stats
            present = present or 0
            pct = (present / total * 100) if total > 0 else 0
            embed.add_field(
                name="📋 Собрания",
                value=f"Посещено: **{present}/{total}** ({pct:.0f}%)",
                inline=True
            )
        
        # Активности
        if raid_stats and raid_stats[0] > 0:
            count, total_time = raid_stats
            total_time = total_time or 0
            embed.add_field(
                name="🎯 Активности",
                value=f"Участий: **{count}**\n"
                      f"Время: **{format_duration(total_time)}**",
                inline=True
            )
        
        # Голосовые
        if voice_stats and voice_stats[0] > 0:
            sessions, total_time = voice_stats
            total_time = total_time or 0
            embed.add_field(
                name="🎤 Голосовые",
                value=f"Сессий: **{sessions}**\n"
                      f"Время: **{format_duration(total_time)}**",
                inline=True
            )
        
        embed.set_footer(text=f"Запросил: {ctx.author.display_name}")
        
        await ctx.send(embed=embed)
    
    # ========================================
    # СОБРАНИЯ
    # ========================================
    
    @commands.group(name='meeting', aliases=['собрание'], invoke_without_command=True)
    @officer_or_higher()
    async def meeting(self, ctx: commands.Context):
        """Управление собраниями"""
        await ctx.send_help(ctx.command)
    
    @meeting.command(name='start', aliases=['начать'])
    @officer_or_higher()
    async def meeting_start(self, ctx: commands.Context):
        """Начать собрание (зайдите в VC)"""
        guild_id = ctx.guild.id
        
        if guild_id in self.bot.active_meetings:
            await ctx.send("⚠️ Собрание уже идёт! `!meeting end` для завершения")
            return
        
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("❌ Зайдите в голосовой канал!")
            return
        
        vc = ctx.author.voice.channel
        now = datetime.now(self.bot.timezone)
        
        # Создаём запись
        cursor = await self.bot.db.execute('''
            INSERT INTO meetings (guild_id, channel_id, channel_name, start_time, created_by, status)
            VALUES (?, ?, ?, ?, ?, 'active')
        ''', (guild_id, vc.id, vc.name, now.isoformat(), ctx.author.id))
        await self.bot.db.commit()
        
        meeting_id = cursor.lastrowid
        
        # Кэш
        self.bot.active_meetings[guild_id] = {
            'id': meeting_id,
            'channel_id': vc.id,
            'start_time': now
        }
        
        # Добавляем ВСЕХ участников сервера
        for member in ctx.guild.members:
            if member.bot:
                continue
            
            role_type = self.bot.get_member_role_type(member)
            is_present = member in vc.members
            
            await self.bot.db.execute('''
                INSERT INTO meeting_attendance 
                (meeting_id, guild_id, user_id, discord_name, role_type, present, join_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                meeting_id, guild_id, member.id, member.display_name, role_type,
                1 if is_present else 0,
                now.isoformat() if is_present else None
            ))
        
        await self.bot.db.commit()
        
        embed = discord.Embed(
            title="📋 Собрание началось!",
            description=f"**Канал:** {vc.mention}\n"
                       f"**Участников:** {len([m for m in vc.members if not m.bot])}",
            color=discord.Color.blue(),
            timestamp=now
        )
        embed.set_footer(text="!meeting end — завершить")
        
        await ctx.send(embed=embed)
    
    @meeting.command(name='end', aliases=['stop', 'завершить'])
    @officer_or_higher()
    async def meeting_end(self, ctx: commands.Context):
        """Завершить собрание"""
        guild_id = ctx.guild.id
        
        if guild_id not in self.bot.active_meetings:
            await ctx.send("❌ Нет активного собрания!")
            return
        
        meeting = self.bot.active_meetings.pop(guild_id)
        meeting_id = meeting['id']
        now = datetime.now(self.bot.timezone)
        
        # Завершаем
        await self.bot.db.execute('''
            UPDATE meetings SET end_time = ?, status = 'completed'
            WHERE id = ?
        ''', (now.isoformat(), meeting_id))
        await self.bot.db.commit()
        
        # Получаем участников
        async with self.bot.db.execute('''
            SELECT discord_name, role_type, present, duration_seconds
            FROM meeting_attendance
            WHERE meeting_id = ?
            ORDER BY 
                CASE role_type 
                    WHEN 'leader' THEN 1 
                    WHEN 'officer' THEN 2 
                    WHEN 'special' THEN 3 
                    WHEN 'member' THEN 4 
                    WHEN 'recruit' THEN 5 
                END,
                discord_name
        ''', (meeting_id,)) as cursor:
            participants = await cursor.fetchall()
        
        # Формируем отчёт
        role_icons = {'leader': '👑', 'officer': '⚔️', 'special': '⭐', 'member': '🎖️', 'recruit': '🆕'}
        
        duration = (now - meeting['start_time']).total_seconds()
        
        embed = discord.Embed(
            title="📋 Собрание завершено!",
            description=f"**Длительность:** {format_duration(int(duration))}",
            color=discord.Color.green(),
            timestamp=now
        )
        
        present_list = []
        absent_list = []
        
        for name, role_type, present, dur in participants:
            icon = role_icons.get(role_type, '')
            if present:
                present_list.append(f"✅{icon} {name}")
            else:
                absent_list.append(f"❌{icon} {name}")
        
        if present_list:
            embed.add_field(
                name=f"✅ Присутствовали ({len(present_list)})",
                value="\n".join(present_list[:15]) + (f"\n... и ещё {len(present_list)-15}" if len(present_list) > 15 else ""),
                inline=True
            )
        
        if absent_list:
            embed.add_field(
                name=f"❌ Отсутствовали ({len(absent_list)})",
                value="\n".join(absent_list[:15]) + (f"\n... и ещё {len(absent_list)-15}" if len(absent_list) > 15 else ""),
                inline=True
            )
        
        total = len(participants)
        present_count = len(present_list)
        pct = (present_count / total * 100) if total > 0 else 0
        
        embed.add_field(
            name="📊 Итого",
            value=f"**{present_count}/{total}** ({pct:.0f}%)",
            inline=False
        )
        
        await ctx.send(embed=embed)
    
    @meeting.command(name='plan', aliases=['запланировать'])
    @officer_or_higher()
    async def meeting_plan(self, ctx: commands.Context, time: str, date: str, *, name: str = "Собрание"):
        """Запланировать собрание"""
        try:
            datetime.strptime(time, '%H:%M')
        except ValueError:
            await ctx.send("❌ Неверный формат времени! Используйте HH:MM")
            return
        
        target_date = parse_date(date)
        if not target_date:
            await ctx.send("❌ Неверный формат даты!")
            return
        
        date_str = date_for_db(target_date)
        
        cursor = await self.bot.db.execute('''
            INSERT INTO meetings (guild_id, channel_name, start_time, created_by, status)
            VALUES (?, ?, ?, ?, 'planned')
        ''', (ctx.guild.id, name, f"{date_str}T{time}:00", ctx.author.id))
        await self.bot.db.commit()
        
        embed = discord.Embed(
            title="📋 Собрание запланировано!",
            color=discord.Color.blue()
        )
        embed.add_field(name="📋 Название", value=name, inline=True)
        embed.add_field(name="📅 Дата", value=format_date(target_date), inline=True)
        embed.add_field(name="🕐 Время", value=time, inline=True)
        embed.set_footer(text=f"ID: {cursor.lastrowid}")
        
        await ctx.send(embed=embed)
    
    @meeting.command(name='list', aliases=['список'])
    async def meeting_list(self, ctx: commands.Context):
        """Список собраний"""
        async with self.bot.db.execute('''
            SELECT id, channel_name, start_time, status
            FROM meetings
            WHERE guild_id = ? AND status IN ('planned', 'active')
            ORDER BY start_time
        ''', (ctx.guild.id,)) as cursor:
            meetings = await cursor.fetchall()
        
        embed = discord.Embed(title="📋 Собрания", color=discord.Color.blue())
        
        if not meetings:
            embed.description = "Нет запланированных собраний"
        else:
            for m_id, name, start_time, status in meetings:
                try:
                    dt = datetime.fromisoformat(start_time)
                    date_str = format_date(dt)
                    time_str = dt.strftime('%H:%M')
                except:
                    date_str = start_time
                    time_str = ""
                
                status_icon = "🔴 СЕЙЧАС" if status == 'active' else "📅"
                embed.add_field(
                    name=f"#{m_id} {status_icon} {name}",
                    value=f"📅 {date_str} в {time_str}",
                    inline=False
                )
        
        await ctx.send(embed=embed)
    
    @meeting.command(name='cancel', aliases=['отмена'])
    @officer_or_higher()
    async def meeting_cancel(self, ctx: commands.Context, meeting_id: int):
        """Отменить собрание"""
        result = await self.bot.db.execute('''
            UPDATE meetings SET status = 'cancelled'
            WHERE id = ? AND guild_id = ? AND status = 'planned'
        ''', (meeting_id, ctx.guild.id))
        await self.bot.db.commit()
        
        if result.rowcount == 0:
            await ctx.send("❌ Собрание не найдено!")
            return
        
        await ctx.send(f"✅ Собрание #{meeting_id} отменено!")
    
    @tasks.loop(minutes=1)
    async def meeting_reminder(self):
        """Напоминания о собраниях"""
        now = datetime.now()
        
        async with self.bot.db.execute('''
            SELECT id, guild_id, channel_name, start_time
            FROM meetings WHERE status = 'planned'
        ''') as cursor:
            meetings = await cursor.fetchall()
        
        for m_id, guild_id, name, start_time in meetings:
            try:
                dt = datetime.fromisoformat(start_time)
                diff = (dt - now).total_seconds()
                
                if 840 <= diff <= 900:  # 14-15 минут
                    guild = self.bot.get_guild(guild_id)
                    if guild:
                        settings = self.bot.guild_settings.get(guild_id, {})
                        channel_id = settings.get('report_channel_id')
                        if channel_id:
                            channel = guild.get_channel(channel_id)
                            if channel:
                                embed = discord.Embed(
                                    title=f"📋 Собрание через 15 минут!",
                                    description=f"**{name}**\n🕐 Время: {dt.strftime('%H:%M')}",
                                    color=discord.Color.orange()
                                )
                                await channel.send(embed=embed)
            except Exception as e:
                logger.error(f"Ошибка напоминания: {e}")
    
    @meeting_reminder.before_loop
    async def before_meeting_reminder(self):
        await self.bot.wait_until_ready()
    
    # ========================================
    # ЭКСПОРТ EXCEL
    # ========================================
    
    @commands.command(name='export', aliases=['экспорт', 'excel'])
    @officer_or_higher()
    async def export_excel(self, ctx: commands.Context, date: str = None):
        """Экспорт в Excel"""
        if not OPENPYXL_AVAILABLE:
            await ctx.send("❌ openpyxl не установлен!")
            return
        
        msg = await ctx.send("📊 Генерация Excel...")
        
        guild_id = ctx.guild.id
        
        if date:
            parsed = parse_date(date)
            if not parsed:
                await msg.edit(content="❌ Неверный формат даты!")
                return
            date_db = date_for_db(parsed)
            date_filter = f"AND date = '{date_db}'"
            date_display = format_date(parsed)
        else:
            date_filter = ""
            date_display = "Все даты"
        
        try:
            wb = Workbook()
            
            # Стили
            header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
            header_font = Font(bold=True, color="FFFFFF")
            thin_border = Border(
                left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin')
            )
            green_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
            red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
            
            # ===== ЛИСТ 1: СЕССИИ =====
            ws_sessions = wb.active
            ws_sessions.title = "Сессии"
            
            headers = ["Дата", "Канал", "Discord ID", "Ник", "Время входа", "Время выхода", "Длительность", "Статус"]
            for col, h in enumerate(headers, 1):
                cell = ws_sessions.cell(1, col, h)
                cell.fill = header_fill
                cell.font = header_font
                cell.border = thin_border
                ws_sessions.column_dimensions[get_column_letter(col)].width = 18
            
            async with self.bot.db.execute(f'''
                SELECT date, channel_name, user_id, display_name, join_time, leave_time, duration_seconds, status
                FROM voice_sessions WHERE guild_id = ? {date_filter}
                ORDER BY join_time DESC
            ''', (guild_id,)) as cursor:
                row_num = 2
                async for row in cursor:
                    date_val, channel, user_id, name, join_t, leave_t, dur, status = row
                    
                    try:
                        join_str = datetime.fromisoformat(join_t).strftime('%H:%M:%S') if join_t else ""
                    except:
                        join_str = ""
                    
                    try:
                        leave_str = datetime.fromisoformat(leave_t).strftime('%H:%M:%S') if leave_t else ""
                    except:
                        leave_str = ""
                    
                    ws_sessions.cell(row_num, 1, format_date(date_str=date_val) if date_val else "")
                    ws_sessions.cell(row_num, 2, channel or "")
                    ws_sessions.cell(row_num, 3, user_id)
                    ws_sessions.cell(row_num, 4, name or "")
                    ws_sessions.cell(row_num, 5, join_str)
                    ws_sessions.cell(row_num, 6, leave_str)
                    ws_sessions.cell(row_num, 7, format_duration(dur) if dur else "")
                    ws_sessions.cell(row_num, 8, status or "")
                    
                    for col in range(1, 9):
                        ws_sessions.cell(row_num, col).border = thin_border
                    row_num += 1
            
            # ===== ЛИСТ 2: ПОСЕЩАЕМОСТЬ КВ =====
            ws_kv = wb.create_sheet("Посещаемость_КВ")
            
            headers = ["Дата", "Время КВ", "Discord ID", "Ник", "Роль", "Присутствовал", "У/П", "Причина", "Время в VC"]
            for col, h in enumerate(headers, 1):
                cell = ws_kv.cell(1, col, h)
                cell.fill = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid")
                cell.font = header_font
                cell.border = thin_border
                ws_kv.column_dimensions[get_column_letter(col)].width = 16
            
            async with self.bot.db.execute(f'''
                SELECT date, kv_time, user_id, discord_name, role_type, present, excused, reason, vc_time_seconds
                FROM kv_attendance WHERE guild_id = ? {date_filter}
                ORDER BY date DESC, role_type, discord_name
            ''', (guild_id,)) as cursor:
                row_num = 2
                async for row in cursor:
                    date_val, kv_time, user_id, name, role, present, excused, reason, vc_time = row
                    
                    ws_kv.cell(row_num, 1, format_date(date_str=date_val) if date_val else "")
                    ws_kv.cell(row_num, 2, kv_time or "")
                    ws_kv.cell(row_num, 3, user_id)
                    ws_kv.cell(row_num, 4, name or "")
                    ws_kv.cell(row_num, 5, role or "member")
                    ws_kv.cell(row_num, 6, "Да" if present else "Нет")
                    ws_kv.cell(row_num, 7, excused or "")
                    ws_kv.cell(row_num, 8, reason or "")
                    ws_kv.cell(row_num, 9, format_duration(vc_time) if vc_time else "")
                    
                    fill = green_fill if present else red_fill
                    for col in range(1, 10):
                        ws_kv.cell(row_num, col).fill = fill
                        ws_kv.cell(row_num, col).border = thin_border
                    row_num += 1
            
            # ===== ЛИСТ 3: СОБРАНИЯ =====
            ws_meetings = wb.create_sheet("Собрания")
            
            headers = ["ID", "Название", "Дата", "Начало", "Окончание", "Discord ID", "Ник", "Роль", "Присутствовал"]
            for col, h in enumerate(headers, 1):
                cell = ws_meetings.cell(1, col, h)
                cell.fill = PatternFill(start_color="7030A0", end_color="7030A0", fill_type="solid")
                cell.font = header_font
                cell.border = thin_border
            
            async with self.bot.db.execute(f'''
                SELECT m.id, m.channel_name, m.start_time, m.end_time,
                       ma.user_id, ma.discord_name, ma.role_type, ma.present
                FROM meetings m
                LEFT JOIN meeting_attendance ma ON m.id = ma.meeting_id
                WHERE m.guild_id = ? {date_filter.replace('date', "date(m.start_time)")}
                ORDER BY m.start_time DESC
            ''', (guild_id,)) as cursor:
                row_num = 2
                async for row in cursor:
                    m_id, name, start, end, user_id, uname, role, present = row
                    
                    try:
                        start_dt = datetime.fromisoformat(start) if start else None
                    except:
                        start_dt = None
                    
                    ws_meetings.cell(row_num, 1, m_id)
                    ws_meetings.cell(row_num, 2, name or "")
                    ws_meetings.cell(row_num, 3, format_date(start_dt) if start_dt else "")
                    ws_meetings.cell(row_num, 4, start_dt.strftime('%H:%M') if start_dt else "")
                    ws_meetings.cell(row_num, 5, "")
                    ws_meetings.cell(row_num, 6, user_id or "")
                    ws_meetings.cell(row_num, 7, uname or "")
                    ws_meetings.cell(row_num, 8, role or "")
                    ws_meetings.cell(row_num, 9, "Да" if present else "Нет")
                    
                    for col in range(1, 10):
                        ws_meetings.cell(row_num, col).border = thin_border
                    row_num += 1
            
            # ===== ЛИСТ 4: РЕЙДЫ =====
            ws_raids = wb.create_sheet("Рейды_Активности")
            
            headers = ["ID", "Название", "Тип", "Дата", "Время", "Статус", "Создал", "VC"]
            for col, h in enumerate(headers, 1):
                cell = ws_raids.cell(1, col, h)
                cell.fill = PatternFill(start_color="70AD47", end_color="70AD47", fill_type="solid")
                cell.font = header_font
                cell.border = thin_border
            
            async with self.bot.db.execute(f'''
                SELECT id, name, raid_type, date, time, status, created_by, vc_channel_id
                FROM raids WHERE guild_id = ? {date_filter}
                ORDER BY date DESC
            ''', (guild_id,)) as cursor:
                row_num = 2
                async for row in cursor:
                    r_id, name, rtype, date_val, time_val, status, created_by, vc_id = row
                    
                    creator = ctx.guild.get_member(created_by)
                    vc = ctx.guild.get_channel(vc_id) if vc_id else None
                    
                    ws_raids.cell(row_num, 1, r_id)
                    ws_raids.cell(row_num, 2, name or "")
                    ws_raids.cell(row_num, 3, rtype or "")
                    ws_raids.cell(row_num, 4, format_date(date_str=date_val) if date_val else "")
                    ws_raids.cell(row_num, 5, time_val or "")
                    ws_raids.cell(row_num, 6, status or "")
                    ws_raids.cell(row_num, 7, creator.display_name if creator else str(created_by))
                    ws_raids.cell(row_num, 8, vc.name if vc else "")
                    
                    for col in range(1, 9):
                        ws_raids.cell(row_num, col).border = thin_border
                    row_num += 1
            
            # ===== ЛИСТ 5: УЧАСТНИКИ РЕЙДОВ =====
            ws_parts = wb.create_sheet("Участники_рейдов")
            
            headers = ["ID Рейда", "Название", "Дата", "Discord ID", "Ник", "Вход", "Выход", "Длительность"]
            for col, h in enumerate(headers, 1):
                cell = ws_parts.cell(1, col, h)
                cell.fill = PatternFill(start_color="ED7D31", end_color="ED7D31", fill_type="solid")
                cell.font = header_font
                cell.border = thin_border
            
            async with self.bot.db.execute(f'''
                SELECT rp.raid_id, r.name, r.date, rp.user_id, rp.display_name, 
                       rp.join_time, rp.leave_time, rp.duration_seconds
                FROM raid_participants rp
                JOIN raids r ON rp.raid_id = r.id
                WHERE r.guild_id = ? {date_filter.replace('date', 'r.date')}
                ORDER BY r.date DESC
            ''', (guild_id,)) as cursor:
                row_num = 2
                async for row in cursor:
                    r_id, name, date_val, user_id, uname, join_t, leave_t, dur = row
                    
                    try:
                        join_str = datetime.fromisoformat(join_t).strftime('%H:%M:%S') if join_t else ""
                    except:
                        join_str = ""
                    
                    try:
                        leave_str = datetime.fromisoformat(leave_t).strftime('%H:%M:%S') if leave_t else ""
                    except:
                        leave_str = ""
                    
                    ws_parts.cell(row_num, 1, r_id)
                    ws_parts.cell(row_num, 2, name or "")
                    ws_parts.cell(row_num, 3, format_date(date_str=date_val) if date_val else "")
                    ws_parts.cell(row_num, 4, user_id)
                    ws_parts.cell(row_num, 5, uname or "")
                    ws_parts.cell(row_num, 6, join_str)
                    ws_parts.cell(row_num, 7, leave_str)
                    ws_parts.cell(row_num, 8, format_duration(dur) if dur else "")
                    
                    for col in range(1, 9):
                        ws_parts.cell(row_num, col).border = thin_border
                    row_num += 1
            
            # Сохраняем
            excel_path = self.bot.config.get('EXCEL_PATH', 'data/clan_attendance.xlsx')
            wb.save(excel_path)
            
            await msg.delete()
            
            embed = discord.Embed(
                title="📊 Excel отчёт готов!",
                description=f"**Период:** {date_display}",
                color=discord.Color.green()
            )
            embed.add_field(
                name="📋 Листы",
                value="• Сессии\n• Посещаемость_КВ\n• Собрания\n• Рейды_Активности\n• Участники_рейдов",
                inline=False
            )
            
            await ctx.send(embed=embed, file=discord.File(excel_path))
            
        except Exception as e:
            logger.error(f"Ошибка экспорта: {e}")
            import traceback
            traceback.print_exc()
            await msg.edit(content=f"❌ Ошибка: {e}")
    
    # ========================================
    # СТАТИСТИКА
    # ========================================
    
    @commands.command(name='stats', aliases=['статистика'])
    async def stats(self, ctx: commands.Context, member: discord.Member = None):
        """Статистика пользователя"""
        await ctx.invoke(self.bot.get_command('me'), member=member)
    
    @commands.command(name='top10', aliases=['топ10', 'топ'])
    async def top10(self, ctx: commands.Context):
        """Топ-10 по посещаемости КВ"""
        guild_id = ctx.guild.id
        
        async with self.bot.db.execute('''
            SELECT user_id, discord_name,
                   COUNT(*) as total,
                   SUM(CASE WHEN present = 1 THEN 1 ELSE 0 END) as present,
                   SUM(vc_time_seconds) as vc_time
            FROM kv_attendance
            WHERE guild_id = ?
            GROUP BY user_id
            ORDER BY present DESC, vc_time DESC
            LIMIT 10
        ''', (guild_id,)) as cursor:
            rows = await cursor.fetchall()
        
        if not rows:
            await ctx.send("📊 Нет данных")
            return
        
        embed = discord.Embed(
            title="🏆 Топ-10 по КВ",
            color=discord.Color.gold()
        )
        
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        lines = []
        for i, (user_id, name, total, present, vc_time) in enumerate(rows):
            vc_time = vc_time or 0
            pct = (present / total * 100) if total > 0 else 0
            lines.append(f"{medals[i]} **{name}** — {present}/{total} ({pct:.0f}%) • {format_duration(vc_time)}")
        
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)
    
    @commands.command(name='online', aliases=['онлайн'])
    async def online(self, ctx: commands.Context):
        """Кто сейчас в голосовых каналах"""
        guild = ctx.guild
        
        embed = discord.Embed(
            title="🎤 Онлайн в голосовых",
            color=discord.Color.green()
        )
        
        total = 0
        for vc in guild.voice_channels:
            members = [m for m in vc.members if not m.bot]
            if members:
                total += len(members)
                names = ", ".join([m.display_name for m in members[:10]])
                if len(members) > 10:
                    names += f" и ещё {len(members)-10}"
                embed.add_field(name=f"🔊 {vc.name} ({len(members)})", value=names, inline=False)
        
        if total == 0:
            embed.description = "Все каналы пусты"
        else:
            embed.description = f"**Всего:** {total}"
        
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(AttendanceCog(bot))
