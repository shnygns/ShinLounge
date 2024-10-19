# CatLounge
Fork of [*secretlounge-ng*](https://github.com/secretlounge/secretlounge-ng), a bot to make an anonymous group chat on Telegram.

CLONED FROM https://github.com/CatLounge/Catlounge

## Changes
You can find a general list of modifications in our sequencially-updated [changelog](changelog.txt) document. This however only includes a selection of the most fundamental changes without much detail. Please see our [commit history](../../compare) for more detailed information on what has been changed.

From within the bot, you can access a prettified version of the changelog file with the `/changelog` command. It lists the changes of the past three releases by default.

## Setup
### Requirements
Make sure you match the system requirements to use this bot framework:
* Linux or Windows* operating system
* Python 3 with PIP
* Bot token from BotFather

*) Windows OS support is still in beta. It should generally work as long as tripcodes are not used.

### Linux setup
1. Enter the following commands to set up the bot environent:
   ```bash
   $ pip3 install -r requirements.txt
   $ cp config.yaml.example config.yaml
   ```
   If the first command fails, you may have to manually install the Python modules listed in `requirements.txt`.
2. Edit `config.yaml` with your favorite text editor
3. Enter the following command to start the bot:
   ```bash
   $ python ./secretlounge-ng
   ```
   Note that on some systems you need to type `python3` instead of `python`

### Windows setup
1. Enter the following commands to set up the bot environent:
   ```bat
   > pip3 install -r requirements.txt
   > copy config.yaml.example config.yaml
   ```
   If the first command fails, you may have to manually install the Python modules listed in `requirements.txt`.
2. Edit `config.yaml` with your favorite text editor
3. Enter the following command to start the bot:
   ```bat
   > python secretlounge-ng
   ```
   Note that on some systems you need to type `python3` instead of `python` or use the absolute path if you have not updated your `PATH` system variable

### BotFather setup
Message [@BotFather](https://t.me/BotFather) to configure your bot as follows:
* `/setprivacy`: enabled
* `/setjoingroups`: disabled

### Commands list
If you want, you can also set the command list with BotFather, by using: `/setcommands <COMMANDS>`. We recommend using the list below, but you can customize them the way you want, of course:
```
start - Join the chat
stop - Leave the chat
help - Show all available commands
info - Get info about your account
ks - Sign with your karma level
users - Show current user count
remove - Delete a message [mod]
```
Please keep in mind that if you decided to enable `karma_is_pats` in the configurations, you may want to change the `ks` into `ps` here.

Our bot also supports a commands setup with `/setup_commands`. This allows you to set up the commands list from within the bot if you have not already defined any commands. If you need to modify your commands list, please use either BotFather's `/setcommands` or your bot's `/commands` command; both work nearly identical.

### Starting the bot
Once the bot is running, you can use a telegram client to connect to your bot. The first person that connects automatically becomes an admin. Thereby, it is important that you do not publish the bot URL before first entering it. If you are the first one to join, you should get a nottification message confirming you have been made an automatic admin. Additional admins and mods may be promoted using the `/admin` and `/mod` commands. We recommend defining a welcome message with rules, too, using `/rules <TEXT>`.

## Contact
You can contact us at any time via our [support bot](https://t.me/catloungesupportrobot). If you find something missing or if you encounter bugs, please [open an issue](../../issues/new).
