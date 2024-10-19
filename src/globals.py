from os import path
from src.util import Enum

# a few utility functions
def escape_html(s):
	ret = ""
	for c in s:
		if c in ("<", ">", "&"):
			c = "&#" + str(ord(c)) + ";"
		ret += c
	return ret

def format_datetime(t, local=False):
	if local:
		return t.strftime("%Y-%m-%d %H:%M")
	else:
		tzinfo = __import__("datetime").timezone.utc
		return t.replace(tzinfo=tzinfo).strftime("%Y-%m-%d %H:%M UTC")

def format_timedelta(d):
	timedelta = __import__("datetime").timedelta
	units = {
	    "w": timedelta(weeks=1),
	    "d": timedelta(days=1),
	    "h": timedelta(hours=1),
	    "m": timedelta(minutes=1)
	}
	remainder = d
	s = ""
	for name, u in units.items():
		if remainder >= u:
			n = remainder // u
			s += (" " if len(s) != 0 else "") + ("%d%c" % (n, name))
			remainder -= u * n
	if remainder.total_seconds() != 0:
		s += (" " if len(s) != 0 else "") + ("%ds" % remainder.total_seconds())
	return s

## for debugging ##
def dump(obj, name=None, r=False):
	name = "" if name is None else (name + ".")
	for e, ev in ((e, getattr(obj, e)) for e in dir(obj)):
		if e.startswith("_") or ev is None:
			continue
		if r and ev.__class__.__name__[0].isupper():
			print("%s%s (%s)" % (name, e, ev.__class__.__name__))
			dump(ev, name + e, r)
		else:
			print("%s%s = %r" % (name, e, ev))

# Program version
VERSION = "1.0"

URL_SECRETLOUNGE = "https://github.com/secretlounge/secretlounge-ng/"
URL_CATLOUNGE = "https://github.com/CatLounge/catlounge-ng-meow/"

# Default commands set
DEFAULT_COMMANDS = {
	"start": "Join the chat",
	"stop": "Leave the chat",
	"help": "Show all available commands",
	"info": "Get info about your account",
	"ps": "Sign with your pat level",
	"users": "Show current user count",
	"remove": "Delete a message [mod]"
}

# File names
FILENAME_CHANGELOG = path.abspath("changelog.txt")

# Ranks
RANKS = Enum({
	"admin": 100,
	"mod": 10,
	"user": 0,
	"banned": -10
})

# Cooldown related
COOLDOWN_TIME_BEGIN = [10, 30, 180, 1440, 4320] # begins with 10m, 30m, 3h, 1d, 3d
COOLDOWN_TIME_LINEAR_M = 4320 # continues 7d, 10d, 13d, 16d, ... (linear)
COOLDOWN_TIME_LINEAR_B = 10080
WARN_EXPIRE_HOURS = 7 * 24

# Karma related
KARMA_PLUS_ONE = 1
KARMA_WARN_PENALTY = 0 # Since we have downvote capability, we don't need/want this
KARMA_LEVELS = [-49, -4, 15, 40, 100, 200, 300, 500] # Minimum karma for different levels

# Spam limits
SPAM_LIMIT = 3
SPAM_LIMIT_HIT = 6
SPAM_INTERVAL_SECONDS = 5

# Spam score calculation
SCORE_STICKER = 1.5
SCORE_PHOTO = 0.3
SCORE_BASE_MESSAGE = 0.75
SCORE_BASE_FORWARD = 1.25
SCORE_TEXT_CHARACTER = 0.002
SCORE_TEXT_LINEBREAK = 0.1
