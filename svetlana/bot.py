import hashlib
import logging
import asyncio
import discord

from svetlana.db import Pollers, Alarms

DESCRIPTION = """I respond to the following commands (friends call me 'svet'):
    * Svetlana hi/help - I'll show you this list!
    * Svetlana follow <ID> - I'll keep track of a game with this ID.
    * Svetlana unfollow <N> - I'll stop following this given game.
    * Svetlana alert <N> - I'll alert N hours before a deadline.
    * Svetlana silence <N> - I won't alert N hours before a deadline.
    * Svetlana list - I'll give you a list of the games I'm following.

I will give a notification when a new round starts and two hours before it
starts and will warn you if players have not given their orders yet.

For more info, check out https://gitlab.jhartog.dev/jhartog/svetlana
"""


class DiscordClient(discord.Client):
    """A Discord client which is used to poll WebDiplomacy games."""
    def __init__(self, wd_client, db_file='svetlana.db', polling=True):
        self.wd_client = wd_client
        self._pollers = Pollers(db_file)
        self._alarms = Alarms(db_file)
        if polling:
            asyncio.Task(self._start_poll())
        super().__init__()

    def _follow(self, game_id, channel_id):
        """Start following a given game by adding it to a list."""
        if not channel_id:
            return False

        obj = (game_id, channel_id)
        if obj in self._pollers:
            return False

        self._pollers.append(obj)
        logging.info('Following: %s', self._pollers)
        return True

    def _unfollow(self, game_id, channel_id):
        """Stop following a given game by removing it to a list."""
        if not channel_id:
            return False

        obj = (game_id, channel_id)
        if obj not in self._pollers:
            return False

        self._pollers.remove(obj)
        logging.info('Following: %s', self._pollers)
        return True

    def _add_alert(self, hours):
        """Add an alert for X hours before a deadline."""
        if hours in self._alarms:
            return False

        self._alarms.append(hours)
        logging.info('Alerting at: %s', self._alarms)
        return True

    def _remove_alert(self, hours):
        """Stop alerting X hours before a deadline."""
        if hours not in self._alarms:
            return False

        self._alarms.remove(hours)
        logging.info('Alerting at: %s', self._alarms)
        return True

    async def _start_poll(self, period=1):
        """Keep polling a list of games every X minutes.

        Note that it first waits, then polls to prevent issues with fetching a
        channel before the client is actually logged in.
        """
        while True:
            await asyncio.sleep(60*period)
            for game_id, channel_id in self._pollers:
                try:
                    game = self.wd_client.fetch(game_id)
                    result = self._poll(game, channel_id, period)
                    if result:
                        channel = self.get_channel(channel_id)
                        embed = self._get_embed(game, result)

                        await channel.send(embed=embed)
                except Exception as exc:
                    # logging.error('Error while polling %d: %s', game_id, exc)
                    logging.exception('Error while polling %d: %s', game_id, exc)

    def _poll(self, game, channel_id, period=1):
        """Poll a game. Returns a message, if needed."""
        msg = None
        if game.pregame:
            if game.hours_left == 0 and game.minutes_left == 0:
                msg = f'The game starts in {game.days_left} days!'
        elif game.won:
            self._unfollow(game.game_id, channel_id)
            msg = f'{game.won} has won!'
        elif game.drawn:
            countries = ', '.join(game.drawn)
            self._unfollow(game.game_id, channel_id)
            msg = f'The game was a draw between {countries}!'
        elif game.hours_left == 23 and game.minutes_left >= 60 - (period*1.5):
            msg = 'Starting new round! Good luck :)'

        for hours in self._alarms:
            if game.hours_left == hours and game.minutes_left <= period*1.5:
                if game.not_ready:
                    countries = ', '.join(game.not_ready)
                    msg = f"{hours}h left! These countries aren't ready: " + \
                            countries
                else:
                    msg = f"{hours}h left, everybody's ready!"

        return msg

    @staticmethod
    def _get_embed(game, msg=''):
        embed = discord.Embed(
                title=f'Diplomacy game {game.game_id}',
                description=msg,
                url=game.url,
            )
        embed.set_image(url=game.map_url)

        return embed

    def _answer_message(self, message):
        """React to a message."""
        def _hash(string):
            return hashlib.sha256(string.encode('utf-8')).digest()

        words = message.content.split(' ')
        command = words[1]
        arguments = words[2:]
        msg = None
        logging.debug('Received command: %s', command)

        if command in {'hi', 'hello', 'help'}:
            msg = f'Hello, {message.author.name}!\n{DESCRIPTION}'
        elif command == 'follow':
            game_id = int(arguments[0])
            game = self.wd_client.fetch(game_id)
            if self._follow(game_id, message.channel.id):
                desc = f'Now following {game_id}!'
            else:
                desc = "I'm already following that game!"
            msg = self._get_embed(game, desc)
        elif command == 'unfollow':
            game_id = int(arguments[0])
            if self._unfollow(game_id, message.channel.id):
                msg = 'Consider it done!'
            else:
                msg = 'Huh? What game?'
        elif command == 'alert':
            hours = int(arguments[0])
            if self._add_alert(hours):
                msg = f'OK, I will alert {hours} hours before a deadline.'
            else:
                msg = f"I'm already alerting {hours} hours before a deadline!"
        elif command == 'silence':
            hours = int(arguments[0])
            if self._remove_alert(hours):
                msg = f'Understood, I will stop alerting T-{hours}h..'
            else:
                msg = f"I already don't alert {hours} hours before a deadline?!"
        elif command == 'list':
            game_ids = [id for id, channel_id in self._pollers \
                    if channel_id == message.channel.id]
            msg = f"I'm following: {game_ids}"
        elif _hash(command) == b'\xb9\xa3e\xc4\xd2g]_\xd8\xecwg*+' + \
                b'\xc2\x94t\x18L8\x05\xb5P\xb9\x87\xb60\xc8< \x0c\x9c':
            # There are no easter eggs here, just serious features
            msg = f'pls {command}?'

        return msg

    async def on_message(self, message):
        """Recieve, parse and possibly react to a message."""
        if message.content in {'lol', 'rofl', 'lmao', 'haha', 'hihi'}:
            await message.channel.send('lol xD')

        words = message.content.split(' ')
        if words[0].lower() in {'svetlana', 'svet'}:
            try:
                answer = self._answer_message(message)
                if answer:
                    if isinstance(answer, discord.Embed):
                        await message.channel.send(embed=answer)
                    else:
                        await message.channel.send(answer)
            except Exception as exc:
                logging.warning(exc)
                await message.channel.send('Huh?')
