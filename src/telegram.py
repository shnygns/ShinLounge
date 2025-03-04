import telebot
import time
import logging
import time
import json
import re
import datetime
import threading
from os import path
from ratelimit import limits, sleep_and_retry
import src.core as core
import src.replies as rp
from src.util import MutablePriorityQueue, genTripcode, Scheduler, get_users_active_elsewhere, check_authorization
from src.globals import *
from enum import Enum


SharedDBLibraryPath = "../ShinLoungeHub/shared_database.py"
current_folder_name = path.basename(path.dirname(path.realpath(__file__)))

# module constants
MEDIA_FILTER_TYPES = ("photo", "animation", "document", "video", "sticker")
CAPTIONABLE_TYPES = ("photo", "audio", "animation", "document", "video", "voice")
HIDE_FORWARD_FROM = set([
	"anonymize_bot", "AnonFaceBot", "AnonymousForwarderBot", "anonomiserBot",
	"anonymous_forwarder_nashenasbot", "anonymous_forward_bot", "mirroring_bot",
	"anonymizbot", "ForwardsCoverBot", "anonymousmcjnbot", "MirroringBot",
	"anonymousforwarder_bot", "anonymousForwardBot", "anonymous_forwarder_bot",
	"anonymousforwardsbot", "HiddenlyBot", "ForwardCoveredBot", "anonym2bot",
	"AntiForwardedBot", "noforward_bot", "Anonymous_telegram_bot",
	"Forwards_Cover_Bot", "ForwardsHideBot", "ForwardsCoversBot",
	"NoForwardsSourceBot", "AntiForwarded_v2_Bot", "ForwardCoverzBot",
])
VENUE_PROPS = ("title", "address", "foursquare_id", "foursquare_type", "google_place_id", "google_place_type")

# module variables
bot = None
me = None
db = None
shared_db = None
ch = None
config = None
message_queue = None
CALLS = 25
RATE_LIMIT_PERIOD = 1
registered_commands = {}
tgsched = Scheduler()
blacklisted = set()
active_elsewhere = set()

# settings
allow_documents = None
allow_polls = None
linked_network: dict = None

class AuthorizationStatus(Enum):
    NONE_TYPE = "none_type"
    BLACKLISTED = "blacklisted"
    ADMIN = "admin"
    UNJOINED = "unjoined"
    UNREGISTERED = "unregistered"
    ACTIVE_ELSEWHERE = "active_elsewhere"
    MEDIA_TIMEOUT = "media_timeout"
    USER_LEFT = "user_left"
    CHAT_NOT_FOUND = "chat_not_found"
    ORDINARY = "ordinary"


def init(_config, _db, _sdb, _ch, _bot, _bl, _ae):
	global bot, db, shared_db, ch, config, message_queue, allow_documents, allow_polls, linked_network, tgsched, blacklisted, me, active_elsewhere
	max_retries = 3
	retry_delay = 5
	if _config["bot_token"] == "":
		logging.error("No telegram token specified.")
		exit(1)

	logging.getLogger("urllib3").setLevel(logging.WARNING) # very noisy with debug otherwise
	telebot.apihelper.READ_TIMEOUT = 20

	# SHIN UPDATE: Start a new thread for a job scheduler to use with telegram functionality
	def start_new_thread(func, join=False, args=(), kwargs={}):
		t = threading.Thread(target=func, args=args, kwargs=kwargs)
		if not join:
			t.daemon = True
		t.start()
		if join:
			t.join()

	start_new_thread(tgsched.run)

	# SHIN UPDATE: Bot is now initialized in secretlounge-ng and passed to telegram.init()
	# bot = telebot.TeleBot(config["bot_token"], threaded=False)
	bot = _bot
	for attempt in range(max_retries):
		try:
			me = bot.get_me()
			break  # Exit the loop if successful
		except telebot.apihelper.ApiException as e:
			logging.error("Failed to get bot info (attempt %d/%d): %s", attempt + 1, max_retries, e.result.text)
			if attempt < max_retries - 1:
				time.sleep(retry_delay)  # Wait before retrying
			else:
				logging.error("Giving up after %d attempts", max_retries)
				exit(1)
	db = _db
	shared_db = _sdb
	blacklisted = _bl
	active_elsewhere = _ae
	ch = _ch
	config = _config
	message_queue = MutablePriorityQueue()

	allow_contacts = config["allow_contacts"]
	allow_documents = config["allow_documents"]
	allow_polls = config["allow_polls"]
	linked_network = config.get("linked_network")
	if linked_network is not None and not isinstance(linked_network, dict):
		logging.error("Wrong type for 'linked_network'")
		exit(1)

	types = ["text", "location", "venue"]
	if allow_contacts:
		types += ["contact"]
	if allow_documents:
		types += ["document"]
	if allow_polls:
		types += ["poll"]
	types += ["animation", "audio", "photo", "sticker", "video", "video_note", "voice"]

	cmds = [
		"start", "stop", "setup_commands", "commands",
		"users", "info", "rules",
		"toggledebug", "togglekarma",
		"version", "changelog", "help", "karmainfo", "botinfo",
		"modsay", "adminsay",
		"mod", "admin",
		"warn", "delete", "deleteall", "remove", "removeall",
		"cooldown", "uncooldown",
		"blacklist", "whitelist", "cleanup",
		"s", "sign", "tripcode", "t", "tsign", "ksign", "ks"
	]

	# Pat aliases
	if core.karma_is_pats:
		global cmd_togglepats, cmd_patinfo, cmd_psign, cmd_ps
		cmds += ["togglepats", "patinfo", "psign", "ps"]
		cmd_togglepats = cmd_togglekarma
		cmd_patinfo = cmd_karmainfo
		cmd_psign = cmd_ksign
		cmd_ps = cmd_ks

	for c in cmds: # maps /<c> to the function cmd_<c>
		c = c.lower()
		registered_commands[c] = globals()["cmd_" + c]
	set_handler(relay, content_types=types)

def set_handler(func, *args, **kwargs):
	def wrapper(*args, **kwargs):
		try:
			func(*args, **kwargs)
		except Exception as e:
			logging.exception("Exception raised in event handler")
	bot.message_handler(*args, **kwargs)(wrapper)

def run():
	while True:
		try:
			bot.polling(none_stop=True, long_polling_timeout=45)
		except Exception as e:
			# you're not supposed to call .polling() more than once but I'm left with no choice
			logging.warning("%s while polling Telegram, retrying.", type(e).__name__)
			time.sleep(1)

def register_tasks(sched):
	# cache expiration
	def task():
		ids = ch.expire()
		if len(ids) == 0:
			return
		n = 0
		def f(item):
			nonlocal n
			if item.msid in ids:
				n += 1
				return True
			return False
		message_queue.delete(f)
		if n > 0:
			logging.warning("Failed to deliver %d messages before they expired from cache.", n)

	#SHIN-PROVEMENT: Add a task to check for users recoreded in the hub database every hour
	sched.register(task, hours=6) # (1/4) * cache duration
		

# Wraps a telegram user in a consistent class (used by core.py)
class UserContainer():
	def __init__(self, u):
		self.id = u.id
		self.username = u.username
		self.realname = u.first_name
		if u.last_name is not None:
			self.realname += " " + u.last_name

def split_command(text):
	if " " not in text:
		return text[1:].lower(), ""
	pos = text.find(" ")
	return text[1:pos].lower(), text[pos+1:].strip()

def takesArgument(optional: bool =False):
	def f(func):
		def wrap(ev):
			_, arg = split_command(ev.text)
			if arg == "" and not optional:
				send_answer(ev, rp.Reply(rp.types.ERR_NO_ARG), True)
				return
			return func(ev, arg)
		return wrap
	return f

# Takes core.py function `func`, returns a new function that runs the former with a UserContainer created from an event's from_user
# The new function also sends the resulting message from `func` to the user
def wrap_core(func, reply_to=False):
	def f(ev):
		m = func(UserContainer(ev.from_user))
		send_answer(ev, m, reply_to=reply_to)
	return f

def send_answer(ev, m, reply_to=False):
	if m is None:
		return
	elif isinstance(m, list):
		for m2 in m:
			send_answer(ev, m2, reply_to)
		return

	reply_to = ev.message_id if reply_to else None
	def f(ev=ev, m=m):
		while True:
			try:
				send_to_single_inner(ev.chat.id, m, reply_to=reply_to)
			except telebot.apihelper.ApiException as e:
				retry = check_telegram_exc(e, None)
				if retry:
					continue
				return
			break

	try:
		user = db.getUser(id=ev.from_user.id)
	except KeyError as e:
		user = None # happens on e.g. /start
	put_into_queue(user, None, f)

# TODO: find a better place for this
def allow_message_text(text):
	if text is None or text == "":
		return True
	# Mathematical Alphanumeric Symbols: has convincing looking bold text
	if any(0x1D400 <= ord(c) <= 0x1D7FF for c in text):
		return False
	return True

# determine spam score for message `ev`
def calc_spam_score(ev):
	if not allow_message_text(ev.text) or not allow_message_text(ev.caption):
		return 999

	s = SCORE_BASE_MESSAGE
	if (ev.forward_from is not None or ev.forward_from_chat is not None
		or ev.json.get("forward_sender_name") is not None):
		s = SCORE_BASE_FORWARD
	elif ev.content_type == "photo":
		return SCORE_PHOTO

	if ev.content_type == "sticker":
		return SCORE_STICKER
	elif ev.content_type == "text":
		pass
	else:
		return s
	s += len(ev.text) * SCORE_TEXT_CHARACTER + ev.text.count("\n") * SCORE_TEXT_LINEBREAK
	return s

# Create BotCommand objects out of dictionary and register them
# I know those decorators (or the function) do not belong here, but I haven't found a better way, yet...
@core.requireUser
@core.requireRank(RANKS.admin)
def register_bot_commands(user, cmd_dict: dict):
		cmds = [telebot.types.BotCommand(cmd, dsc) for cmd, dsc in cmd_dict.items()]
		if bot.set_my_commands(cmds):
			logging.info("%s set commands", user)
		else:
			return rp.Reply(rp.types.ERR_COMMANDS_REGISTER_FAIL)

###

# Formatting for user messages, which are largely passed through as-is

class FormattedMessage():
	html: bool
	content: str
	def __init__(self, html, content):
		self.html = html
		self.content = content

class FormattedMessageBuilder():
	text_content: str
	# initialize builder with first argument that isn't None
	def __init__(self, *args):
		self.text_content = next(filter(lambda x: x is not None, args))
		self.inserts = {}
	def get_text(self):
		return self.text_content
	# insert `content` at `pos`, `html` indicates HTML or plaintext
	# if `pre` is set content will be inserted *before* existing insertions
	def insert(self, pos, content, html=False, pre=False):
		i = self.inserts.get(pos)
		if i is not None:
			cat = lambda a, b: (b + a) if pre else (a + b)
			# only turn insert into HTML if strictly necessary
			if i[0] == html:
				i = ( i[0], cat(i[1], content) )
			elif not i[0]:
				i = ( True, cat(escape_html(i[1]), content) )
			else: # not html
				i = ( True, cat(i[1], escape_html(content)) )
		else:
			i = (html, content)
		self.inserts[pos] = i
	def prepend(self, content, html=False):
		self.insert(0, content, html, True)
	def append(self, content, html=False):
		self.insert(len(self.text_content), content, html)
	def enclose(self, pos1, pos2, content_begin, content_end, html=False):
		self.insert(pos1, content_begin, html)
		self.insert(pos2, content_end, html, True)
	def build(self) -> FormattedMessage:
		if len(self.inserts) == 0:
			return
		html = any(i[0] for i in self.inserts.values())
		norm = lambda i: i[1] if i[0] == html else escape_html(i[1])
		s = ""
		for idx, c in enumerate(self.text_content):
			i = self.inserts.pop(idx, None)
			if i is not None:
				s += norm(i)
			s += escape_html(c) if html else c
		i = self.inserts.pop(len(self.text_content), None)
		if i is not None:
			s += norm(i)
		assert len(self.inserts) == 0
		return FormattedMessage(html, s)

# Append inline URLs from the message `ev` to `fmt` so they are preserved even
# if the original formatting is stripped
def formatter_replace_links(ev, fmt: FormattedMessageBuilder):
	entities = ev.caption_entities or ev.entities
	if entities is None:
		return
	for ent in entities:
		if ent.type == "text_link":
			if ent.url.startswith("tg://"):
				continue # doubt anyone needs these
			if "://t.me/" in ent.url and "?start=" in ent.url:
				continue # deep links look ugly and are likely not important
			fmt.append("\n(%s)" % ent.url)

# Add inline links for >>>/name/ syntax depending on configuration
def formatter_network_links(fmt: FormattedMessageBuilder):
	if not linked_network:
		return
	for m in re.finditer(r'>>>/([a-zA-Z0-9]+)/', fmt.get_text()):
		link = linked_network.get(m.group(1).lower())
		if link:
			# we use a tg:// URL here because it avoids web page preview
			fmt.enclose(m.start(), m.end(),
				"<a href=\"tg://resolve?domain=%s\">" % link, "</a>", True)

# Add signed message formatting for User `user` to `fmt`
def formatter_signed_message(user: core.User, fmt: FormattedMessageBuilder):
	fmt.append(" <a href=\"tg://user?id=%d\">" % user.id, True)
	fmt.append("~~" + user.getFormattedName())
	fmt.append("</a>", True)

# Add signed message formatting for User `user` to `fmt`
def formatter_ksigned_message(user: core.User, fmt: FormattedMessageBuilder):
	karma_level = core.getKarmaLevelName(user.karma)
	fmt.append(" <i><b>t. ", True)
	fmt.append(karma_level if karma_level != "" else "???")
	fmt.append("</b></i>", True)

# Add tripcode message formatting for User `user` to `fmt`
def formatter_tripcoded_message(user: core.User, fmt: FormattedMessageBuilder):
	tripname, tripcode = genTripcode(user.tripcode)
	# due to how prepend() works the string is built right-to-left
	fmt.prepend("</code>:\n", True)
	fmt.prepend(tripcode)
	fmt.prepend("</b> <code>", True)
	fmt.prepend(tripname)
	fmt.prepend("<b>", True)

###

# Message sending (queue-related)

class QueueItem():
	__slots__ = ("user_id", "msid", "func")
	def __init__(self, user, msid, func):
		self.user_id = None # who this item is being delivered to
		if user is not None:
			self.user_id = user.id
		self.msid = msid # message id connected to this item
		self.func = func
	def call(self):
		try:
			self.func()
		except Exception as e:
			logging.exception("Exception raised during queued message")

def get_priority_for(user):
	if user is None:
		# user doesn't exist (yet): handle as rank=0, lastActive=<now>
		# cf. User.getMessagePriority in database.py
		return max(RANKS.values()) << 16
	return user.getMessagePriority()

def put_into_queue(user, msid, f):
	message_queue.put(get_priority_for(user), QueueItem(user, msid, f))


def send_thread():
	while True:
		item = message_queue.get()
		item.call()

###

# Message sending (functions)

def is_forward(ev):
	return (ev.forward_from is not None or ev.forward_from_chat is not None
		or ev.json.get("forward_sender_name") is not None)

def should_hide_forward(ev):
	# Hide forwards from anonymizing bots that have recently become popular.
	# The main reason is that the bot API heavily penalizes forwarding and the
	# 'Forwarded from Anonymize Bot' provides no additional/useful information.
	if ev.forward_from is not None:
		return ev.forward_from.username in HIDE_FORWARD_FROM
	return False

def resend_message(chat_id, ev, reply_to=None, force_caption: FormattedMessage=None):
	# Check if the message is either voice or video
	if ev.content_type in ("video_note", "voice"):
		# We need the full Chat object here, because some properties are not available in the ev.chat trait
		tchat = bot.get_chat(chat_id)
		# Check if the user has disabled them
		if tchat.has_restricted_voice_and_video_messages:
			return bot.send_message(chat_id, rp.formatForTelegram(rp.Reply(rp.types.ERR_VOICE_AND_VIDEO_PRIVACY_RESTRICTION)), parse_mode="HTML")

	if should_hide_forward(ev):
		pass
	elif is_forward(ev) and (ev.content_type != "poll"):
		# forward message instead of re-sending the contents
		return bot.forward_message(chat_id, ev.chat.id, ev.message_id)

	kwargs = {}
	if reply_to is not None:
		kwargs["reply_to_message_id"] = reply_to
		kwargs["allow_sending_without_reply"] = True
	if ev.content_type in CAPTIONABLE_TYPES:
		if force_caption is not None:
			kwargs["caption"] = force_caption.content
			if force_caption.html:
				kwargs["parse_mode"] = "HTML"
		else:
			kwargs["caption"] = ev.caption

	# re-send message based on content type
	if ev.content_type == "text":
		return bot.send_message(chat_id, ev.text, **kwargs)
	elif ev.content_type == "photo":
		photo = sorted(ev.photo, key=lambda e: e.width*e.height, reverse=True)[0]
		return bot.send_photo(chat_id, photo.file_id, **kwargs)
	elif ev.content_type == "audio":
		for prop in ("performer", "title"):
			kwargs[prop] = getattr(ev.audio, prop)
		return bot.send_audio(chat_id, ev.audio.file_id, **kwargs)
	elif ev.content_type == "animation":
		return bot.send_animation(chat_id, ev.animation.file_id, **kwargs)
	elif ev.content_type == "document":
		return bot.send_document(chat_id, ev.document.file_id, **kwargs)
	elif ev.content_type == "video":
		return bot.send_video(chat_id, ev.video.file_id, **kwargs)
	elif ev.content_type == "voice":
		return bot.send_voice(chat_id, ev.voice.file_id, **kwargs)
	elif ev.content_type == "video_note":
		return bot.send_video_note(chat_id, ev.video_note.file_id, **kwargs)
	elif ev.content_type == "location":
		kwargs["latitude"] = ev.location.latitude
		kwargs["longitude"] = ev.location.longitude
		return bot.send_location(chat_id, **kwargs)
	elif ev.content_type == "venue":
		kwargs["latitude"] = ev.venue.location.latitude
		kwargs["longitude"] = ev.venue.location.longitude
		for prop in VENUE_PROPS:
			kwargs[prop] = getattr(ev.venue, prop)
		return bot.send_venue(chat_id, **kwargs)
	elif ev.content_type == "contact":
		for prop in ("phone_number", "first_name", "last_name"):
			kwargs[prop] = getattr(ev.contact, prop)
		return bot.send_contact(chat_id, **kwargs)
	elif ev.content_type == "sticker":
		return bot.send_sticker(chat_id, ev.sticker.file_id, **kwargs)
	elif ev.content_type == "poll":
		return bot.forward_message(chat_id, ev.chat.id, ev.message_id)
	else:
		raise NotImplementedError("content_type = %s" % ev.content_type)

# send a message `ev` (multiple types possible) to Telegram ID `chat_id`
# returns the sent Telegram message
@sleep_and_retry
@limits(calls=CALLS, period=RATE_LIMIT_PERIOD)
def send_to_single_inner(chat_id, ev, reply_to=None, force_caption=None, media=None):
	if media:
		return bot.send_media_group(chat_id, media, reply_to_message_id=reply_to)
	if isinstance(ev, rp.Reply):
		kwargs2 = {}
		if reply_to is not None:
			kwargs2["reply_to_message_id"] = reply_to
			kwargs2["allow_sending_without_reply"] = True
		kwargs2["disable_web_page_preview"] = True
		return bot.send_message(chat_id, rp.formatForTelegram(ev), parse_mode="HTML", **kwargs2)
	elif isinstance(ev, FormattedMessage):
		kwargs2 = {}
		if reply_to is not None:
			kwargs2["reply_to_message_id"] = reply_to
			kwargs2["allow_sending_without_reply"] = True
		if ev.html:
			kwargs2["parse_mode"] = "HTML"
		return bot.send_message(chat_id, ev.content, **kwargs2)

	return resend_message(chat_id, ev, reply_to=reply_to, force_caption=force_caption)

# queue sending of a single message `ev` (multiple types possible) to User `user`
# this includes saving of the sent message id to the cache mapping.
# `reply_msid` can be a msid of the message that will be replied to
# `force_caption` can be a FormattedMessage to set the caption for resent media
def send_to_single(ev, msid, user, *, reply_msid=None, force_caption=None, media=None):
	# set reply_to_message_id if applicable
	reply_to = None
	if reply_msid is not None:
		reply_to = ch.lookupMapping(user.id, msid=reply_msid)

	user_id = user.id
	def f():
		while True:
			try:
				ev2 = send_to_single_inner(user_id, ev, reply_to, force_caption, media=media)
			except (telebot.apihelper.ApiException, telebot.apihelper.ApiTelegramException) as e:
				retry = check_telegram_exc(e, user_id)
				if retry:
					continue
				return
			break
		if isinstance(ev2, list):
			# Save the message_id of the first message in the album
			ch.saveMapping(user_id, msid, ev2[0].message_id)
		else:
			ch.saveMapping(user_id, msid, ev2.message_id)
	put_into_queue(user, msid, f)

# delete message with `id` in Telegram chat `user_id`
def delete_message_inner(user_id, id):
	while True:
		try:
			bot.delete_message(user_id, id)
		except telebot.apihelper.ApiException as e:
			retry = check_telegram_exc(e, None)
			if retry:
				continue
			return
		break

# look at given Exception `e`, force-leave user if bot was blocked
# returns True if message sending should be retried
def check_telegram_exc(e, user_id):
	errmsgs = ["bot was blocked by the user", "user is deactivated",
		"PEER_ID_INVALID", "bot can't initiate conversation"]
	if any(msg in e.result.text for msg in errmsgs):
		if user_id is not None:
			core.force_user_leave(user_id)
		return False

	if "Too Many Requests" in e.result.text:
		d = json.loads(e.result.text)["parameters"]["retry_after"]
		d = min(d, 30) # supposedly this is in seconds, but you sometimes get 100 or even 2000
		if d >= 20: # We do not need to log cooldowns of less than 20, this would flood the channel
			logging.warning("API rate limit hit, waiting for %ds", d)
		time.sleep(d)
		return True # retry

	logging.exception("API exception")
	return False

####

# Event receiver: handles all things the core decides to do "on its own":
# e.g. karma notifications, deletion of messages, signed messages
# This does *not* include direct replies to commands or relaying of messages.

@core.registerReceiver
class MyReceiver(core.Receiver):
	@staticmethod
	def reply(m, msid, who, except_who, reply_msid):
		if who is not None:
			return send_to_single(m, msid, who, reply_msid=reply_msid)

		for user in db.iterateUsers():
			if not user.isJoined():
				continue
			if user == except_who and not user.debugEnabled:
				continue
			send_to_single(m, msid, user, reply_msid=reply_msid)
	@staticmethod
	def delete(msids):
		msids_set = set(msids)
		# first stop actively delivering this message
		message_queue.delete(lambda item: item.msid in msids_set)
		# then delete all instances that have already been sent
		msids_owner = []
		for msid in msids:
			tmp = ch.getMessage(msid)
			msids_owner.append(None if tmp is None else tmp.user_id)
		assert len(msids_owner) == len(msids)
		# FIXME: there's a hard to avoid race condition here:
		# if a message is currently being sent, but finishes after we grab the
		# message ids it will never be deleted
		for user in db.iterateUsers():
			if not user.isJoined():
				continue

			for j, msid in enumerate(msids):
				if user.id == msids_owner[j] and not user.debugEnabled:
					continue
				id = ch.lookupMapping(user.id, msid=msid)
				if id is None:
					continue
				user_id = user.id
				def f(user_id=user_id, id=id):
					delete_message_inner(user_id, id)
				# msid=None here since this is a deletion, not a message being sent
				put_into_queue(user, None, f)
		# drop the mappings for this message so the id doesn't end up used e.g. for replies
		for msid in msids_set:
			ch.deleteMappings(msid)
	@staticmethod
	def stop_invoked(user, delete_out):
		# delete pending messages to be delivered *to* the user
		message_queue.delete(lambda item, user_id=user.id: item.user_id == user_id)
		if not delete_out:
			return
		# delete all pending messages written *by* the user too
		def f(item):
			if item.msid is None:
				return False
			cm = ch.getMessage(item.msid)
			if cm is None:
				return False
			return cm.user_id == user.id
		message_queue.delete(f)

####

# Custom logger mapping to specified channel

def log_into_channel(msg, html=False):
	try:
		if (bot is not None) and core.log_channel:
			bot.send_message(core.log_channel, msg, parse_mode= "HTML" if html else None)
	except:
		pass

class ChannelHandler(logging.StreamHandler):
	def emit(self, record):
		log_into_channel(self.format(record))

####

cmd_start = wrap_core(core.user_join)
cmd_stop = wrap_core(core.user_leave)

def cmd_setup_commands(ev):
	c_user = UserContainer(ev.from_user)
	if bot.get_my_commands() != []:
		return send_answer(ev, rp.Reply(rp.types.ERR_COMMANDS_ALREADY_SET_UP, bot_name=core.bot_name), reply_to=True)
	result = register_bot_commands(c_user, DEFAULT_COMMANDS)
	if isinstance(result, rp.Reply):
		return send_answer(ev, result, reply_to=True)
	return send_answer(ev, rp.Reply(rp.types.SUCCESS_COMMANDS_SETUP, bot_name=core.bot_name, cmds=DEFAULT_COMMANDS), reply_to=True)

@takesArgument(optional=True)
def cmd_commands(ev, arg):
	if arg == "":
		cmds = bot.get_my_commands()
		send_answer(ev, rp.Reply(rp.types.COMMANDS, cmds=cmds), reply_to=True)
	else:
		c_user = UserContainer(ev.from_user)
		result = core.set_commands_dict(c_user, arg)
		if result is not None:
			if isinstance(result, rp.Reply):
				return send_answer(ev, result, reply_to=True)
			result = register_bot_commands(c_user, result)
			if isinstance(result, rp.Reply):
				return send_answer(ev, result, reply_to=True)
			return send_answer(ev, rp.Reply(rp.types.SUCCESS_COMMANDS, bot_name=core.bot_name), reply_to=True)

cmd_users = wrap_core(core.get_users)

def cmd_info(ev):
	c_user = UserContainer(ev.from_user)
	if ev.reply_to_message is None:
		return send_answer(ev, core.get_info(c_user), True)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), True)
	return send_answer(ev, core.get_info_mod(c_user, reply_msid), True)

@takesArgument(optional=True)
def cmd_rules(ev, arg):
	c_user = UserContainer(ev.from_user)

	if arg == "":
		send_answer(ev, core.get_rules(c_user), reply_to=True)
	else:
		send_answer(ev, core.set_rules(c_user, arg), reply_to=True)

cmd_toggledebug = wrap_core(core.toggle_debug)
cmd_togglekarma = wrap_core(core.toggle_karma)

@takesArgument(optional=True)
def cmd_tripcode(ev, arg):
	c_user = UserContainer(ev.from_user)

	if arg == "":
		send_answer(ev, core.get_tripcode(c_user))
	else:
		send_answer(ev, core.set_tripcode(c_user, arg))

def cmd_help(ev):
	c_user = UserContainer(ev.from_user)
	user = None
	try:
		user = db.getUser(id=c_user.id)
	except KeyError as e:
		pass
	send_answer(ev, rp.Reply(rp.types.HELP, rank=(user.rank if (user is not None) and user.isJoined() else None), karma_is_pats=core.karma_is_pats), True)

def cmd_karmainfo(ev):
	c_user = UserContainer(ev.from_user)
	send_answer(ev, core.get_karma_info(c_user), True)

def cmd_botinfo(ev):
	c_user = UserContainer(ev.from_user)
	send_answer(ev, core.get_bot_info(c_user), True)

def cmd_version(ev):
	send_answer(ev, rp.Reply(rp.types.PROGRAM_VERSION, version=VERSION, url_catlounge=URL_CATLOUNGE, url_secretlounge=URL_SECRETLOUNGE), True)

def cmd_changelog(ev):
	if path.exists(FILENAME_CHANGELOG):
		changelog = open(FILENAME_CHANGELOG, "r")
		caption = ""
		sections = {}
		for line in changelog.readlines():
			line = line.strip(" \n\r")
			if line != "":
				if re.match("^=.*=$", line):
					caption = line.strip(" =")
					sections[caption] = []
				elif re.match("^\* ", line):
					sections[caption].append(line.lstrip(" *"))
				else:
					sections[caption].append(line)
		send_answer(ev, rp.Reply(rp.types.PROGRAM_CHANGELOG, versions=sections, count=3), True)
	else:
		send_answer(ev, rp.Reply(rp.types.ERR_NO_CHANGELOG), True)

@takesArgument()
def cmd_modsay(ev, arg):
	c_user = UserContainer(ev.from_user)
	arg = escape_html(arg)
	return send_answer(ev, core.send_mod_message(c_user, arg), True)

@takesArgument()
def cmd_adminsay(ev, arg):
	c_user = UserContainer(ev.from_user)
	arg = escape_html(arg)
	return send_answer(ev, core.send_admin_message(c_user, arg), True)

@takesArgument()
def cmd_mod(ev, arg):
	c_user = UserContainer(ev.from_user)
	arg = arg.lstrip("@")
	send_answer(ev, core.promote_user(c_user, arg, RANKS.mod), True)

@takesArgument()
def cmd_admin(ev, arg):
	c_user = UserContainer(ev.from_user)
	arg = arg.lstrip("@")
	send_answer(ev, core.promote_user(c_user, arg, RANKS.admin), True)

def cmd_warn(ev, delete=False, only_delete=False, delete_all=False, cooldown_duration=""):
	c_user = UserContainer(ev.from_user)

	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), True)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), True)
	if only_delete:
		r = core.delete_message(c_user, reply_msid, delete_all)
	else:
		r = core.warn_user(c_user, reply_msid, delete, delete_all, cooldown_duration)
	send_answer(ev, r, True)

@takesArgument(optional=True)
def cmd_delete(ev, arg):
	return cmd_warn(ev, delete=True, cooldown_duration=arg)

@takesArgument(optional=True)
def cmd_deleteall(ev, arg):
	return cmd_warn(ev, delete=True, delete_all=True, cooldown_duration=arg)

cmd_remove = lambda ev: cmd_warn(ev, only_delete=True)

cmd_removeall = lambda ev: cmd_warn(ev, only_delete=True, delete_all=True)

@takesArgument()
def cmd_cooldown(ev, arg):
	return cmd_warn(ev, delete=False, cooldown_duration=arg)

cmd_cleanup = wrap_core(core.cleanup_messages)

@takesArgument()
def cmd_uncooldown(ev, arg):
	c_user = UserContainer(ev.from_user)

	oid, username = None, None
	if len(arg) < 5:
		oid = arg # usernames can't be this short -> it's an id
	else:
		username = arg

	send_answer(ev, core.uncooldown_user(c_user, oid, username), True)

@takesArgument(optional=True)
def cmd_blacklist(ev, arg):
	c_user = UserContainer(ev.from_user)
	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), True)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), True)
	return send_answer(ev, core.blacklist_user(c_user, reply_msid, arg, True), True)


def cmd_whitelist(ev):
	c_user = UserContainer(ev.from_user)
	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), True)
	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), True)
	return send_answer(ev, core.whitelist_user(c_user, reply_msid), True)	


def reaction(ev, modifier):
	c_user = UserContainer(ev.from_user)
	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), True)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), True)

	return send_answer(ev, core.modify_karma(c_user, reply_msid, modifier), True)

def get_album_messages(media_group_id):
    # This function should retrieve all messages from the media group
    # This would typically involve iterating through the incoming updates and matching the media_group_id
    album_messages = []
    for update in bot.get_updates():
        if update.message.media_group_id == media_group_id:
            album_messages.append(update.message)
    return album_messages

def check_user_active_silently(user_id):
    try:
        bot.send_chat_action(user_id, "typing")
        return True  # User is active
    except telebot.apihelper.ApiTelegramException as e:
        if "forbidden" in str(e).lower() or "chat not found" in str(e).lower():
            logging.debug(f"User {user_id} has exited the DM with the bot.")
            return False  # User is no longer active
        else:
            logging.exception("Unexpected error while checking user activity.")
            return False

def relay(ev):
	global active_elsewhere
	global blacklisted
	album_count = 1
	media_packing = config.get("media_packing", True) 
	user = ev.from_user
	if user and shared_db:
		user_id = user.id
		user_full_name = user.full_name
		user_username = user.username
		active_elsewhere = get_users_active_elsewhere(shared_db, config)
		blacklisted = shared_db.get_list_of_banned_users()

	# SHIN UPDATE: Functions to send media group videos as an album

	def active_elsewhere_reply(user, shared_db, config):
		if shared_db is None:
			return
		if user.id in active_elsewhere and not (db_user and db_user.rank >= RANKS.mod and db_user.registered):
			active_lounge = shared_db.get_user_current_lounge_name(user.id)
			try:
				bot.send_message(user.id, f"<em>Just so you know, because you are currently active in <strong>{active_lounge}</strong>, you will not see media in this lounge. You must leave that bot first and give time to let it refresh.</em>", parse_mode="HTML")
			except telebot.apihelper.ApiTelegramException as e:
				logging.exception(f"Error while communicating to user about active lounge: {e}")
		return

	def send_media_as_album(data=None, ev=None):
		media_group_id = ev.media_group_id
		# Scheduler list member order is [name, func, data, interval, first_run, ev]
		if not data:
			logging.warning(f"No videos found for media group {media_group_id}")
			return
		
		# Use the first message in the album as a template for the album post
		ev_template = ev 
		relay_inner(ev_template, album_files=data)

	def send_packed_media_as_album(data=None, ev=None):
		# Process up to 10 media files, and send them as an album
		# If there is remainder media upon job execution, remainder should be sent to a newly registered job
		if not data or not isinstance(data, list):
			logging.error(f"Data absent or unexpected data structure for media group job: {data}")
			return
		media_files_to_send = data[:10]
		if len(media_files_to_send) == 1:
			# If there is only one media file, send it as a single message
			relay_inner(ev, album_files=[])
			return
		remaining_media_files = data[10:]
		if remaining_media_files:
			tgsched.register(send_packed_media_as_album, name="media_packing", data=remaining_media_files, ev=ev)
		#else:
		#	active_elsewhere_reply(user, shared_db, config)
		relay_inner(ev, album_files=media_files_to_send)

	def handle_media_group(ev):
		media_group_id = ev.media_group_id
		media_type = ev.content_type
		media_file_id = None

		if media_type == "video":
			media_file_id = ev.video.file_id
		elif media_type == "photo":
			media_file_id = ev.photo[-1].file_id  # Get the highest resolution photo
		elif media_type == "document":
			media_file_id = ev.document.file_id
		elif media_type == "audio":
			media_file_id = ev.audio.file_id

		if media_file_id is None:
			logging.warning(f"Unsupported media type: {media_type}")
			return

		job = tgsched.get_job_by_name(str(media_group_id))
		if job:
			if not isinstance(job[2], list):
				logging.error(f"Unexpected data structure for media group job: {job[2]}")
				return
			job_data = job[2]
			media_types_in_job = [i['media_type'] for i in job_data]
			if 'audio' in media_types_in_job and media_type != 'audio':
				logging.warning(f"Audio albums cannot mix other media types. Ignoring {media_type} file.")
				return
			elif 'document' in media_types_in_job and media_type != 'document':
				logging.warning(f"Document albums cannot mix other media types. Ignoring {media_type} file.")
				return
			else:
				job_data.append({'file_id': media_file_id, 'media_type': media_type})
		else:
			tgsched.register(send_media_as_album, name=str(media_group_id), data=[{'file_id': media_file_id, 'media_type': media_type}], ev=ev)
			active_elsewhere_reply(user, shared_db, config)
		return
	
	def pack_media(ev):
		# Register or add to a tsched job that will assemble media in groups of 10 and then post them as an album
		# If there is remainder media upon job execution, remainder should be sent to a newly registered job
		from_user = ev.from_user
		media_type = ev.content_type
		if media_type == "video":
			media_file_id = ev.video.file_id
		elif media_type == "photo":
			media_file_id = ev.photo[-1].file_id  # Get the highest resolution photo
		job = tgsched.get_job_by_name(f"media_packing_{from_user}")
		if job:
			job_data = job[2]
			job_data.append({'file_id': media_file_id, 'media_type': media_type})
		else:
			tgsched.register(send_packed_media_as_album, name=f"media_packing_{from_user}", data=[{'file_id': media_file_id, 'media_type': media_type}], ev=ev, first_run = 1)
		return
	

	# handle commands and karma giving
	if ev.content_type == "text":
		if ev.text.startswith("/"):
			c, _ = split_command(ev.text)
			if c in registered_commands.keys():
				registered_commands[c](ev)
			return
		elif ev.text.strip() == "+1":
			active_elsewhere_reply(user, shared_db, config)
			return reaction(ev, core.karma_amount_add)
		elif ev.text.strip() == "-1":
			active_elsewhere_reply(user, shared_db, config)
			return reaction(ev, -core.karma_amount_remove)
	# prohibit non-anonymous polls
	if ev.content_type == "poll":
		if not ev.poll.is_anonymous:
			return send_answer(ev, rp.Reply(rp.types.ERR_POLL_NOT_ANONYMOUS))
		
	try:
		db_user = db.getUser(id=user.id)
	except KeyError as e:
		db_user = None

	if shared_db and db_user and db_user.isJoined():
		active_user_count = db.count_active_users()
		shared_db.ping(me.username, config["bot_token"], active_user_count = active_user_count)
		shared_db.update_user(user_id, user_full_name, user_username, me.username, config["bot_token"])


	#SHIN UPDATE - Check if the message is part of an album
	if media_packing and ev.content_type in ['video', 'photo']:
		pack_media(ev)
		return
	elif ev.media_group_id:
		handle_media_group(ev)
		return

	# manually handle signing / tripcodes for media since captions don't count for commands
	if not is_forward(ev) and ev.content_type in CAPTIONABLE_TYPES and (ev.caption or "").startswith("/"):
		c, arg = split_command(ev.caption)
		if c in ("s", "sign"):
			return relay_inner(ev, caption_text=arg, signed=True, album_count=album_count)
		elif c in ("t", "tsign"):
			return relay_inner(ev, caption_text=arg, tripcode=True, album_count=album_count)

	relay_inner(ev)

# relay the message `ev` to other users in the chat
# `caption_text` can be a FormattedMessage that overrides the caption of media
# `signed` and `tripcode` indicate if the message is signed or tripcoded respectively
def relay_inner(ev, *, caption_text=None, signed=False, tripcode=False, ksigned=False, album_files=[]):
	media = []
	media_types_in_album = []
	videos_in_album = []
	reg_uploads = config.get("reg_uploads", 5) # default to 5 if not set
	media_hours = config.get("media_hours") 
	blacklist_contact = config.get("blacklist_contact")
	is_media = is_forward(ev) or ev.content_type in MEDIA_FILTER_TYPES

	msid = core.prepare_user_message(UserContainer(ev.from_user), calc_spam_score(ev),
		is_media=is_media, signed=signed, tripcode=tripcode, ksigned=ksigned)
	if msid is None or isinstance(msid, rp.Reply):
		return send_answer(ev, msid) # don't relay message, instead reply
	
	user = db.getUser(id=ev.from_user.id)

	if user.id in blacklisted:
		return send_answer(ev, rp.Reply(rp.types.ERR_BLACKLISTED, reason="You have been iniversally blacklisted from the lounge groups.", contact = blacklist_contact))

	if album_files:
		if isinstance(album_files, list):
			for album_file in album_files:
				file_id = album_file.get('file_id')
				media_type = album_file.get('media_type')
				if media_type == "video":
					media.append(telebot.types.InputMediaVideo(file_id))
				elif media_type == "photo":
					media.append(telebot.types.InputMediaPhoto(file_id))
				elif media_type == "document":
					media.append(telebot.types.InputMediaDocument(file_id))
				elif media_type == "audio":
					media.append(telebot.types.InputMediaAudio(file_id))
				else:
					logging.warning(f"Unsupported media type for album: {media_type}")
					return
			media_types_in_album = [file['media_type'] for file in album_files]
			videos_in_album = [file['file_id'] for file in album_files if file['media_type'] == 'video']
		else:
			logging.error("album_files is not a list.")
		


	# SHIN UPDATE: Check if the message is a video
	if ev.content_type == "video" or (album_files and "video" in media_types_in_album):
		with db.modifyUser(id=user.id) as user:
			video_count_to_add = max(len(videos_in_album), 1) if album_files else 1
			user.media_count = (user.media_count or 0) + video_count_to_add
			user.last_media = datetime.datetime.utcnow()
			logging.info(f"User {user.id} - {user.chat_username} has posted {user.media_count} video messages.")

			# If the media count reaches [reg_uploads], mark the user as registered
			try:
				if reg_uploads and not user.registered:
					if user.media_count >= reg_uploads:
						user.registered = datetime.datetime.utcnow()
						logging.info(f"User {user.id} - {user.chat_username} has been registered due to posting {reg_uploads} or more video messages.")
						bot.send_message(user.id, "Thank you. You are now registered, and will see messages from the group.")
						bot.send_message(user.id, f"As a reminder, you need to post a video every {media_hours} hours to stay live.")
					elif user.media_count < reg_uploads:
						bot.send_message(user.id, f"Thank you. Please upload {reg_uploads - user.media_count} more video messages to be registered.")
			except Exception as e:
				logging.exception(f"Error while communicating to user about registration status: {e}")


	# for signed msgs: check user's forward privacy status first
	# FIXME? this is a possible bottleneck
	if signed:
		tchat = bot.get_chat(user.id)
		if tchat.has_private_forwards:
			return send_answer(ev, rp.Reply(rp.types.ERR_SIGN_PRIVACY))

	# apply text formatting to text or caption (if media)
	ev_tosend = ev
	force_caption = None
	#if is_forward(ev):
		# pass # leave message alone
	if ev.content_type == "text" or ev.caption is not None or caption_text is not None:
		fmt = FormattedMessageBuilder(caption_text, ev.caption, ev.text)
		formatter_replace_links(ev, fmt)
		formatter_network_links(fmt)

		#SHIN UPDATE - Prepend the chat_username
		if user.chat_username:
			chat_username_display = f"<b>{user.chat_username}</b>:\n"
			fmt.prepend(chat_username_display, html=True)
		if ksigned:
			formatter_ksigned_message(user, fmt)
		elif signed:
			formatter_signed_message(user, fmt)
		elif tripcode:
			formatter_tripcoded_message(user, fmt)
		fmt = fmt.build()
		# either replace whole message or just the caption
		if ev.content_type == "text":
			ev_tosend = fmt or ev_tosend
		else:
			force_caption = fmt

	# find out which message is being replied to
	reply_msid = None
	if ev.reply_to_message is not None:
		reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
		# There is no benefit of having this logged in the console/channel...
		#if reply_msid is None:
		#	logging.warning("Message replied to not found in cache")

	# relay message to all other users
	logging.debug("relay(): msid=%d reply_msid=%r", msid, reply_msid)
	for user2 in db.iterateUsers():
		auth_dict = check_authorization(user2, config, blacklisted, active_elsewhere, db, bot, shared_db)
		can_receive = auth_dict['can_receive']
		reply_status = " (SENT)" if can_receive else " (WITHHELD)"
		reply = auth_dict['log_message'] + reply_status

		#if user2.username and any(substring in user2.username for substring in ["shinanygans", "shins_bot_testing_bitch"]):
		#	logging.info(reply)

		if not can_receive:
			continue

		if user2 == user and not user.debugEnabled:
			ch.saveMapping(user2.id, msid, ev.message_id)
			continue

		send_to_single(ev_tosend, msid, user2,
			reply_msid=reply_msid, force_caption=force_caption, media=media)

@takesArgument()
def cmd_sign(ev, arg):
	ev.text = arg
	relay_inner(ev, signed=True)

cmd_s = cmd_sign # alias

@takesArgument()
def cmd_ksign(ev, arg):
	ev.text = arg
	relay_inner(ev, ksigned=True)

cmd_ks = cmd_ksign # alias

@takesArgument()
def cmd_tsign(ev, arg):
	ev.text = arg
	relay_inner(ev, tripcode=True)

cmd_t = cmd_tsign # alias