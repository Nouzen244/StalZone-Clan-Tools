"""
Модуль активностей v3.0
Gold Drop, Рейды, Боссы и другие активности
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from pathlib import Path

logger = logging.getLogger('STALCRAFT')


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
        except:
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


ACTIVITY_TYPES = {
    'raid': ('⚔️', 'Рейд'),
    'gold': ('💰', 'Gold Drop'),
    'gold_drop': ('💰', 'Gold Drop'),
    'farm': ('🌾', 'Фарм'),
    'pvp': ('🎯', 'PvP'),
    'boss': ('👹', 'Босс'),
    'event': ('🎉', 'Ивент'),
    'other': ('📋', 'Другое'),
}


def officer_or_higher():
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.guild_permissions.administrator:
            return True
        return await ctx.bot.has_permission(ctx.author, 'officer')
    return commands.check(predicate)


# ============================================
# МОДАЛЬНЫЕ ОКНА
# ============================================

class ActivityCreateModal(discord.ui.Modal, title="📝 Создание активности"):
    name = discord.ui.TextInput(label="Название", placeholder="Gold Drop на Радаре", required=True, max_length=100)
    date = discord.ui.TextInput(label="Дата (DD-MM-YYYY)", placeholder="25-01-2026", required=True, max_length=10)
    time = discord.ui.TextInput(label="Время (HH:MM)", placeholder="20:00", required=True, max_length=5)
    description = discord.ui.TextInput(label="Описание", required=False, style=discord.TextStyle.paragraph, max_length=500)
    
    def __init__(self, bot, activity_type: str = "other"):
        super().__init__()
        self.bot = bot
        self.activity_type = activity_type
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            datetime.strptime(self.time.value, '%H:%M')
        except ValueError:
            await interaction.response.send_message("❌ Неверный формат времени!", ephemeral=True)
            return
        
        parsed_date = parse_date(self.date.value)
        if not parsed_date:
            await interaction.response.send_message("❌ Неверный формат даты!", ephemeral=True)
            return
        
        db_date = date_for_db(parsed_date)
        
        try:
            cursor = await self.bot.db.execute('''
                INSERT INTO raids (guild_id, name, raid_type, date, time, description, status, created_by)
                VALUES (?, ?, ?, ?, ?, ?, 'planned', ?)
            ''', (interaction.guild.id, self.name.value, self.activity_type, db_date, self.time.value, self.description.value or None, interaction.user.id))
            await self.bot.db.commit()
            
            raid_id = cursor.lastrowid
            emoji, type_name = ACTIVITY_TYPES.get(self.activity_type, ('📋', 'Другое'))
            
            embed = discord.Embed(title=f"{emoji} Активность создана!", color=discord.Color.green())
            embed.add_field(name="🆔 ID", value=str(raid_id), inline=True)
            embed.add_field(name="📋 Название", value=self.name.value, inline=True)
            embed.add_field(name="📁 Тип", value=type_name, inline=True)
            embed.add_field(name="📅 Дата", value=format_date(parsed_date), inline=True)
            embed.add_field(name="🕐 Время", value=self.time.value, inline=True)
            
            if self.description.value:
                embed.add_field(name="📝 Описание", value=self.description.value, inline=False)
            
            view = ActivityManageView(self.bot, raid_id)
            await interaction.response.send_message(embed=embed, view=view)
            
        except Exception as e:
            await interaction.response.send_message(f"❌ Ошибка: {e}", ephemeral=True)


# ============================================
# VIEWS
# ============================================

class ActivityTypeSelectView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot
    
    @discord.ui.select(
        placeholder="📁 Тип активности...",
        options=[
            discord.SelectOption(label="Gold Drop", value="gold_drop", emoji="💰"),
            discord.SelectOption(label="Рейд", value="raid", emoji="⚔️"),
            discord.SelectOption(label="Фарм", value="farm", emoji="🌾"),
            discord.SelectOption(label="PvP", value="pvp", emoji="🎯"),
            discord.SelectOption(label="Босс", value="boss", emoji="👹"),
            discord.SelectOption(label="Ивент", value="event", emoji="🎉"),
            discord.SelectOption(label="Другое", value="other", emoji="📋"),
        ]
    )
    async def select_type(self, interaction: discord.Interaction, select):
        modal = ActivityCreateModal(self.bot, select.values[0])
        await interaction.response.send_modal(modal)
        self.stop()


class ActivityManageView(discord.ui.View):
    def __init__(self, bot, raid_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.raid_id = raid_id
    
    @discord.ui.button(label="▶️ Запустить", style=discord.ButtonStyle.success)
    async def start_activity(self, interaction: discord.Interaction, button):
        if not await self.bot.has_permission(interaction.user, 'officer'):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("🚫 Недостаточно прав!", ephemeral=True)
                return
        
        async with self.bot.db.execute(
            'SELECT name, vc_channel_id, status FROM raids WHERE id = ? AND guild_id = ?',
            (self.raid_id, interaction.guild.id)
        ) as cursor:
            raid = await cursor.fetchone()
        
        if not raid:
            await interaction.response.send_message("❌ Активность не найдена!", ephemeral=True)
            return
        
        if raid[2] == 'active':
            await interaction.response.send_message("⚠️ Уже запущена!", ephemeral=True)
            return
        
        if raid[2] == 'completed':
            await interaction.response.send_message("⚠️ Уже завершена!", ephemeral=True)
            return
        
        await self.bot.db.execute("UPDATE raids SET status = 'active' WHERE id = ?", (self.raid_id,))
        await self.bot.db.commit()
        
        self.bot.active_raids[self.raid_id] = {
            'name': raid[0],
            'vc_channel_id': raid[1],
            'guild_id': interaction.guild.id,
            'start_time': datetime.now(self.bot.timezone)
        }
        
        embed = discord.Embed(title="🚀 Активность запущена!", description=f"**{raid[0]}**", color=discord.Color.gold())
        
        if raid[1]:
            vc = interaction.guild.get_channel(raid[1])
            if vc:
                embed.add_field(name="🎤 Канал", value=vc.mention, inline=True)
                
                now = datetime.now(self.bot.timezone)
                for member in vc.members:
                    if not member.bot:
                        session_key = f"{self.raid_id}:{member.id}"
                        self.bot.raid_sessions[session_key] = {'join_time': now, 'raid_id': self.raid_id}
                        
                        await self.bot.db.execute('''
                            INSERT INTO raid_participants (raid_id, user_id, username, display_name, join_time, status)
                            VALUES (?, ?, ?, ?, ?, 'joined')
                            ON CONFLICT(raid_id, user_id) DO UPDATE SET join_time = excluded.join_time, status = 'joined'
                        ''', (self.raid_id, member.id, str(member), member.display_name, now.isoformat()))
                
                await self.bot.db.commit()
        
        view = ActivityActiveView(self.bot, self.raid_id)
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="🎤 Привязать VC", style=discord.ButtonStyle.secondary)
    async def set_vc(self, interaction: discord.Interaction, button):
        if not await self.bot.has_permission(interaction.user, 'officer'):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("🚫 Недостаточно прав!", ephemeral=True)
                return
        
        channels = [vc for vc in interaction.guild.voice_channels][:25]
        if not channels:
            await interaction.response.send_message("❌ Нет голосовых каналов!", ephemeral=True)
            return
        
        view = VCSelectView(self.bot, self.raid_id, channels)
        await interaction.response.send_message("🎤 Выберите канал:", view=view, ephemeral=True)
    
    @discord.ui.button(label="❌ Отменить", style=discord.ButtonStyle.danger)
    async def cancel_activity(self, interaction: discord.Interaction, button):
        if not await self.bot.has_permission(interaction.user, 'officer'):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("🚫 Недостаточно прав!", ephemeral=True)
                return
        
        await self.bot.db.execute("UPDATE raids SET status = 'cancelled' WHERE id = ?", (self.raid_id,))
        await self.bot.db.commit()
        
        self.bot.active_raids.pop(self.raid_id, None)
        
        embed = discord.Embed(title="❌ Активность отменена", color=discord.Color.red())
        await interaction.response.edit_message(embed=embed, view=None)


class ActivityActiveView(discord.ui.View):
    def __init__(self, bot, raid_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.raid_id = raid_id
    
    @discord.ui.button(label="🏁 Завершить", style=discord.ButtonStyle.danger)
    async def end_activity(self, interaction: discord.Interaction, button):
        if not await self.bot.has_permission(interaction.user, 'officer'):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("🚫 Недостаточно прав!", ephemeral=True)
                return
        
        async with self.bot.db.execute(
            'SELECT name, vc_channel_id, status, date FROM raids WHERE id = ? AND guild_id = ?',
            (self.raid_id, interaction.guild.id)
        ) as cursor:
            raid = await cursor.fetchone()
        
        if not raid:
            await interaction.response.send_message("❌ Не найдена!", ephemeral=True)
            return
        
        now = datetime.now(self.bot.timezone)
        end_time_str = now.strftime('%H:%M')
        
        # Закрываем сессии
        for session_key in list(self.bot.raid_sessions.keys()):
            if session_key.startswith(f"{self.raid_id}:"):
                session = self.bot.raid_sessions.pop(session_key)
                user_id = int(session_key.split(':')[1])
                duration = (now - session['join_time']).total_seconds()
                
                await self.bot.db.execute('''
                    UPDATE raid_participants SET leave_time = ?, duration_seconds = duration_seconds + ?, status = 'completed'
                    WHERE raid_id = ? AND user_id = ?
                ''', (now.isoformat(), int(duration), self.raid_id, user_id))
        
        await self.bot.db.execute("UPDATE raids SET status = 'completed', end_time = ? WHERE id = ?", (end_time_str, self.raid_id))
        await self.bot.db.commit()
        
        self.bot.active_raids.pop(self.raid_id, None)
        
        # Статистика
        async with self.bot.db.execute('''
            SELECT COUNT(*), SUM(duration_seconds) FROM raid_participants WHERE raid_id = ?
        ''', (self.raid_id,)) as cursor:
            stats = await cursor.fetchone()
        
        participants = stats[0] or 0
        total_time = stats[1] or 0
        avg_time = int(total_time / participants / 60) if participants > 0 else 0
        
        embed = discord.Embed(title="🏁 Активность завершена!", description=f"**{raid[0]}**", color=discord.Color.green())
        embed.add_field(name="👥 Участников", value=str(participants), inline=True)
        embed.add_field(name="📊 Среднее время", value=f"{avg_time} мин", inline=True)
        embed.add_field(name="📅 Дата", value=format_date(date_str=raid[3]), inline=True)
        
        await interaction.response.edit_message(embed=embed, view=None)
    
    @discord.ui.button(label="📊 Участники", style=discord.ButtonStyle.primary)
    async def show_participants(self, interaction: discord.Interaction, button):
        async with self.bot.db.execute('''
            SELECT display_name, join_time, leave_time, duration_seconds, status
            FROM raid_participants WHERE raid_id = ? ORDER BY join_time
        ''', (self.raid_id,)) as cursor:
            participants = await cursor.fetchall()
        
        if not participants:
            await interaction.response.send_message("👥 Пока нет участников", ephemeral=True)
            return
        
        lines = []
        for name, join_t, leave_t, dur, status in participants:
            dur = dur or 0
            icon = "🟢" if status == 'joined' else "✅"
            
            join_str = datetime.fromisoformat(join_t).strftime('%H:%M') if join_t else "?"
            leave_str = datetime.fromisoformat(leave_t).strftime('%H:%M') if leave_t else "—"
            
            lines.append(f"{icon} **{name}** | {join_str}→{leave_str} | {format_duration(dur)}")
        
        embed = discord.Embed(title=f"👥 Участники ({len(participants)})", description="\n".join(lines[:20]), color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)


class VCSelectView(discord.ui.View):
    def __init__(self, bot, raid_id: int, channels):
        super().__init__(timeout=60)
        options = [discord.SelectOption(label=vc.name[:100], value=str(vc.id), emoji="🔊") for vc in channels]
        self.add_item(VCSelect(bot, raid_id, options))


class VCSelect(discord.ui.Select):
    def __init__(self, bot, raid_id: int, options):
        super().__init__(placeholder="🎤 Канал...", options=options)
        self.bot = bot
        self.raid_id = raid_id
    
    async def callback(self, interaction: discord.Interaction):
        channel_id = int(self.values[0])
        channel = interaction.guild.get_channel(channel_id)
        
        await self.bot.db.execute('UPDATE raids SET vc_channel_id = ? WHERE id = ?', (channel_id, self.raid_id))
        await self.bot.db.commit()
        
        await interaction.response.send_message(f"✅ Канал **{channel.name}** привязан!", ephemeral=True)


# ============================================
# КОГ
# ============================================

class StalcraftCog(commands.Cog, name="STALCRAFT"):
    """Активности и рейды"""
    
    def __init__(self, bot):
        self.bot = bot
        logger.info("🎮 StalcraftCog v3.0 загружен")
        self.raid_reminder.start()
    
    def cog_unload(self):
        self.raid_reminder.cancel()
    
    @commands.group(name='activity', aliases=['act', 'активность'], invoke_without_command=True)
    async def activity(self, ctx: commands.Context):
        """Группа команд активностей"""
        await ctx.invoke(self.bot.get_command('activity list'))
    
    @activity.command(name='new', aliases=['create', 'add', 'создать'])
    @officer_or_higher()
    async def activity_new(self, ctx: commands.Context):
        """Создать активность через меню"""
        view = ActivityTypeSelectView(self.bot)
        embed = discord.Embed(title="📝 Создание активности", description="Выберите тип:", color=discord.Color.blue())
        await ctx.send(embed=embed, view=view)
    
    @activity.command(name='quick', aliases=['fast', 'быстро'])
    @officer_or_higher()
    async def activity_quick(self, ctx: commands.Context, time: str, date: str, *, name: str):
        """
        Быстрое создание
        !activity quick 20:00 17-01-2026 Gold Drop
        """
        try:
            datetime.strptime(time, '%H:%M')
        except ValueError:
            await ctx.send("❌ Неверный формат времени!")
            return
        
        parsed_date = parse_date(date)
        if not parsed_date:
            await ctx.send("❌ Неверный формат даты!")
            return
        
        # Определяем тип
        activity_type = 'other'
        name_lower = name.lower()
        
        keywords = {
            'gold': 'gold_drop', 'голд': 'gold_drop',
            'рейд': 'raid', 'raid': 'raid',
            'фарм': 'farm', 'farm': 'farm',
            'пвп': 'pvp', 'pvp': 'pvp',
            'босс': 'boss', 'boss': 'boss',
        }
        
        for kw, atype in keywords.items():
            if kw in name_lower:
                activity_type = atype
                break
        
        db_date = date_for_db(parsed_date)
        
        try:
            cursor = await self.bot.db.execute('''
                INSERT INTO raids (guild_id, name, raid_type, date, time, status, created_by)
                VALUES (?, ?, ?, ?, ?, 'planned', ?)
            ''', (ctx.guild.id, name, activity_type, db_date, time, ctx.author.id))
            await self.bot.db.commit()
            
            raid_id = cursor.lastrowid
            emoji, type_name = ACTIVITY_TYPES.get(activity_type, ('📋', 'Другое'))
            
            embed = discord.Embed(title=f"{emoji} Активность создана!", color=discord.Color.green())
            embed.add_field(name="🆔 ID", value=str(raid_id), inline=True)
            embed.add_field(name="📋 Название", value=name, inline=True)
            embed.add_field(name="📁 Тип", value=type_name, inline=True)
            embed.add_field(name="📅 Дата", value=format_date(parsed_date), inline=True)
            embed.add_field(name="🕐 Время", value=time, inline=True)
            
            view = ActivityManageView(self.bot, raid_id)
            await ctx.send(embed=embed, view=view)
            
        except Exception as e:
            await ctx.send(f"❌ Ошибка: {e}")
    
    @activity.command(name='list', aliases=['список'])
    async def activity_list(self, ctx: commands.Context, show_all: str = None):
        """Список активностей"""
        guild_id = ctx.guild.id
        
        if show_all == 'all':
            query = '''SELECT id, name, raid_type, date, time, status, vc_channel_id
                       FROM raids WHERE guild_id = ? ORDER BY date DESC, time DESC LIMIT 25'''
        else:
            query = '''SELECT id, name, raid_type, date, time, status, vc_channel_id
                       FROM raids WHERE guild_id = ? AND status IN ('planned', 'active')
                       ORDER BY date, time'''
        
        async with self.bot.db.execute(query, (guild_id,)) as cursor:
            raids = await cursor.fetchall()
        
        embed = discord.Embed(title="📋 Активности", color=discord.Color.blue(), timestamp=datetime.now())
        
        if not raids:
            embed.description = "Нет активностей.\n\n`!activity new` — создать"
            embed.color = discord.Color.greyple()
        else:
            for r_id, name, rtype, date, time, status, vc_id in raids:
                emoji, type_name = ACTIVITY_TYPES.get(rtype, ('📋', 'Другое'))
                
                status_icons = {'planned': '📅', 'active': '🔴 LIVE', 'completed': '✅', 'cancelled': '❌'}
                status_text = status_icons.get(status, '❓')
                
                vc_text = ""
                if vc_id:
                    vc = ctx.guild.get_channel(vc_id)
                    vc_text = f"\n🎤 {vc.name if vc else 'Удалён'}"
                
                embed.add_field(
                    name=f"#{r_id} {emoji} {name}",
                    value=f"📅 {format_date(date_str=date)} в {time}\n📁 {type_name} | {status_text}{vc_text}",
                    inline=False
                )
        
        view = ActivityListView(self.bot)
        await ctx.send(embed=embed, view=view)
    
    @activity.command(name='info', aliases=['инфо'])
    async def activity_info(self, ctx: commands.Context, activity_id: int):
        """Информация об активности"""
        async with self.bot.db.execute('''
            SELECT id, name, raid_type, date, time, end_time, status, vc_channel_id, description, created_by
            FROM raids WHERE id = ? AND guild_id = ?
        ''', (activity_id, ctx.guild.id)) as cursor:
            raid = await cursor.fetchone()
        
        if not raid:
            await ctx.send(f"❌ Активность #{activity_id} не найдена!")
            return
        
        _, name, rtype, date, time, end_time, status, vc_id, desc, created_by = raid
        emoji, type_name = ACTIVITY_TYPES.get(rtype, ('📋', 'Другое'))
        
        status_text = {'planned': '📅 Запланировано', 'active': '🔴 Активно', 'completed': '✅ Завершено', 'cancelled': '❌ Отменено'}.get(status, status)
        
        embed = discord.Embed(title=f"{emoji} {name}", color=discord.Color.gold() if status == 'active' else discord.Color.blue())
        embed.add_field(name="🆔 ID", value=str(activity_id), inline=True)
        embed.add_field(name="📁 Тип", value=type_name, inline=True)
        embed.add_field(name="📊 Статус", value=status_text, inline=True)
        embed.add_field(name="📅 Дата", value=format_date(date_str=date), inline=True)
        embed.add_field(name="🕐 Время", value=f"{time}{' - ' + end_time if end_time else ''}", inline=True)
        
        if vc_id:
            vc = ctx.guild.get_channel(vc_id)
            embed.add_field(name="🎤 Канал", value=vc.mention if vc else "Удалён", inline=True)
        
        creator = ctx.guild.get_member(created_by)
        if creator:
            embed.add_field(name="👤 Создал", value=creator.mention, inline=True)
        
        if desc:
            embed.add_field(name="📝 Описание", value=desc, inline=False)
        
        # Участники
        async with self.bot.db.execute('''
            SELECT display_name, duration_seconds, status FROM raid_participants WHERE raid_id = ?
            ORDER BY duration_seconds DESC
        ''', (activity_id,)) as cursor:
            participants = await cursor.fetchall()
        
        if participants:
            lines = []
            for pname, dur, pstatus in participants[:15]:
                dur = dur or 0
                icon = "🟢" if pstatus == 'joined' else "✅"
                lines.append(f"{icon} {pname} — {format_duration(dur)}")
            
            if len(participants) > 15:
                lines.append(f"... и ещё {len(participants) - 15}")
            
            embed.add_field(name=f"👥 Участники ({len(participants)})", value="\n".join(lines), inline=False)
        
        if status == 'planned':
            view = ActivityManageView(self.bot, activity_id)
        elif status == 'active':
            view = ActivityActiveView(self.bot, activity_id)
        else:
            view = None
        
        await ctx.send(embed=embed, view=view)
    
    @activity.command(name='setvc', aliases=['vc'])
    @officer_or_higher()
    async def activity_setvc(self, ctx: commands.Context, activity_id: int, channel: discord.VoiceChannel):
        """Привязать VC к активности"""
        await self.bot.db.execute('UPDATE raids SET vc_channel_id = ? WHERE id = ? AND guild_id = ?', (channel.id, activity_id, ctx.guild.id))
        await self.bot.db.commit()
        await ctx.send(f"✅ Канал **{channel.name}** привязан к активности #{activity_id}!")
    
    @activity.command(name='start', aliases=['старт'])
    @officer_or_higher()
    async def activity_start(self, ctx: commands.Context, activity_id: int):
        """Запустить активность"""
        async with self.bot.db.execute(
            'SELECT name, vc_channel_id, status, date FROM raids WHERE id = ? AND guild_id = ?',
            (activity_id, ctx.guild.id)
        ) as cursor:
            raid = await cursor.fetchone()
        
        if not raid:
            await ctx.send(f"❌ Активность #{activity_id} не найдена!")
            return
        
        if raid[2] != 'planned':
            await ctx.send(f"⚠️ Статус активности: {raid[2]}")
            return
        
        await self.bot.db.execute("UPDATE raids SET status = 'active' WHERE id = ?", (activity_id,))
        await self.bot.db.commit()
        
        self.bot.active_raids[activity_id] = {
            'name': raid[0], 'vc_channel_id': raid[1],
            'guild_id': ctx.guild.id, 'start_time': datetime.now(self.bot.timezone)
        }
        
        embed = discord.Embed(title="🚀 Активность запущена!", description=f"**{raid[0]}**", color=discord.Color.gold())
        
        if raid[1]:
            vc = ctx.guild.get_channel(raid[1])
            if vc:
                embed.add_field(name="🎤 Канал", value=vc.mention)
                
                now = datetime.now(self.bot.timezone)
                for member in vc.members:
                    if not member.bot:
                        self.bot.raid_sessions[f"{activity_id}:{member.id}"] = {'join_time': now, 'raid_id': activity_id}
                        await self.bot.db.execute('''
                            INSERT INTO raid_participants (raid_id, user_id, username, display_name, join_time, status)
                            VALUES (?, ?, ?, ?, ?, 'joined') ON CONFLICT(raid_id, user_id) DO UPDATE SET join_time = excluded.join_time, status = 'joined'
                        ''', (activity_id, member.id, str(member), member.display_name, now.isoformat()))
                await self.bot.db.commit()
        
        view = ActivityActiveView(self.bot, activity_id)
        await ctx.send(embed=embed, view=view)
    
    @activity.command(name='end', aliases=['stop', 'завершить'])
    @officer_or_higher()
    async def activity_end(self, ctx: commands.Context, activity_id: int):
        """Завершить активность"""
        async with self.bot.db.execute(
            'SELECT name, status, date FROM raids WHERE id = ? AND guild_id = ?',
            (activity_id, ctx.guild.id)
        ) as cursor:
            raid = await cursor.fetchone()
        
        if not raid:
            await ctx.send(f"❌ Активность #{activity_id} не найдена!")
            return
        
        now = datetime.now(self.bot.timezone)
        
        # Закрываем сессии
        for sk in list(self.bot.raid_sessions.keys()):
            if sk.startswith(f"{activity_id}:"):
                session = self.bot.raid_sessions.pop(sk)
                user_id = int(sk.split(':')[1])
                dur = (now - session['join_time']).total_seconds()
                await self.bot.db.execute('''
                    UPDATE raid_participants SET leave_time = ?, duration_seconds = duration_seconds + ?, status = 'completed'
                    WHERE raid_id = ? AND user_id = ?
                ''', (now.isoformat(), int(dur), activity_id, user_id))
        
        await self.bot.db.execute("UPDATE raids SET status = 'completed', end_time = ? WHERE id = ?", (now.strftime('%H:%M'), activity_id))
        await self.bot.db.commit()
        
        self.bot.active_raids.pop(activity_id, None)
        
        async with self.bot.db.execute('SELECT COUNT(*), SUM(duration_seconds) FROM raid_participants WHERE raid_id = ?', (activity_id,)) as cursor:
            stats = await cursor.fetchone()
        
        participants = stats[0] or 0
        total_time = stats[1] or 0
        avg = int(total_time / participants / 60) if participants else 0
        
        embed = discord.Embed(title="🏁 Активность завершена!", description=f"**{raid[0]}**", color=discord.Color.green())
        embed.add_field(name="👥 Участников", value=str(participants), inline=True)
        embed.add_field(name="📊 Среднее время", value=f"{avg} мин", inline=True)
        
        await ctx.send(embed=embed)
    
    @activity.command(name='delete', aliases=['удалить'])
    @officer_or_higher()
    async def activity_delete(self, ctx: commands.Context, activity_id: int):
        """Удалить активность"""
        async with self.bot.db.execute('SELECT name FROM raids WHERE id = ? AND guild_id = ?', (activity_id, ctx.guild.id)) as cursor:
            raid = await cursor.fetchone()
        
        if not raid:
            await ctx.send(f"❌ Активность #{activity_id} не найдена!")
            return
        
        await self.bot.db.execute('DELETE FROM raid_participants WHERE raid_id = ?', (activity_id,))
        await self.bot.db.execute('DELETE FROM raids WHERE id = ?', (activity_id,))
        await self.bot.db.commit()
        
        self.bot.active_raids.pop(activity_id, None)
        await ctx.send(f"✅ Активность **{raid[0]}** удалена!")
    
    @commands.command(name='calendar', aliases=['календарь'])
    async def calendar(self, ctx: commands.Context):
        """Календарь на неделю"""
        guild_id = ctx.guild.id
        today = datetime.now(self.bot.timezone)
        week_later = today + timedelta(days=7)
        
        today_str = date_for_db(today)
        week_str = date_for_db(week_later)
        
        async with self.bot.db.execute('''
            SELECT id, name, raid_type, date, time, status FROM raids
            WHERE guild_id = ? AND date >= ? AND date <= ? AND status != 'cancelled'
            ORDER BY date, time
        ''', (guild_id, today_str, week_str)) as cursor:
            activities = await cursor.fetchall()
        
        embed = discord.Embed(
            title="📅 Календарь на неделю",
            description=f"{format_date(today)} — {format_date(week_later)}",
            color=discord.Color.blue()
        )
        
        day_names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
        days = {}
        
        for i in range(7):
            day = today + timedelta(days=i)
            day_str = date_for_db(day)
            days[day_str] = {'display': f"{day_names[day.weekday()]} {format_date(day)}", 'events': []}
            
            # Расписание КВ
            for sched in self.bot.guild_schedules.get(guild_id, []):
                if day.weekday() in sched['days_of_week']:
                    days[day_str]['events'].append(f"⚔️ {sched['name']} ({sched['start_time']}-{sched['end_time']})")
        
        for r_id, name, rtype, date, time, status in activities:
            if date in days:
                emoji, _ = ACTIVITY_TYPES.get(rtype, ('📋', ''))
                status_icon = '🔴' if status == 'active' else ''
                days[date]['events'].append(f"{emoji} {name} в {time} {status_icon}")
        
        has_events = False
        for day_str, data in days.items():
            if data['events']:
                has_events = True
                embed.add_field(name=data['display'], value="\n".join(data['events'][:5]), inline=False)
        
        if not has_events:
            embed.add_field(name="Пусто", value="Нет событий.\n`!activity new`", inline=False)
        
        view = ActivityListView(self.bot)
        await ctx.send(embed=embed, view=view)
    
    @commands.command(name='ping')
    async def ping(self, ctx: commands.Context):
        """Проверка бота"""
        latency = round(self.bot.latency * 1000)
        color = discord.Color.green() if latency < 100 else discord.Color.yellow() if latency < 200 else discord.Color.red()
        embed = discord.Embed(title="🏓 Pong!", color=color)
        embed.add_field(name="Задержка", value=f"{latency}ms")
        await ctx.send(embed=embed)
    
    @commands.command(name='uptime')
    async def uptime(self, ctx: commands.Context):
        """Время работы"""
        uptime = datetime.now() - self.bot.start_time
        embed = discord.Embed(
            title="⏱️ Время работы",
            description=f"**{uptime.days}** дн. **{uptime.seconds // 3600}** ч. **{(uptime.seconds % 3600) // 60}** мин.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)
    
    @tasks.loop(minutes=1)
    async def raid_reminder(self):
        """Напоминания о рейдах"""
        now = datetime.now(self.bot.timezone)
        today = date_for_db(now)
        current_time = now.strftime('%H:%M')
        
        async with self.bot.db.execute('''
            SELECT id, guild_id, name, time, vc_channel_id, raid_type
            FROM raids WHERE date = ? AND status = 'planned'
        ''', (today,)) as cursor:
            upcoming = await cursor.fetchall()
        
        for r_id, guild_id, name, raid_time, vc_id, rtype in upcoming:
            try:
                raid_dt = datetime.strptime(raid_time, '%H:%M')
                notify_dt = raid_dt - timedelta(minutes=15)
                
                if now.strftime('%H:%M') == notify_dt.strftime('%H:%M'):
                    guild = self.bot.get_guild(guild_id)
                    if guild:
                        settings = self.bot.guild_settings.get(guild_id, {})
                        channel_id = settings.get('report_channel_id')
                        if channel_id:
                            channel = guild.get_channel(channel_id)
                            if channel:
                                emoji, type_name = ACTIVITY_TYPES.get(rtype, ('📋', 'Активность'))
                                embed = discord.Embed(
                                    title=f"{emoji} {name} через 15 минут!",
                                    description=f"**Время:** {raid_time}\n**Тип:** {type_name}",
                                    color=discord.Color.orange()
                                )
                                if vc_id:
                                    vc = guild.get_channel(vc_id)
                                    if vc:
                                        embed.add_field(name="🎤 Канал", value=vc.mention)
                                
                                view = ActivityManageView(self.bot, r_id)
                                await channel.send(embed=embed, view=view)
            except Exception as e:
                logger.error(f"Ошибка напоминания: {e}")
    
    @raid_reminder.before_loop
    async def before_raid_reminder(self):
        await self.bot.wait_until_ready()


class ActivityListView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot
    
    @discord.ui.button(label="➕ Создать", style=discord.ButtonStyle.success)
    async def create_new(self, interaction: discord.Interaction, button):
        if not await self.bot.has_permission(interaction.user, 'officer'):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("🚫 Недостаточно прав!", ephemeral=True)
                return
        
        view = ActivityTypeSelectView(self.bot)
        await interaction.response.send_message("📁 Выберите тип:", view=view, ephemeral=True)


class ErrorHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("🚫 Недостаточно прав!", delete_after=10)
        elif isinstance(error, commands.CheckFailure):
            await ctx.send("🚫 Нет доступа!", delete_after=10)
        elif isinstance(error, commands.CommandNotFound):
            pass
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Не хватает: `{error.param.name}`", delete_after=10)
        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"❌ Неверный аргумент", delete_after=10)
        else:
            logger.error(f"Ошибка: {error}")
            await ctx.send(f"❌ Ошибка: {error}", delete_after=15)


async def setup(bot):
    await bot.add_cog(StalcraftCog(bot))
    await bot.add_cog(ErrorHandler(bot))
