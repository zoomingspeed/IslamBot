import datetime as dt
from dataclasses import dataclass
from datetime import timedelta, datetime as dt

import discord
from dateutil.tz import gettz
from discord.ext import commands, tasks

from salaah.praytimes import PrayTimes
from utils.database_utils import UserPrayerCalculationMethod

ICON = 'https://images-na.ssl-images-amazon.com/images/I/51q8CGXOltL.png'
METHODS_URL = 'https://api.aladhan.com/v1/methods'
PRAYER_TIMES_URL = 'http://api.aladhan.com/v1/timingsByAddress?address={}&method={}&school={}'

headers = {'content-type': 'application/json'}


@dataclass
class PrayerTimesResponse:
    fajr: str
    sunrise: str
    dhuhr: str
    asr: str
    asr_hanafi: str
    maghrib: str
    isha: str
    imsak: str
    midnight: str
    date: str


class PrayerTimes(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.calculation_methods = None

    async def cog_load(self) -> None:
        self.update_calculation_methods.start()

    async def cog_unload(self) -> None:
        self.update_calculation_methods.cancel()

    # The calculation methods (infrequently) update, so dynamically add new methods
    @tasks.loop(hours=1)
    async def update_calculation_methods(self):
        async with self.bot.session.get(METHODS_URL, headers=headers) as resp:
            data = await resp.json()
            data = data['data'].values()
            # There's an entry ('CUSTOM') with no 'name' value, so we need to ignore it:
            self.calculation_methods = {method['id']: method['name'] for method in data if int(method['id']) != 99}

    async def get_prayertimes(self, location, calculation_method) -> PrayerTimesResponse:
        url = PRAYER_TIMES_URL.format(location, calculation_method, '0')

        async with self.bot.session.get(url, headers=headers) as resp:
            data = await resp.json()
            fajr = data['data']['timings']['Fajr']
            sunrise = data['data']['timings']['Sunrise']
            dhuhr = data['data']['timings']['Dhuhr']
            asr = data['data']['timings']['Asr']
            maghrib = data['data']['timings']['Maghrib']
            isha = data['data']['timings']['Isha']
            imsak = data['data']['timings']['Imsak']
            midnight = data['data']['timings']['Midnight']
            date = data['data']['date']['readable']

        url = PRAYER_TIMES_URL.format(location, calculation_method, '1')

        async with self.bot.session.get(url, headers=headers) as resp:
            data = await resp.json()
            asr_hanafi = data['data']['timings']['Asr']

        return PrayerTimesResponse(fajr, sunrise, dhuhr, asr, asr_hanafi, maghrib, isha, imsak, midnight, date)

    async def get_prayertimes_local(self, location, calculation_method) -> PrayerTimesResponse:
        async def get_information(location):
            url = "http://api.aladhan.com/v1/hijriCalendarByAddress"
            params = {"address": location}
            async with self.bot.session.get(url, headers=headers, params=params) as resp:
                data = await resp.json()
                meta = data['data'][0]['meta']
                coordinates = (meta['latitude'], meta['longitude'])
                timezone_name = meta['timezone']
            time = dt.datetime.now(gettz(timezone_name))
            timezone_offset = time.utcoffset() / timedelta(hours=1)
            return coordinates, time, timezone_offset

        def get_method_name(method_id):
            id_to_name = {
                3: 'MWL',
                2: 'ISNA',
                5: 'Egypt',
                4: 'Makkah',
                1: 'Karachi',
                7: 'Tehran',
                0: 'Jafari',
                8: 'Gulf',  # TODO add this method to praytimes.py
                9: 'Kuwait',  # TODO add this method to praytimes.py
                10: 'Qatar',  # TODO add this method to praytimes.py
                11: 'Singapore',  # TODO add this method to praytimes.py
                12: 'France',  # TODO add this method to praytimes.py
                13: 'Turkey',  # TODO add this method to praytimes.py
                14: 'Russia',  # TODO add this method to praytimes.py

                # didn't include method 'MOONSIGHTING' because it uses 'shafaq' parameter,
                # which isn't used in praytimes.py
            }
            return id_to_name[method_id]

        coordinates, time, time_offset = await get_information(location)
        date = (time.year, time.month, time.day)
        method_name = get_method_name(calculation_method)
        prayTimes = PrayTimes()
        prayTimes.setMethod(method_name)
        prayTimes.adjust({"highLats": "AngleBased"})
        prayTimes.adjust({"asr": "Standard"})
        times = prayTimes.getTimes(date, coordinates, time_offset)
        prayTimes.adjust({"asr": "Hanafi"})
        timesWithHanafiAsr = prayTimes.getTimes(date, coordinates, time_offset)

        fajr = times['fajr']
        sunrise = times['sunrise']
        dhuhr = times['dhuhr']
        asr = times['asr']
        hanafi_asr = timesWithHanafiAsr['asr']
        maghrib = times['maghrib']
        isha = times['isha']
        imsak = times['imsak']
        midnight = times['midnight']
        sunrise = times['sunrise']
        readable_date = time.strftime('%d %B, %Y')

        return PrayerTimesResponse(fajr, sunrise, dhuhr, asr, hanafi_asr, maghrib, isha, imsak, midnight, readable_date)

    async def _prayer_times(self, interaction: discord.Interaction, location: str,
                            calculation_method: int = None, hidden: bool = False, twelve_hour: bool = False):
        if calculation_method is None:
            calculation_method = await UserPrayerCalculationMethod(interaction.user.id).get()

        try:
            response = await self.get_prayertimes(location, calculation_method)
        except Exception as e:
            return await interaction.followup.send_message(f":warning: **Location not found** {e}.", ephemeral=hidden)

        em = discord.Embed(colour=0x558a25, title=response.date)
        em.set_footer(text=f'Calculation Method: {self.calculation_methods[calculation_method]}')
        em.set_author(name=f'Prayer Times for {location.title()}', icon_url=ICON)

        prayer_times = {
            'Imsak (إِمْسَاك)': response.imsak,
            'Fajr (صلاة الفجر)': response.fajr,
            'Sunrise (طلوع الشمس)': response.sunrise,
            'Ẓuhr (صلاة الظهر)': response.dhuhr,
            'Asr (صلاة العصر)': response.asr,
            'Asr - Ḥanafī School (صلاة العصر - حنفي)': response.asr_hanafi,
            'Maghrib (صلاة المغرب)': response.maghrib,
            'Isha (صلاة العشاء)': response.isha,
            'Midnight (منتصف الليل)': response.midnight
        }

        time_format = "%I:%M %p" if twelve_hour else "%H:%M"

        for name, time_str in prayer_times.items():
            if twelve_hour:
                time_str = dt.strptime(time_str, "%H:%M").strftime(time_format)
            em.add_field(name=f'**{name}**', value=time_str, inline=True)

        await interaction.followup.send(embed=em, ephemeral=hidden)

    group = discord.app_commands.Group(
        name="prayertimes",
        description="Commands related to prayer times.",
        allowed_contexts=discord.app_commands.AppCommandContext(
            guild=True,
            dm_channel=True,
            private_channel=True,
        ),
        allowed_installs=discord.app_commands.AppInstallationType(
            guild=True,
            user=True
        )
    )

    @group.command(name="get", description="Get the prayer times for a location.")
    @discord.app_commands.allowed_installs(guilds=True, users=True)
    @discord.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @discord.app_commands.describe(
        location="The location to get prayer times for.",
        calculation_method="The method to use when calculating the prayer times.",
        hidden="Whether to hide the response from other users.",
        twelve_hour="Returns the prayer times in a 12 hour format."
    )
    async def prayer_times(self, interaction: discord.Interaction, location: str,
                           calculation_method: int = None, hidden: bool = False, twelve_hour: bool = False):
        await interaction.response.defer(thinking=True, ephemeral=hidden)
        await self._prayer_times(interaction, location, calculation_method, hidden, twelve_hour)

    async def _set_calculation_method(self, interaction: discord.Interaction, method_num: int):
        if method_num not in self.calculation_methods.keys():
            return await interaction.followup.send("❌ **Invalid calculation method number.**", ephemeral=True)

        await UserPrayerCalculationMethod(interaction.user.id).update(method_num)
        return await interaction.followup.send(f':white_check_mark: **Successfully updated user calculation method to `{self.calculation_methods[method_num]}`!**', ephemeral=True)

    @group.command(name="set_calculation_method", description="Change your default prayer times calculation method")
    @discord.app_commands.guild_only()
    @discord.app_commands.guild_install()
    @discord.app_commands.describe(
        calculation_method="The prayer time calculation method number, from the choices provided or /prayertimes list_calculation_methods."
    )
    async def set_calculation_method(self, interaction: discord.Interaction, calculation_method: int):
        await interaction.response.defer(thinking=True, ephemeral=True)
        await self._set_calculation_method(interaction, calculation_method)

    @prayer_times.autocomplete('calculation_method')
    @set_calculation_method.autocomplete('calculation_method')
    async def calculation_method_autocomplete_callback(self, interaction: discord.Interaction, current: str):
        return [
            discord.app_commands.Choice(name=v, value=k)
            for k, v in self.calculation_methods.items() if current.lower() in v.lower()
        ]

    async def _list_methods(self, interaction: discord.Interaction):
        em = discord.Embed(colour=0x558a25, description='')
        em.set_author(name='Calculation Methods', icon_url=ICON)
        for method, name in self.calculation_methods.items():
            em.description = f'{em.description}**{method}** - {name}\n'
        return await interaction.followup.send(embed=em, ephemeral=True)

    @group.command(name="list_calculation_methods", description="Sends the list of calculation methods.")
    async def list_methods(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        await self._list_methods(interaction)


async def setup(bot):
    await bot.add_cog(PrayerTimes(bot))
