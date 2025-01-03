import cmd
import re
import math
from string import Formatter
import importlib
from src.globals import *


SharedDBLibraryPath = "../ShinLoungeHub/shared_database.py"
SharedDatabase = None

# Check if the file exists
if path.isfile(SharedDBLibraryPath):
	# Load the module dynamically
	spec = importlib.util.spec_from_file_location("shared_database", SharedDBLibraryPath)
	shared_database_module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(shared_database_module)

	# Import the SharedDatabase class
	SharedDatabase = getattr(shared_database_module, "SharedDatabase", None)
	if SharedDatabase is None:
		raise ImportError("SharedDatabase class not found in the specified module.")

class NumericEnum(Enum):
	def __init__(self, names):
		d = {name: i for i, name in enumerate(names)}
		super(NumericEnum, self).__init__(d)

class CustomFormatter(Formatter):
	def convert_field(self, value, conversion):
		if conversion == "x": # escape
			return escape_html(value)
		elif conversion == "t": # date[t]ime
			return format_datetime(value)
		elif conversion == "d": # time[d]elta
			return format_timedelta(value)
		return super(CustomFormatter, self).convert_field(value, conversion)

# definition of reply class and types

class Reply():
	def __init__(self, type, **kwargs):
		self.type = type
		self.kwargs = kwargs

types = NumericEnum([
	"CUSTOM",
	"SUCCESS",
	"SUCCESS_COMMANDS",
	"SUCCESS_COMMANDS_SETUP",
	"SUCCESS_RULES",
	"SUCCESS_DELETE",
	"SUCCESS_DELETEALL",
	"SUCCESS_WARN",
	"SUCCESS_WARN_DELETE",
	"SUCCESS_WARN_DELETEALL",
	"SUCCESS_BLACKLIST",
	"SUCCESS_BLACKLIST_DELETEALL",
	"SUCCESS_WHITELIST",
	"LOG_CHANNEL",
	"COMMANDS",
	"BOOLEAN_CONFIG",

	
	"CHAT_ANNOUNCE_LIMIT",
	"CHAT_ENTER_NAME",
	"CHAT_JOIN",
	"CHAT_JOIN_FIRST",
	"CHAT_LEAVE",
	"CHAT_REJOIN",
	"CHAT_REJOIN_UNREG",
	"CHAT_REJOIN_NO_HOURS",
	"CHAT_UPLOAD_UPON_JOINING",
	"CHAT_GOOD_STANDING",
	"CHAT_GOOD_STANDING_NO_HOURS",
	"CHAT_REG_REMINDER",
	"USER_IN_CHAT",
	"USER_NOT_IN_CHAT",
	"USER_IS_ADMIN",
	"GIVEN_COOLDOWN",
	"MESSAGE_DELETED",
	"DELETION_QUEUED",
	"PROMOTED_MOD",
	"PROMOTED_ADMIN",
	"KARMA_VOTED_UP",
	"KARMA_VOTED_DOWN",
	"KARMA_NOTIFICATION",
	"KARMA_LEVEL_UP",
	"KARMA_LEVEL_DOWN",
	"TRIPCODE_INFO",
	"TRIPCODE_SET",

	"ERR_NO_ARG",
	"ERR_COMMAND_DISABLED",
	"ERR_NO_REPLY",
	"ERR_COMMANDS_ALREADY_SET_UP",
	"ERR_COMMANDS_REGISTER_FAIL",
	"ERR_NOT_IN_CACHE",
	"ERR_NO_USER",
	"ERR_NO_USER_BY_ID",
	"ERR_ALREADY_WARNED",
	"ERR_INVALID_DURATION",
	"ERR_NOT_IN_COOLDOWN",
	"ERR_COOLDOWN",
	"ERR_BLACKLISTED",
	"ERR_ACTIVE_ELSEWHERE",
	"ERR_CHAT_FULL",
	"ERR_ALREADY_VOTED_UP",
	"ERR_ALREADY_VOTED_DOWN",
	"ERR_VOTE_OWN_MESSAGE",
	"ERR_SPAMMY",
	"ERR_SPAMMY_SIGN",
	"ERR_SPAMMY_VOTE_UP",
	"ERR_SPAMMY_VOTE_DOWN",
	"ERR_SIGN_PRIVACY",
	"ERR_INVALID_TRIP_FORMAT",
	"ERR_NO_TRIPCODE",
	"ERR_MEDIA_LIMIT",
	"ERR_NO_CHANGELOG",
	"ERR_POLL_NOT_ANONYMOUS",
	"ERR_REG_CLOSED",
	"ERR_VOICE_AND_VIDEO_PRIVACY_RESTRICTION",

	"USER_INFO",
	"USER_INFO_MOD",
	"USERS_INFO",
	"USERS_INFO_EXTENDED",

	"PROGRAM_VERSION",
	"PROGRAM_CHANGELOG",
	"HELP",
	"KARMA_INFO",
	"BOT_INFO",
])

# formatting of these as user-readable text

def em(s):
	# make commands clickable by excluding them from the formatting
	s = re.sub(r'[^a-z0-9_-]/[A-Za-z]+\b', r'</em>\g<0><em>', s)
	return "<em>" + s + "</em>"

def smiley(n):
	if n <= 0: return ":)"
	elif n == 1: return ":|"
	elif n <= 3: return ":/"
	else: return ":("

def progress(value, min_value, max_value, size=10):
    assert size > 0, "Invalid size for progress bar"
    assert min_value < max_value, "Invalid value constraints for progress bar"
    if value <= min_value:
        filled = 0
    elif value >= max_value:
        filled = size
    else:
        filled = round((size / (max_value - min_value)) * (value - min_value))
    return "■" * filled + "□" * (size - filled)

format_strs = {
	types.CUSTOM: "{text}",
	types.SUCCESS: "☑",
	types.SUCCESS_COMMANDS: "☑ <i>The commands for {bot_name} lounge have been updated</i>",
	types.SUCCESS_COMMANDS_SETUP: lambda cmds, **_:
		"☑ <i>Commands for {bot_name} have been set-up.\n" +
		"Registered commands:\n" +
		"\n".join([
			"• %s" % (cmd) for cmd in cmds
		]) + '</i>',
	types.SUCCESS_RULES: "☑ <i>The rules for {bot_name} lounge have been updated</i>",
	types.SUCCESS_DELETE: "☑ <i>The message by</i> <b>{id}</b> <i>has been deleted</i>",
	types.SUCCESS_DELETEALL: "☑ <i>All</i> {count} <i>messages by</i> <b>{id}</b> <i>have been deleted</i>",
	types.SUCCESS_WARN: lambda cooldown, **_:
		"☑ <b>{id}</b> <i>has been warned" + (" (cooldown: {cooldown})" if cooldown is not None else "") + "</i>",
	types.SUCCESS_WARN_DELETE: lambda cooldown, **_:
		"☑ <b>{id}</b> <i>has been warned" + (" (cooldown: {cooldown})" if cooldown is not None else "") + " and the message was deleted</i>",
	types.SUCCESS_WARN_DELETEALL: lambda cooldown, **_:
		"☑ <b>{id}</b> <i>has been warned" + (" (cooldown: {cooldown})" if cooldown is not None else "") + " and all {count} messages were deleted</i>",
	types.SUCCESS_BLACKLIST: "☑ <b>{id}</b> <i>has been blacklisted and the message was deleted</i>",
	types.SUCCESS_BLACKLIST_DELETEALL: "☑ <b>{id}</b> <i>has been blacklisted and all {count} messages were deleted</i>",
	types.SUCCESS_WHITELIST: "☑ <b>{id}</b> <i>has been whitelisted for all lounges.</i>",
	types.LOG_CHANNEL: "ShinLounge v{version} started\n"+
						"This is the log channel for: <b>{bot_name}</b> lounge",
	types.COMMANDS: lambda cmds, **_:
		"\n".join([
			"<b>%s:</b> <i>%s</i>" % (cmd.command, cmd.description) for cmd in cmds
		]),
	types.BOOLEAN_CONFIG: lambda enabled, **_:
		"<b>{description!x}</b>: " + (enabled and "enabled" or "disabled"),
	types.CHAT_ANNOUNCE_LIMIT: em("Once you are registered, you need to post a vid every {media_hours} hours to stay live."),
	types.CHAT_ENTER_NAME: em("But first, please enter a username to use in the chat."),
	types.CHAT_JOIN: em("You joined the {bot_name} lounge!"),
	types.CHAT_JOIN_FIRST: em(
			"Since you are the first user that joined {bot_name}, you were made an admin automatically. Press /help to see all available commands.\n" +
			"In case you have yet to set up the commands menu for your bot you can simply use /setup_commands once to register a set of default commands.\n" +
			"\n" +
			"You can define most necessary settings in the configuration file. Don't forget to set up a welcome message using /rules.\n" +
			"Have fun using ShinLounge! 😉"
		),
	types.CHAT_LEAVE: em("You left the {bot_name} lounge!"),
	types.CHAT_REJOIN_NO_HOURS: em("Welcome back to the media bot."),
	types.CHAT_REJOIN_UNREG: em("Welcome back to the media bot. You will need to upload {reg_uploads} video(s) to complete registration (Current number received: {videos_uploaded})."),
	types.CHAT_REJOIN: em("Welcome back to the media bot. As a reminder, you will need to upload media every {media_hours} video(s) to stay live."),
	types.CHAT_UPLOAD_UPON_JOINING: em("Welcome to the media bot. You will need to upload {reg_uploads} video(s) to complete registration (Current number received: {videos_uploaded})."),
	types.CHAT_REG_REMINDER: em("You need to upload {reg_uploads} video(s) to complete registration (Current number received: {videos_uploaded})."),
	types.CHAT_GOOD_STANDING_NO_HOURS: em("You are active here and registered."),
	types.CHAT_GOOD_STANDING: em("You are active here and registered. Just a reminder, you will need to upload media every {media_hours} video(s) to stay live."),
	types.USER_IN_CHAT: em("You're already in the {bot_name} lounge."),
	types.USER_NOT_IN_CHAT: em("You're not in the {bot_name} lounge yet. Use /start to join!"),
	types.USER_IS_ADMIN: em("Welcome. You are the admin and life is good. Obviously, you are the balls! The chat is ready for you to invite users."),
	types.GIVEN_COOLDOWN: lambda deleted, **_:
		em( "You've been handed a cooldown of {duration!d} for this message. Please read the /rules!"+
			(deleted and " (message also deleted)" or "") ),
	types.MESSAGE_DELETED:
		em( "Your message has been deleted. No cooldown has been "
			"given this time, but refrain from posting it again." ),
	types.DELETION_QUEUED: em("{count} messages matched, deletion was queued."),
	types.PROMOTED_MOD: em("You've been promoted to moderator, run /help for a list of commands."),
	types.PROMOTED_ADMIN: em("You've been promoted to admin, run /help for a list of commands."),
	types.KARMA_VOTED_UP: lambda karma_is_pats, **_: em(
			"You just gave this {bot_name} " + ("a pat" if karma_is_pats else "karma") + ", awesome!"
		),
	types.KARMA_VOTED_DOWN: lambda karma_is_pats, **_: em(
			"You just removed " + ("a pat" if karma_is_pats else "karma") + " from this {bot_name}!"
		),
	types.KARMA_NOTIFICATION: lambda karma_is_pats, count, **_: em(
			"You have just " + ("been given" if count > 0 else "lost") + " " + ("a pat" if karma_is_pats else "karma") + "!" +
			(" (use /patinfo to see your pats and pat level" if karma_is_pats else " (use /karmainfo to see your karma and karma level") +
			" or /toggle" + ("pats" if karma_is_pats else "karma") + " to turn these notifications off)"
		),
	types.KARMA_LEVEL_UP: lambda karma_is_pats, **_:
		"<i>Congratulations!\n" +
		"You have reached a new " + ("pat" if karma_is_pats else "karma") + " level:</i>\n" +
		"<b>{level}</b>\n" +
		"<i>Keep posting good stuff!</i>\n" +
		"<i>(Use /toggle" + ("pats" if karma_is_pats else "karma") + " to turn these notifications off)</i>",
	types.KARMA_LEVEL_DOWN: lambda karma_is_pats, **_:
		"<i>Oh no, you lost your " + ("pat" if karma_is_pats else "karma") + " level!\n" +
		"Your current level is:</i>\n" +
		"<b>{level}</b>\n" +
		"<i>Posting some cute pictures might help...</i>\n" +
		"<i>(Use /toggle" + ("pats" if karma_is_pats else "karma") + " to turn these notifications off)</i>",
	types.TRIPCODE_INFO: lambda tripcode, **_:
		"<b>tripcode</b>: " + ("<code>{tripcode!x}</code>" if tripcode is not None else "unset"),
	types.TRIPCODE_SET: em("Tripcode set. It will appear as: ") + "<b>{tripname!x}</b> <code>{tripcode!x}</code>",

	types.ERR_NO_ARG: em("This command requires an argument."),
	types.ERR_COMMAND_DISABLED: em("This command has been disabled."),
	types.ERR_NO_REPLY: em("You need to reply to a message to use this command."),
	types.ERR_COMMANDS_ALREADY_SET_UP: em(
			"Bot commands have already been set up.\n" +
			"You can use /commands to view or re-define them."
		),
	types.ERR_COMMANDS_REGISTER_FAIL: em(
			"Failed to register bot commands."
		),
	types.ERR_NOT_IN_CACHE: em(
			"The message was not found in cache.\n" +
			"This can be either because it is an automatic bot message, because it is older then 24 hours or because the bot has been restarted."
		),
	types.ERR_NO_USER: em("No user found by that name!"),
	types.ERR_NO_USER_BY_ID: em("No user found by that id! Note that all ids rotate every 24 hours."),
	types.ERR_COOLDOWN: em("Your cooldown expires at {until!t}"),
	types.ERR_ALREADY_WARNED: em("A warning has already been issued for this message."),
	types.ERR_INVALID_DURATION: em("You entered an invalid cooldown duration."),
	types.ERR_NOT_IN_COOLDOWN: em("This user is not in a cooldown right now."),
	types.ERR_ACTIVE_ELSEWHERE: em("We've set this to be your currently active lounge. You will receive media here, and {lounge} media will be paused. Make sure you have uploaded enough media to register here."),
	types.ERR_CHAT_FULL: em("Sorry, the chat is full right now. Please try again later."),
	types.ERR_BLACKLISTED: lambda reason, contact, **_:
		em( "You've been blacklisted" + (reason and " for {reason!x}" or "") )+
		( em("\ncontact:") + " {contact}" if contact else "" ),
	types.ERR_ALREADY_VOTED_UP: lambda karma_is_pats, **_: em(
			"You have already given " + ("a pat" if karma_is_pats else "karma") + " for this message"),
	types.ERR_ALREADY_VOTED_DOWN: lambda karma_is_pats, **_: em(
			"You have already removed " + ("a pat" if karma_is_pats else "karma") + " from this message"),
	types.ERR_VOTE_OWN_MESSAGE: lambda karma_is_pats, **_: em(
			"You cannot give or remove yourself " + ("pats" if karma_is_pats else "karma")),
	types.ERR_SPAMMY: em("Your message has not been sent. Avoid sending messages too fast, try again later."),
	types.ERR_SPAMMY_SIGN: em("Your message has not been sent. Avoid using /sign too often, try again later."),
	types.ERR_SPAMMY_VOTE_UP: lambda karma_is_pats, **_: em(
			"Your " + ("pat" if karma_is_pats else "karma") + " was not transmitted.\n" +
			"Avoid using +1 too often, try again later."
		),
	types.ERR_SPAMMY_VOTE_DOWN: lambda karma_is_pats, **_: em(
			"The " + ("pat" if karma_is_pats else "karma") + " was not removed.\n" +
			"Avoid using -1 too often, try again later."
		),
	types.ERR_SIGN_PRIVACY: em("Your account privacy settings prevent usage of the sign feature. Enable linked forwards first."),
	types.ERR_INVALID_TRIP_FORMAT:
		em("Given tripcode is not valid, the format is ")+
		"<code>name#pass</code>" + em("."),
	types.ERR_NO_TRIPCODE: em("You don't have a tripcode set."),
	types.ERR_MEDIA_LIMIT: em("Spam protection triggred! You can't send media or forward messages at this time, try again in {media_limit_period} hours after join."),
	types.ERR_NO_CHANGELOG: em("Changelog not found"),
	types.ERR_POLL_NOT_ANONYMOUS: em("Poll or quiz must be anonymous!"),
	types.ERR_REG_CLOSED: em("Registrations are closed"),
	types.ERR_VOICE_AND_VIDEO_PRIVACY_RESTRICTION:
		em("This message can't be displayed on premium accounts with restricted access to voice and video messages"),

	types.USER_INFO: lambda karma_is_pats, warnings, cooldown, **_:
		"<b>ID</b>: {id}, <b>username</b>: {username!x}\n" +
		"<b>Chat Username</b>: {chat_username}\n" +  # SHIN UPDATE: Add the chat_username field here
		"<b>rank</b>: {rank}\n" +
		"<b>" + ("Pats" if karma_is_pats else "Karma") + "</b>: {karma} ({karmalevel})\n" +
		"<b>Warnings</b>: {warnings} " + smiley(warnings) +
		( " (one warning will be removed on {warnExpiry!t})" if warnings > 0 else "" ) + ", " +
		"<b>Cooldown</b>: " +
		( cooldown and "yes, until {cooldown!t}" or "no" ),
	types.USER_INFO_MOD: lambda karma_is_pats, karma_obfuscated, warnings, cooldown, **_:
		"<b>ID</b>: {id}\n"+
		"<b>rank</b>: {rank} ({rank_i})\n" +
		"<b>" + ("Pats" if karma_is_pats else "Karma") + "</b>: " + ("~" if karma_obfuscated else "") + "{karma}\n"+
		"<b>Warnings</b>: {warnings}" +
		(" (one warning will be removed on {warnExpiry!t})" if warnings > 0 else "") + ", " +
		"<b>Cooldown</b>: " +
		(cooldown and "yes, until {cooldown!t}" or "no"),
	types.USERS_INFO:
		"<b>Total users:</b> {total}\n" +
		"<b>• Active:</b> {active}\n" +
		"<b>• Inactive:</b> {inactive}",
	types.USERS_INFO_EXTENDED:
		"<b>Total users:</b> {total}\n" +
		"<b>• Active:</b> {active}\n" +
		"<b>• Inactive:</b> {inactive}\n" +
		"\n" +
		"<b>Blacklisted:</b> {blacklisted}\n" +
		"<b>In cooldown:</b> {cooldown}",

	types.PROGRAM_VERSION: "<b>SHINLOUNGE</b>",
	types.PROGRAM_CHANGELOG: lambda versions, count=-1, **_:
		"\n\n".join(["<b><u>" + version + "</u></b>\n" +
			"\n".join(
				"• " + (
					"<b>%s:</b> %s" % (
						parts[0].strip(), ":".join(
							parts[slice(1, len(parts))]
						).strip()
					) if len(
						parts := change.split(":")
					) >= 2 else "%s" % change
				) for change in changes
			) for index, (version, changes) in enumerate(
				versions.items()
			) if (count < 0) or (index >= len(versions) - count)
		]),
	types.HELP: lambda rank, karma_is_pats, **_:
		"<b><u>Important commands</u></b>\n"+
		"	/start" +                  " - <i>Join the chat</i>\n" +
		(
		"	/stop" +                   " - <i>Leave the chat</i>\n" +
		"	/info" +                   " - <i>Show info about you</i>\n"
		if rank is not None else "") +
		"	/help" +                   " - <i>Show available commands</i>\n" +
		"\n<b><u>Additional commands</u></b>\n" +
		(
		"	/users" +                  " - <i>Show number of users</i>\n"
		if rank is not None else "") +
		"	/version" +                " - <i>Show bot version</i>\n" +
		"	/changelog" +              " - <i>Show changelog</i>\n" +
		(
		"	/rules" +                  " - <i>Show rules</i>\n" +
		"	/toggledebug" +            " - <i>Toggle debug message</i>\n" +
		"	/sign <i>or</i> /s TEXT" + " - <i>Sign message with your username</i>\n"
		if rank is not None else "") +
		(
		"\n<b><u>" + ("Pat" if karma_is_pats else "Karma") + " commands</u></b>\n" +
		"	+1" +                                              " (reply) - <i>Give " + ("a pat" if karma_is_pats else "karma") + "</i>\n" +
		"	-1" +                                              " (reply) - <i>Remove " + ("a pat" if karma_is_pats else "karma") + "</i>\n" +
		"	/toggle" + ("pats" if karma_is_pats else "karma") +        " - <i>Toggle " + ("pat" if karma_is_pats else "karma") + " notifications</i>\n" +
		"	/" + ("p" if karma_is_pats else "k") + "sign <i>or</i> " +
			"/" + ("p" if karma_is_pats else "k") + "s TEXT" +         " - <i>Sign message with your " + ("pat" if karma_is_pats else "karma") + " level</i>\n" +
		"	/" + ("pat" if karma_is_pats else "karma") + "info" +      " - <i>Show info about your " + ("pat" if karma_is_pats else "karma") + " level</i>\n" +
		(
			"\n<b><u>Mod commands</u></b>\n" +
			"	/info" +              " (reply) - <i>Show info about a user</i>\n" +
			"	/modsay TEXT" +               " - <i>Post mod message</i>\n" +
			"	/warn" +       	      " (reply) - <i>Warn a user</i>\n" +
			"	/remove" +      	  " (reply) - <i>Delete the message</i>\n" +
			"	/removeall" +   	  " (reply) - <i>Delete all messages from a user</i>\n" +
			"	/cooldown DURATION" + " (reply) - <i>Give spicific cooldown and warn</i>\n" +
			"	/delete" +     	      " (reply) - <i>Warn a user and delete the message</i>\n" +
			"	/delete DURATION" +   " (reply) - <i>Delete, warn and give spicific cooldown</i>\n" +
			"	/deleteall" +         " (reply) - <i>Warn a user and delete all messages</i>\n" +
			"	/blacklist REASON" +  " (reply) - <i>Blacklist a user and delete all messages</i>\n"
		if rank >= RANKS.mod else "") +
		(
			"\n<b><u>Admin commands</u></b>\n" +
			"	/adminsay TEXT" +          " - <i>Post admin message</i>\n" +
			"	/rules TEXT" +             " - <i>Define rules (HTML)</i>\n" +
			"	/botinfo" +                " - <i>Show bot system info</i>\n" +
			"	/uncooldown ID/USERNAME" + " - <i>Remove cooldown from a user</i>\n" +
			"	/mod USERNAME" +           " - <i>Promote a user to mod</i>\n" +
			"	/admin USERNAME" +         " - <i>Promote a user to admin</i>\n" +
			"	/commands COMMANDS" +      " - <i>Change bot commands</i>\n"
		if rank >= RANKS.admin else "")  +
			(
			"	/whitelist" +          " - <i>Whitelist user for all lounges</i>\n"
		if rank >= RANKS.admin and SharedDatabase else "")
		if rank is not None else ""),
	types.KARMA_INFO: lambda karma, karma_is_pats, level_karma, next_level_karma, **_:
		"<b>Your level:</b> <i>{level_name}</i>\n" +
		"<b>Next level:</b> <i>{next_level_name}</i>\n" +
		"\n" +
		"<b>" + ("Pats" if karma_is_pats else "Karma") + ":</b> {karma}/" + ("{next_level_karma}" if next_level_karma is not None else "{level_karma}") + "\n" +
		progress(karma, level_karma if level_karma is not None else (karma - 1), next_level_karma if next_level_karma is not None else karma),
	types.BOT_INFO:
		"<b>Python version:</b> {python_ver}\n" +
		"<b>OS:</b> {os}\n" +
		"\n" +
		"<b>Last modification:</b> {last_file_mod!t}\n" +
		"<b>Launched:</b> {launched!t}\n" +
		"<b>Local time:</b> {time}\n" + # Must not use "t" conversion
		"\n" +
		"<b>Cached messages:</b> {cached_msgs:n}\n" +
		"<b>Recently-active users:</b> {active_users:n}"
}

localization = {}

def formatForTelegram(m):
	s = localization.get(m.type)
	if s is None:
		s = format_strs[m.type]
	if type(s).__name__ == "function":
		s = s(**m.kwargs)
	cls = localization.get("_FORMATTER_", CustomFormatter)
	return cls().format(s, **m.kwargs)
