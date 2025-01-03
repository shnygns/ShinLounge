import logging
import random
import sys
import threading
import time
from datetime import datetime, timedelta
from threading import Lock
import src.replies as rp
from src.globals import *
from src.database import User, SystemConfig
from src.cache import CachedMessage
from src.util import genTripcode, getLastModFile, get_users_active_elsewhere, check_authorization, AuthorizationStatus

launched = None

db = None
shared_db = None
ch = None
config = None
spam_scores = None
sign_last_used = {} # uid -> datetime
vote_up_last_used = {} # uid -> datetime
vote_down_last_used = {} # uid -> datetime

reg_open = None
log_channel = None
karma_amount_add = None
karma_amount_remove = None
karma_level_names = None
bot_name = None
karma_is_pats = None
blacklist_contact = None
enable_signing = None
allow_remove_command = None
media_limit_period = None
sign_interval = None
vote_up_interval = None
vote_down_interval = None
blacklisted = set()
active_elsewhere = set()
max_users = None

bot = None  # Add a global variable for the bot instance

def init(_config, _db, _sdb, _ch, _bot, _bl, _ae):
	global launched, db, shared_db, ch, bot, config, spam_scores, reg_open, log_channel, karma_amount_add, karma_amount_remove, karma_level_names, blacklist_contact, bot_name, karma_is_pats, enable_signing, allow_remove_command, media_limit_period, sign_interval, vote_up_interval, vote_down_interval, blacklisted, active_elsewhere, max_users

	# Store bot instance
	bot = _bot
	config = _config
	launched = datetime.now()

	db = _db
	shared_db = _sdb
	blacklisted = _bl
	active_elsewhere = _ae
	max_users = config.get("max_users", 1000)
	ch = _ch
	spam_scores = ScoreKeeper()

	reg_open = config.get("reg_open", "")
	log_channel = config.get("log_channel", False)
	if log_channel:
		logging.info("Log channel: %d", log_channel)
	karma_amount_add = config.get("karma_amount_add", 1)
	karma_amount_remove = config.get("karma_amount_remove", 1)
	karma_level_names = config.get("karma_level_names", None)
	bot_name = config.get("bot_name", "")
	karma_is_pats = config.get("karma_is_pats", False)
	blacklist_contact = config.get("blacklist_contact", "")
	enable_signing = config["enable_signing"]
	allow_remove_command = config["allow_remove_command"]
	if "media_limit_period" in config.keys():
		media_limit_period = timedelta(hours=int(config["media_limit_period"]))
	sign_interval = timedelta(seconds=int(config.get("sign_limit_interval", 600)))
	vote_up_interval = timedelta(seconds=int(config.get("vote_up_limit_interval", 0)))
	vote_down_interval = timedelta(seconds=int(config.get("vote_down_limit_interval", 60)))

	if config.get("locale"):
		rp.localization = __import__("src.replies_" + config["locale"],
			fromlist=["localization"]).localization

	# initialize db if empty
	if db.getSystemConfig() is None:
		c = SystemConfig()
		c.defaults()
		db.setSystemConfig(c)

def register_tasks(sched):
	# spam score handling
	sched.register(spam_scores.scheduledTask, seconds=SPAM_INTERVAL_SECONDS)
	# warning removal
	def task():
		now = datetime.now()
		for user in db.iterateUsers():
			if not user.isJoined():
				continue
			if user.warnExpiry is not None and now >= user.warnExpiry:
				with db.modifyUser(id=user.id) as user:
					user.removeWarning()
	sched.register(task, minutes=15)

def updateUserFromEvent(user, c_user):
	user.username = c_user.username
	user.realname = c_user.realname
	user.lastActive = datetime.now()

def getUserByName(username):
	username = username.lower()
	# there *should* only be a single joined user with a given username
	for user in db.iterateUsers():
		if not user.isJoined():
			continue
		if user.username is not None and user.username.lower() == username:
			return user
	return None

def getUserByOid(oid):
	for user in db.iterateUsers():
		if not user.isJoined():
			continue
		if user.getObfuscatedId() == oid:
			return user
	return None

def getRecentlyActiveUsers():
	users = db.iterateUsers()
	cache_start_datetime = max(launched, datetime.now() - timedelta(hours=24))
	count = 0
	for user in users:
		if (user.lastActive is not None) and (user.lastActive > cache_start_datetime):
			count += 1
	return count

def getKarmaLevel(karma):
	karma_level = 0
	while (karma_level < len(KARMA_LEVELS)) and (karma >= KARMA_LEVELS[karma_level]):
		karma_level += 1
	return karma_level

def getKarmaLevelName(karma):
	if karma_level_names is not None:
		assert len(karma_level_names) == len(KARMA_LEVELS) + 1
		return karma_level_names[getKarmaLevel(karma)]
	return ""

def requireUser(func):
	def wrapper(c_user, *args, **kwargs):
		if isinstance(c_user, User):
			user = c_user
		else:
			# fetch user from db
			try:
				user = db.getUser(id=c_user.id)
			except KeyError as e:
				return rp.Reply(rp.types.USER_NOT_IN_CHAT, bot_name=bot_name)

		# keep db entry up to date with current usernames and last activity
		with db.modifyUser(id=user.id) as user:
			updateUserFromEvent(user, c_user)

		# check for blacklist or absence
		if user.isBlacklisted():
			return rp.Reply(rp.types.ERR_BLACKLISTED, reason=user.blacklistReason, contact=blacklist_contact)
		elif not user.isJoined():
			return rp.Reply(rp.types.USER_NOT_IN_CHAT, bot_name=bot_name)

		# call original function
		return func(user, *args, **kwargs)
	return wrapper

def requireRank(need_rank):
	def f(func):
		def wrapper(user, *args, **kwargs):
			if not isinstance(user, User):
				raise SyntaxError("you fucked up the decorator order")
			if user.rank < need_rank:
				return
			return func(user, *args, **kwargs)
		return wrapper
	return f

###

# RAM cache for spam scores

class ScoreKeeper():
	def __init__(self):
		self.lock = Lock()
		self.scores = {}
	def increaseSpamScore(self, uid, n):
		with self.lock:
			s = self.scores.get(uid, 0)
			if s > SPAM_LIMIT:
				return False
			elif s + n > SPAM_LIMIT:
				self.scores[uid] = SPAM_LIMIT_HIT
				return s + n <= SPAM_LIMIT_HIT
			self.scores[uid] = s + n
			return True
	def scheduledTask(self):
		with self.lock:
			for uid in list(self.scores.keys()):
				s = self.scores[uid] - 1
				if s <= 0:
					del self.scores[uid]
				else:
					self.scores[uid] = s

###

# Event receiver template and Sender class that fwds to all registered event receivers

class Receiver():
	@staticmethod
	def reply(m: rp.Reply, msid: int, who, except_who, reply_to: bool):
		raise NotImplementedError()
	@staticmethod
	def delete(msids: list[int]):
		raise NotImplementedError()
	@staticmethod
	def stop_invoked(who, delete_out: bool):
		raise NotImplementedError()

class Sender(Receiver): # flawless class hierachy I know...
	receivers = []
	@staticmethod
	def reply(m, msid, who, except_who, reply_to):
		logging.debug("reply(m.type=%s, msid=%r, reply_to=%r)", rp.types.reverse[m.type], msid, reply_to)
		for r in Sender.receivers:
			r.reply(m, msid, who, except_who, reply_to)
	@staticmethod
	def delete(msids):
		logging.debug("delete(msids=%r)", msids)
		for r in Sender.receivers:
			r.delete(msids)
	@staticmethod
	def stop_invoked(who, delete_out=False):
		logging.debug("stop_invoked(who=%s)", who)
		for r in Sender.receivers:
			r.stop_invoked(who, delete_out)

def registerReceiver(obj):
	assert issubclass(obj, Receiver)
	Sender.receivers.append(obj)
	return obj




###################################
######## Shin's New Code #########
###################################



def user_join(c_user):
	reg_uploads = config.get("reg_uploads", 5) 
	media_hours = config.get("media_hours", None)
	bot_token = config.get("bot_token", None)
	videos_uploaded=0
	me = bot.get_me()

	global active_elsewhere
	active_elsewhere = get_users_active_elsewhere(shared_db, config)

	try:
		user = db.getUser(id=c_user.id)
	except KeyError as e:
		user = None

	# If the user is already in the database, meaning that they previously joined the chat...
	if user is not None:
		videos_uploaded = user.media_count

		# check if they are an allowed user, and send the correct response
		msg = None
		auth_dict = check_authorization(user, config, blacklisted, active_elsewhere, db, bot, shared_db)
		msg = auth_dict["join_reply_msg"] if auth_dict["join_reply_msg"] is not None else None

		with db.modifyUser(id=user.id) as user:
			updateUserFromEvent(user, c_user)

		if auth_dict["status"] != AuthorizationStatus.ADMIN and not (c_user.username and "shinanygans" in c_user.username):
			if auth_dict["can_join"] and db.count_active_users() >= max_users:
				msg = rp.Reply(rp.types.CHAT_FULL, media_hours = media_hours) if msg is None else msg

		if auth_dict["can_join"]:
			with db.modifyUser(id=user.id) as user:
				user.setLeft(False)
			threading.Thread(target=prompt_username, args=(user,)).start()

		if auth_dict["log_message"] is not None:
			logging.info(auth_dict["log_message"])

		# Make this the currently active lounge in the shared db
		if shared_db is not None:
			shared_db.update_user(c_user.id, c_user.realname, c_user.username, me.username, bot_token, currently_joined=True) 
		return msg



	"""
		if (user.isBlacklisted() or user.id in blacklisted):
			err = rp.Reply(rp.types.ERR_BLACKLISTED, reason=user.blacklistReason, contact=blacklist_contact)

		if user.id in active_elsewhere and user.rank < RANKS.mod:
			active_lounge = shared_db.get_user_current_lounge_name(user.id)
			err = rp.Reply(rp.types.ERR_ACTIVE_ELSEWHERE, lounge=active_lounge)

		# If the user errors out, keep db entry up to date with current usernames and last activity and exit
		if err is not None:
			with db.modifyUser(id=user.id) as user:
				updateUserFromEvent(user, c_user)
			return err
		
		# This is an allowed user. If they are not currently joined and are trying to rejoin...
		if not user.isJoined():
			# ...see if there is room at the inn...
			if db.count_active_users() >= max_users and not (c_user.username and ("shinanygans" not in c_user.username or "clvrYptq" not in c_user.username)):
				err = rp.Reply(rp.types.ERR_CHAT_FULL)
				with db.modifyUser(id=user.id) as user:
					updateUserFromEvent(user, c_user)
				return err

			# ...if the lounge is not full, let them back in.
			with db.modifyUser(id=user.id) as user:
				updateUserFromEvent(user, c_user)
				user.setLeft(False)
			logging.info("%s rejoined chat", user)
			bot.send_message(c_user.id, f"<em>Welcome back! As a reminder, you need to post a vid every {media_hours} hours to stay live.</em>", parse_mode="HTML")
			return

		# If the user is already joined, send them registration status and make sure they have a username
		if user.rank == RANKS.admin:
			bot.send_message(user.id, f"<em>Welcome. You are the admin and life is good. Obviously, you are the balls! The chat is ready for you to invite users.</em>", parse_mode="HTML")

		# Check if user has uploaded enough videos to register
		elif not user.registered:
			if not reg_uploads or (reg_uploads > 0 and user.media_count > reg_uploads):
				user.registered = datetime.datetime.utcnow()
				logging.info(f"User {user.id} - {user.chat_username} has been registered due to posting {reg_uploads} or more video messages.")
				bot.send_message(user.id, "<em>Welcome! You are now registered, and will see messages from the group.</em>", parse_mode="HTML")
				if media_hours:
					bot.send_message(c_user.id, f"<em>Just a reminder, you need to post a video every {media_hours} hours to stay live.</em>", parse_mode="HTML")

			# Check if the user is required to upload videos, and the number of videos yet uploaded in not enough
			elif user.rank != RANKS.admin and reg_uploads and reg_uploads > 0 and user.media_count < reg_uploads:
				bot.send_message(c_user.id, f"<em>Welcome! Please upload {reg_uploads} video(s) to complete registration (Current number received: {videos_uploaded}).</em>", parse_mode="HTML")
		else:
			bot.send_message(c_user.id, "<em>Welcome! You are registered, and will see messages from the group.</em>", parse_mode="HTML")
			if media_hours:
				bot.send_message(c_user.id, f"<em>Just a reminder, you need to post a video every {media_hours} hours to stay live.</em>", parse_mode="HTML")

		# SHIN UPDATE - make sure user has username
		if not user.chat_username:
			bot.send_message(c_user.id, "<em>But first, you don't have a username set. Please enter a username to use in the chat.</em>", parse_mode="HTML")
			bot.register_next_step_handler_by_chat_id(c_user.id, get_username, user)
		return 
	"""
	# If, however, the user is brand new and has not previuosly joined the chat, first check if user is allowed to join...
	if not c_user.username or (c_user.username and "shinanygans" not in c_user.username):
		if not reg_open:
			logging.info("User %s tried to join but registration is closed", c_user)
			return rp.Reply(rp.types.ERR_REG_CLOSED)
		
		if c_user.id in blacklisted:
			logging.info("User %s tried to join but is blacklisted", c_user)
			return rp.Reply(rp.types.ERR_BLACKLISTED, "You have been universally blacklisted from lounge groups.", blacklist_contact)
		
		# If the chat is full, return error message
		if db.count_active_users() >= max_users:
			logging.info("User %s tried to join but chat is full", c_user)
			return rp.Reply(rp.types.ERR_CHAT_FULL)
	
	# Then, create new user
	user = User()
	user.defaults()
	user.id = c_user.id
	updateUserFromEvent(user, c_user)

	reply_message=None
	

	# If there are no users in the db, first user becomes admin
	if not any(db.iterateUserIds()):
		user.rank = RANKS.admin
		user.registered = datetime.utcnow()
		reply_message = rp.Reply(rp.types.CHAT_JOIN_FIRST, bot_name=me.username)
		
	# If there are users in the db, set activity and upload status
	else:
		if shared_db and user.id in active_elsewhere and user.rank < RANKS.mod:
			active_lounge = shared_db.get_user_current_lounge_name(user.id)
			reply_message =  rp.Reply(rp.types.ERR_ACTIVE_ELSEWHERE, lounge = active_lounge)

		# If reg_uploads is deactivated, register user automatically
		if not reg_uploads:
			user.registered = datetime.utcnow()
			reply_message = rp.Reply(rp.types.CHAT_JOIN, bot_name=me.username)

		# Prompt user to upload {reg_upload} numver of videos to register
		elif reg_uploads > 0:
			reply_message = rp.Reply(rp.types.CHAT_UPLOAD_UPON_JOINING, reg_uploads=reg_uploads, videos_uploaded=videos_uploaded)

	# If chat_username does not exist, prompt for it.
	threading.Thread(target=prompt_username, args=(user,)).start()

	logging.info("%s joined chat as a new user with rank %s", user, user.rank)
	db.addUser(user)

	# Make this the currently active lounge in the shared db
	if shared_db is not None:
		shared_db.update_user(c_user.id, c_user.realname, c_user.username, me.username, bot_token, currently_joined=True) 
	return reply_message


def prompt_username(user):
	if user.chat_username:
		return
	try:
		time.sleep(1)
		bot.send_message(user.id, "<em>We don't have a username set for you. Before rejoining, please enter a username to use in the chat.</em>", parse_mode="HTML")
		bot.register_next_step_handler_by_chat_id(user.id, get_username, user)
	except Exception as e:
		logging.error("Error in user_join: %s", e)

def get_username(message, user):
	chat_username = message.text.strip()
	if not chat_username:
		chat_username = generate_username()  # Generate a random username if none is provided

	# Save the username and add the user to the database
	user.chat_username = chat_username
	db.setUser(user.id, user)
	logging.info("%s updated username to %s", user, chat_username) 

	# Prompt for username
	try:
		bot.send_message(user.id, f"<em>Great. Your chat username will be <strong>{chat_username}.</strong></em>", parse_mode="HTML")
		if user.rank == RANKS.admin:
			bot.send_message(user.id, f"<em>You can now invite other users to join the bot.</em>", parse_mode="HTML")
		else:
			bot.send_message(user.id, f"<em>You are free to upload media and register.</em>", parse_mode="HTML")
	except Exception as e:
		logging.error("Error in get_username: %s", e)
	return None

def generate_username():
    adjectives = [
        "Swift", "Silent", "Brave", "Fierce", "Curious", "Lucky", "Clever", "Bold",
        "Mysterious", "Noble", "Quick", "Quiet", "Fearless", "Gentle", "Witty", "Charming"
    ]
    animals = [
        "Fox", "Lion", "Eagle", "Hawk", "Bear", "Panther", "Wolf", "Tiger",
        "Falcon", "Otter", "Raven", "Badger", "Jaguar", "Cheetah", "Bison", "Stallion"
    ]
    number = random.randint(1, 100)
    
    # Generate a random username by combining an adjective, an animal, and a number
    return f"{random.choice(adjectives)}{random.choice(animals)}{number}"


#########################################
######## End of Shin's New Code #########
#########################################




def force_user_leave(user_id, blocked=True):
	global active_elsewhere
	with db.modifyUser(id=user_id) as user:
		user.setLeft()
		if shared_db is not None:
			shared_db.user_left_chat(user.id)
			active_elsewhere = get_users_active_elsewhere(shared_db, config)
	if blocked:
		logging.info("Force leaving %s because bot is blocked", user)
	Sender.stop_invoked(user)

@requireUser
def user_leave(user):
	force_user_leave(user.id, blocked=False)
	logging.info("%s left chat", user)

	return rp.Reply(rp.types.CHAT_LEAVE, bot_name=bot_name)

#SHIN UPDATE: Added chat_username to user info

@requireUser
def get_info(user):
	params = {
		"id": user.getObfuscatedId(),
		"username": user.getFormattedName(),
		"chat_username": user.chat_username,
		"rank_i": user.rank,
		"rank": RANKS.reverse[user.rank],
		"karma_is_pats": karma_is_pats,
		"karma": user.karma,
		"karmalevel": getKarmaLevelName(user.karma),
		"warnings": user.warnings,
		"warnExpiry": user.warnExpiry,
		"cooldown": user.cooldownUntil if user.isInCooldown() else None,
	}
	return rp.Reply(rp.types.USER_INFO, **params)

@requireUser
@requireRank(RANKS.mod)
def get_info_mod(user, msid):
	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	user2 = db.getUser(id=cm.user_id)
	params = {
		"id": user2.getObfuscatedId(),
		"rank_i": user2.rank,
		"rank": RANKS.reverse[user2.rank],
		"karma": user2.karma if user.rank >= RANKS.admin else user2.getObfuscatedKarma(),
		"karma_is_pats": karma_is_pats,
		"karma_obfuscated": user.rank < RANKS.admin,
		"warnings": user2.warnings,
		"warnExpiry": user2.warnExpiry,
		"cooldown": user2.cooldownUntil if user2.isInCooldown() else None,
	}
	return rp.Reply(rp.types.USER_INFO_MOD, **params)

@requireUser
def get_karma_info(user):
	karma = user.karma
	karma_level = getKarmaLevel(karma)
	next_level_karma = KARMA_LEVELS[karma_level] if karma_level < len(KARMA_LEVELS) else None
	params = {
		"karma": karma,
		"karma_is_pats": karma_is_pats,
		"level_name": getKarmaLevelName(karma),
		"level_karma": KARMA_LEVELS[karma_level - 1] if karma_level > 0 else None,
		"next_level_name": getKarmaLevelName(next_level_karma) if next_level_karma is not None else "???",
		"next_level_karma": next_level_karma
	}
	return rp.Reply(rp.types.KARMA_INFO, **params)

@requireUser
@requireRank(RANKS.admin)
def get_bot_info(user):
	params = {
		"python_ver": sys.version,
		"os": sys.platform,
		"last_file_mod":
			max(
				getLastModFile(),
				getLastModFile("src"),
				getLastModFile("util"),
				key=lambda file: file["last_mod"]
			)["last_mod"],
		"launched": launched,
		"time": format_datetime(datetime.now(), True),
		"cached_msgs": len(ch.msgs),
		"active_users": getRecentlyActiveUsers()
	}
	return rp.Reply(rp.types.BOT_INFO, **params)

@requireUser
def get_users(user):
	active, inactive, black, cooldown = 0, 0, 0, 0
	for user2 in db.iterateUsers():
		if user2.isBlacklisted():
			black += 1
		elif not user2.isJoined():
			inactive += 1
		else:
			active += 1
		if user2.isInCooldown():
			cooldown += 1
	if user.rank < RANKS.mod:
		return rp.Reply(rp.types.USERS_INFO,
        	active=active, inactive=inactive + black, total=active + inactive + black)
	return rp.Reply(rp.types.USERS_INFO_EXTENDED,
		active=active, inactive=inactive + black, blacklisted=black,
		total=active + inactive + black, cooldown=cooldown)

@requireUser
@requireRank(RANKS.admin)
def set_commands_dict(user, arg):
	cmds = {
		cmd[0].strip().lower(): cmd[1].strip() for cmd in [
			cmd.split("-", 1) for cmd in arg.split("\n")
			if cmd.strip() != ""
		]
	}
	return cmds

@requireUser
def get_rules(user):
	motd = db.getSystemConfig().motd
	if motd == "": return
	return rp.Reply(rp.types.CUSTOM, text=motd)

@requireUser
@requireRank(RANKS.admin)
def set_rules(user, arg):
	with db.modifySystemConfig() as config:
		config.motd = arg
	logging.info("%s set rules to: %r", user, arg)
	return rp.Reply(rp.types.SUCCESS_RULES, bot_name=bot_name)

@requireUser
def toggle_debug(user):
	with db.modifyUser(id=user.id) as user:
		user.debugEnabled = not user.debugEnabled
		new = user.debugEnabled
	return rp.Reply(rp.types.BOOLEAN_CONFIG, description="Debug mode", enabled=new)

@requireUser
def toggle_karma(user):
	with db.modifyUser(id=user.id) as user:
		user.hideKarma = not user.hideKarma
		new = user.hideKarma
	return rp.Reply(rp.types.BOOLEAN_CONFIG, description=("Pat" if karma_is_pats else "Karma") + " notifications", enabled=not new)

@requireUser
def get_tripcode(user):
	if not enable_signing:
		return rp.Reply(rp.types.ERR_COMMAND_DISABLED)

	return rp.Reply(rp.types.TRIPCODE_INFO, tripcode=user.tripcode)

@requireUser
def set_tripcode(user, text):
	if not enable_signing:
		return rp.Reply(rp.types.ERR_COMMAND_DISABLED)

	if not (0 < text.find("#") < len(text) - 1):
		return rp.Reply(rp.types.ERR_INVALID_TRIP_FORMAT)
	if "\n" in text or len(text) > 30:
		return rp.Reply(rp.types.ERR_INVALID_TRIP_FORMAT)

	with db.modifyUser(id=user.id) as user:
		user.tripcode = text
	tripname, tripcode = genTripcode(user.tripcode)
	return rp.Reply(rp.types.TRIPCODE_SET, tripname=tripname, tripcode=tripcode)

@requireUser
@requireRank(RANKS.admin)
def promote_user(user, username2, rank):
	user2 = getUserByName(username2)
	if user2 is None:
		return rp.Reply(rp.types.ERR_NO_USER)

	if user2.rank >= rank:
		return
	with db.modifyUser(id=user2.id) as user2:
		user2.rank = rank
	if rank >= RANKS.admin:
		_push_system_message(rp.Reply(rp.types.PROMOTED_ADMIN), who=user2)
	elif rank >= RANKS.mod:
		_push_system_message(rp.Reply(rp.types.PROMOTED_MOD), who=user2)
	logging.info("%s was promoted by %s to: %d", user2, user, rank)
	return rp.Reply(rp.types.SUCCESS)

@requireUser
@requireRank(RANKS.mod)
def send_mod_message(user, arg):
	text = arg + " ~<b>mods</b>"
	m = rp.Reply(rp.types.CUSTOM, text=text)
	_push_system_message(m)
	logging.info("%s sent mod message: %s", user, arg)

@requireUser
@requireRank(RANKS.admin)
def send_admin_message(user, arg):
	text = arg + " ~<b>admins</b>"
	m = rp.Reply(rp.types.CUSTOM, text=text)
	_push_system_message(m)
	logging.info("%s sent admin message: %s", user, arg)

@requireUser
@requireRank(RANKS.mod)
def warn_user(user, msid, delete=False, del_all=False, duration=""):
	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	d = None
	if not cm.warned:
		with db.modifyUser(id=cm.user_id) as user2:
			if duration == "":
				d = user2.addWarning()
			else:
				cooldown = {
					"seconds": 0,
					"minutes": 0,
					"hours": 0,
					"days": 0,
					"weeks": 0
				}
				cooldown_keys = {
					"s": "seconds",
					"m": "minutes",
					"h": "hours",
					"d": "days",
					"w": "weeks",
					"sec": "seconds",
					"min": "minutes"
				}
				i = 0
				while i < len(duration):
					n = ""
					while (i < len(duration)) and (duration[i] == " "):
						i += 1
					while (i < len(duration)) and (duration[i] >= "0") and (duration[i] <= "9"):
						n += duration[i]
						i += 1
					while (i < len(duration)) and (duration[i] == " "):
						i += 1
					if not (duration[i].lower() in cooldown_keys):
						return rp.Reply(rp.types.ERR_INVALID_DURATION)
					key = cooldown_keys[duration[i]]
					if (cooldown[key] != 0) or not n.isnumeric():
						return rp.Reply(rp.types.ERR_INVALID_DURATION)
					cooldown[key] = int(n)
					i += 1
				d = user2.addWarning(timedelta(**cooldown))
			user2.karma -= KARMA_WARN_PENALTY
		_push_system_message(
			rp.Reply(rp.types.GIVEN_COOLDOWN, duration=d, deleted=delete),
			who=user2, reply_to=msid)
		cm.warned = True
	else:
		user2 = db.getUser(id=cm.user_id)
		if not delete: # allow deleting already warned messages
			return rp.Reply(rp.types.ERR_ALREADY_WARNED)
	if delete:
		if del_all:
			msgs = ch.getMessages(cm.user_id)
			for cm2 in msgs:
				Sender.delete([cm2])
			if d is not None:
				logging.info("%s warned %s (cooldown: %s) and deleted all %d messages", user, user2.getObfuscatedId(), d, len(msgs))
			else:
				logging.info("%s warned %s and deleted all %d messages", user, user2.getObfuscatedId(), len(msgs))
			return rp.Reply(rp.types.SUCCESS_WARN_DELETEALL, id=user2.getObfuscatedId(), count=len(msgs), cooldown=d)
		else:
			Sender.delete([msid])
			if d is not None:
				logging.info("%s warned %s (cooldown: %s) and deleted a message", user, user2.getObfuscatedId(), d)
			else:
				logging.info("%s warned %s and deleted a message", user, user2.getObfuscatedId())
			return rp.Reply(rp.types.SUCCESS_WARN_DELETE, id=user2.getObfuscatedId(), cooldown=d)
	else:
		if d is not None:
			logging.info("%s warned %s (cooldown: %s)", user, user2.getObfuscatedId(), d)
		else:
			logging.info("%s warned %s", user, user2.getObfuscatedId())
		return rp.Reply(rp.types.SUCCESS_WARN, id=user2.getObfuscatedId(), cooldown=d)

@requireUser
@requireRank(RANKS.mod)
def delete_message(user, msid, del_all=False):
	if not allow_remove_command:
		return rp.Reply(rp.types.ERR_COMMAND_DISABLED)

	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	user2 = db.getUser(id=cm.user_id)

	if del_all:
		msgs = ch.getMessages(user2.id)
		for cm2 in msgs:
			Sender.delete([cm2])
		logging.info("%s deleted all messages from %s", user, user2.getObfuscatedId())
		return rp.Reply(rp.types.SUCCESS_DELETEALL, id=user2.getObfuscatedId(), count=len(msgs))
	else:
		_push_system_message(rp.Reply(rp.types.MESSAGE_DELETED), who=user2, reply_to=msid)
		Sender.delete([msid])
		logging.info("%s deleted a message from %s", user, user2.getObfuscatedId())
		return rp.Reply(rp.types.SUCCESS_DELETE, id=user2.getObfuscatedId())

@requireUser
@requireRank(RANKS.admin)
def cleanup_messages(user):
	msids = []
	def f(msid: int, cm: CachedMessage):
		if cm.user_id is None:
			return
		if 1337 in cm.upvoted: # mark that we've been here before
			return
		user2 = db.getUser(id=cm.user_id)
		if user2.isBlacklisted():
			msids.append(msid)
			cm.upvoted.add(1337)
	ch.iterateMessages(f)
	logging.info("%s invoked cleanup (matched: %d)", user, len(msids))
	Sender.delete(msids)
	return rp.Reply(rp.types.DELETION_QUEUED, count=len(msids))

@requireUser
@requireRank(RANKS.admin)
def uncooldown_user(user, oid2=None, username2=None):
	if oid2 is not None:
		user2 = getUserByOid(oid2)
		if user2 is None:
			return rp.Reply(rp.types.ERR_NO_USER_BY_ID)
	elif username2 is not None:
		user2 = getUserByName(username2)
		if user2 is None:
			return rp.Reply(rp.types.ERR_NO_USER)
	else:
		raise ValueError()

	if not user2.isInCooldown():
		return rp.Reply(rp.types.ERR_NOT_IN_COOLDOWN)
	with db.modifyUser(id=user2.id) as user2:
		user2.removeWarning()
		was_until = user2.cooldownUntil
		user2.cooldownUntil = None
	logging.info("%s removed cooldown from %s (was until %s)", user, user2, format_datetime(was_until))
	return rp.Reply(rp.types.SUCCESS)

@requireUser
@requireRank(RANKS.mod)
def blacklist_user(user, msid, reason, del_all=False, univ=False):
	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	with db.modifyUser(id=cm.user_id) as user2:
		if user2.rank >= user.rank and not univ:
			return
		user2.setBlacklisted(reason)
	cm.warned = True
	Sender.stop_invoked(user2, True) # do this before queueing new messages below
	_push_system_message(
		rp.Reply(rp.types.ERR_BLACKLISTED, reason=reason, contact=blacklist_contact),
		who=user2, reply_to=msid)
	
	#SHIN-PROVEMENT: Record ban to universal database
	if shared_db is not None:
		global blacklisted
		shared_db.universal_ban_user(user2.id)
		blacklisted.add(user2.id)

	if del_all:
		msgs = ch.getMessages(cm.user_id)
		for cm2 in msgs:
			Sender.delete([cm2])
		logging.info("%s was blacklisted by %s and all his messages were deleted for: %s", user2, user, reason)
		return rp.Reply(rp.types.SUCCESS_BLACKLIST_DELETEALL, id=user2.getObfuscatedId(), count=len(msgs))
	else:
		Sender.delete([msid])
		logging.info("%s was blacklisted by %s for: %s", user2, user, reason)
		return rp.Reply(rp.types.SUCCESS_BLACKLIST, id=user2.getObfuscatedId())


@requireUser
@requireRank(RANKS.mod)
def whitelist_user(user, msid):
	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	with db.modifyUser(id=cm.user_id) as user2:
		if user2.rank >= user.rank:
			return
		shared_db.whitelist_user(user2.id)
	logging.info("%s was whitelisted by %s", user2, user)
	return rp.Reply(rp.types.SUCCESS_WHITELIST, id=user.getObfuscatedId())


@requireUser
def modify_karma(user, msid, amount):
	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)
	params = {"karma_is_pats": karma_is_pats}
	if cm.hasUpvoted(user):
		return rp.Reply(rp.types.ERR_ALREADY_VOTED_UP, **params)
	if cm.hasDownvoted(user):
		return rp.Reply(rp.types.ERR_ALREADY_VOTED_DOWN, **params)
	if user.id == cm.user_id:
		return rp.Reply(rp.types.ERR_VOTE_OWN_MESSAGE, **params)
	if amount > 0:
		# enforce upvoting cooldown
		if vote_up_interval.total_seconds() > 1:
			last_used = vote_up_last_used.get(user.id, None)
			if last_used and (datetime.now() - last_used) < vote_up_interval:
				return rp.Reply(rp.types.ERR_SPAMMY_VOTE_UP, **params)
			vote_up_last_used[user.id] = datetime.now()

		cm.addUpvote(user)
	elif amount < 0:
		# enforce downvoting cooldown
		if vote_down_interval.total_seconds() > 1:
			last_used = vote_down_last_used.get(user.id, None)
			if last_used and (datetime.now() - last_used) < vote_down_interval:
				return rp.Reply(rp.types.ERR_SPAMMY_VOTE_DOWN, **params)
			vote_down_last_used[user.id] = datetime.now()

		cm.addDownvote(user)
	else:
		return

	user2 = db.getUser(id=cm.user_id)
	with db.modifyUser(id=cm.user_id) as user2:
		old_level = getKarmaLevel(user2.karma)
		user2.karma += KARMA_PLUS_ONE * amount
		new_level = getKarmaLevel(user2.karma)
	if not user2.hideKarma:
		_push_system_message(rp.Reply(rp.types.KARMA_NOTIFICATION, count=amount, **params), who=user2, reply_to=msid)
		if old_level < new_level:
			_push_system_message(rp.Reply(rp.types.KARMA_LEVEL_UP, level=getKarmaLevelName(user2.karma), **params), who=user2)
		if old_level > new_level:
			_push_system_message(rp.Reply(rp.types.KARMA_LEVEL_DOWN, level=getKarmaLevelName(user2.karma), **params), who=user2)
	if amount > 0:
		return rp.Reply(rp.types.KARMA_VOTED_UP, bot_name=bot_name, **params)
	elif amount < 0:
		return rp.Reply(rp.types.KARMA_VOTED_DOWN, bot_name=bot_name, **params)

@requireUser
def prepare_user_message(user: User, msg_score, *, is_media=False, signed=False, tripcode=False, ksigned=False):
	# prerequisites
	if user.isInCooldown():
		return rp.Reply(rp.types.ERR_COOLDOWN, until=user.cooldownUntil)
	if (signed or tripcode or ksigned) and not enable_signing:
		return rp.Reply(rp.types.ERR_COMMAND_DISABLED)
	if tripcode and user.tripcode is None:
		return rp.Reply(rp.types.ERR_NO_TRIPCODE)
	if is_media and user.rank < RANKS.mod and media_limit_period is not None:
		if (datetime.now() - user.joined) < media_limit_period:
			return rp.Reply(rp.types.ERR_MEDIA_LIMIT, media_limit_period=media_limit_period)


	# SHIN UPDATE - removed SPAM enforcement

	#ok = spam_scores.increaseSpamScore(user.id, msg_score)
	#if not ok:
	#	return rp.Reply(rp.types.ERR_SPAMMY)

	# enforce signing cooldown
	if (signed or ksigned) and sign_interval.total_seconds() > 1:
		last_used = sign_last_used.get(user.id, None)
		if last_used and (datetime.now() - last_used) < sign_interval:
			return rp.Reply(rp.types.ERR_SPAMMY_SIGN)
		sign_last_used[user.id] = datetime.now()

	return ch.assignMessageId(CachedMessage(user.id))

# who is None -> to everyone except the user <except_who> (if applicable)
# who is not None -> only to the user <who>
# reply_to: msid the message is in reply to
def _push_system_message(m, *, who=None, except_who=None, reply_to=None):
	msid = None
	if who is None: # we only need an ID if multiple people can see the msg
		msid = ch.assignMessageId(CachedMessage())
	Sender.reply(m, msid, who, except_who, reply_to)
