# Home Assistant Community App: UniFi OS Server

This app runs Ubiquiti's UniFi OS Server, which includes the UniFi Network
application, so you can manage your UniFi network from a web browser and from
Home Assistant.

It replaces the standalone UniFi Network Application that earlier versions of
this app ran. The app keeps the same identity (`unifi`), so updating it takes
your existing installation along: on its first start, the app prepares your
existing data and installs UniFi OS Server, and Ubiquiti's setup wizard then
imports your Network configuration.

> [!WARNING]
> Updating from an earlier version of this app (the UniFi Network
> Application) **cannot be undone** except by restoring a Home Assistant
> backup of the app made **before** the update. The previous app cannot read
> the upgraded database.
>
> - Make a Home Assistant backup of this app before updating.
> - If you sign in to UniFi with a local account (not a Ubiquiti account),
>   download a `.unf` backup from the current UniFi Network Application
>   **before** updating. You need it to bring your configuration across.
> - UniFi OS Server needs much more memory and disk space than the previous
>   app. Check the [requirements](#requirements) first.
> - Home Assistant will not install this update automatically, even with
>   automatic updates turned on. You need to start it yourself.

## What changes in this version

- The app now runs UniFi OS Server 5.1.42, installed with Ubiquiti's official
  installer. The installer is unmodified and is checked against a fixed
  SHA-256 fingerprint when the app is built. UniFi OS runs inside the app with
  its own service manager (systemd) and rootless Podman.
- On the first start with existing data, the app upgrades the retained UniFi
  Network database from MongoDB 3.6 to 4.4. Before it touches the database it
  stores a verified copy of the original files inside the app's data.
- Your Network configuration is imported by Ubiquiti's own setup wizard, not
  by the app.
- The web interface now opens inside Home Assistant (ingress), and the
  UniFi OS console has a new port, 11443. The old ports keep working.
- Problems that stop the app from starting are reported as Home Assistant
  notifications.

## Requirements

- Home Assistant OS or a Supervised installation with cgroup v2 and AppArmor
  enabled and enforcing, and `/dev/net/tun` available.
- A 64-bit system: `aarch64` or `amd64`.
- Memory: at least 4 GB of RAM in the Home Assistant host, 8 GB preferred.
  In testing, an existing installation peaked at about 3.4 GiB of memory for
  this app alone. Home Assistant and your other apps need memory on top of
  that.
- Disk: at least 25 GB of free space. The first start keeps a full copy of
  your previous UniFi data, UniFi OS itself needs several gigabytes, and every
  backup checkpoint needs room next to the previous one.
- Internet access during setup, for signing in to a Ubiquiti account and for
  downloads made by the setup wizard.

If a check of the container environment fails (for example AppArmor is not
enforcing), the app does not start and shows a notification with the reason.

## Before you update

1. **Make a backup of the app.** In Home Assistant, go to **Settings** →
   **System** → **Backups** and create a backup that includes the UniFi app
   (or turn on the option to back up before updating in the update dialog).
   This backup, made with the previous version still installed, is the only
   way back. Copy it off the Home Assistant host if you can.
1. **Local-account users: export a `.unf` backup.** If you do not sign in to
   UniFi with a Ubiquiti account, open the current UniFi Network Application
   and download a backup from **Settings** → **Control Plane** → **Backups**
   (older versions: **Settings** → **System** → **Backups**). Keep the file
   on your computer. After the update you cannot create it any more.
1. **Check free memory and disk space** against the
   [requirements](#requirements).
1. **Check the new ports.** The update maps three additional host ports by
   default: `11443/tcp`, `8444/tcp` and `10003/udp`. If another service on
   your Home Assistant host already uses one of them, the first start after the
   update fails (see [Ports](#ports)). You cannot change these mappings before
   updating, because the current version does not have them yet.

## Updating

Open the app in **Settings** → **Apps** and select **Update**. Then watch the
**Log** tab of the app. On the first start the app:

1. Checks the container environment (cgroup v2, AppArmor, `/dev/net/tun`).
1. Attaches your existing UniFi Network data where UniFi OS expects it.
1. Saves a verified copy of your original data and upgrades the database
   step by step from MongoDB 3.6 to 4.4, validating every collection after
   each step.
1. Runs Ubiquiti's UniFi OS Server installer.

This usually takes several minutes, and can take much longer on slow hardware
or with a large database. The app allows up to 45 minutes before it treats the
start as failed. Do not stop or restart the app while this runs. Later starts
are much faster, because these steps only run once.

If a step fails, the app stops itself and shows a notification explaining what
happened. See [Troubleshooting](#troubleshooting).

### New installation

1. Click the Home Assistant My button below to open the app on your Home
   Assistant instance.

   [![Open this app in your Home Assistant instance.][app-badge]][app]

1. Click the "Install" button to install the app.
1. Start the app and watch its **Log** tab. The first start installs UniFi OS
   Server and takes several minutes.
1. Set up UniFi OS as described below.

## First-run setup and import

Until UniFi OS has been set up, the app shows a Home Assistant notification
titled **UniFi OS setup needed** (or **UniFi Network migration setup needed**
when your previous Network data was found). Select **Open UniFi Web UI** in the
notification, or **Open Web UI** on the app page, and follow Ubiquiti's setup
wizard.

How your previous configuration gets imported depends on how you sign in:

- **Ubiquiti account:** sign in with your Ubiquiti account in the wizard. When
  the wizard offers to migrate or import your existing UniFi Network data,
  accept it. The wizard then restores your configuration.
- **Local account:** the wizard's built-in import is only available after
  signing in with a Ubiquiti account. Instead, complete the setup, then open
  the Network application and restore the `.unf` file you exported before the
  update, in **Settings** → **Control Plane** → **Backups**. Ubiquiti describes
  this in [Backups and Migration in UniFi][ubiquiti-migration].

The app never imports, restores or creates accounts by itself.

After the import, check your sites, networks, Wi-Fi, administrators and
devices before you rely on the new installation. Devices keep talking to the
controller on port `8080`, so they normally reconnect without changes.

The notification disappears by itself once UniFi OS reports that setup is
complete. It does not track a later `.unf` restore.

## Accessing the console

Ingress is always enabled: **Open Web UI**, the sidebar entry (turn on
**Show in sidebar** on the app's **Info** tab) and the setup notification all
open UniFi through Home Assistant. What you see depends on how you reach Home
Assistant:

- **Home Assistant over HTTPS:** the UniFi console opens inside Home Assistant.
  This is the recommended setup.
- **Home Assistant over plain HTTP:** UniFi's login only works over HTTPS, so
  Home Assistant shows a short page that opens the UniFi console directly over
  HTTPS, on the same hostname you use for Home Assistant. If your browser blocks
  that, select the **Open secure UniFi login** link on the page.

You still sign in to UniFi with your UniFi credentials. Being logged in to Home
Assistant does not log you in to UniFi.

### Direct console

You can always open the console directly at `https://<your Home Assistant
host>:11443` (or `:8443`, the previous port). UniFi OS uses a self-signed
certificate, so your browser shows a certificate warning the first time.

When Home Assistant is on HTTP, the page above picks the direct console port
from the app's network settings, in this order:

1. The host port for `11443/tcp`, if you changed it from the default.
1. The host port for `8443/tcp`, if you changed it from the default.
1. `11443`, if that mapping is enabled.
1. `8443`, if that mapping is enabled.

If both mappings are disabled, it uses `11443`, which then only works if you
expose that port some other way. The port is read when the app starts; if it
could not be read yet, the page offers **Retry**.

### Reverse proxies

If Home Assistant sits behind a reverse proxy that handles HTTPS, Home
Assistant must know the original request was HTTPS: the proxy must send
`X-Forwarded-Proto: https`, and Home Assistant must trust the proxy
(`use_x_forwarded_for` and `trusted_proxies` in its `http` configuration).
Otherwise you get the HTTP behaviour described above, or a page with a link
to the secure console.

### Opening the console inside Home Assistant over HTTP

The `ingress_allow_insecure_http` option (off by default) lets the console open
inside Home Assistant even when you use plain HTTP. To make that work, the app
removes the `Secure` protection from UniFi's login and session cookies for
these requests.

**Risk:** with this option on, your UniFi password and session travel
unencrypted between your browser and Home Assistant. Anyone who can watch that
network traffic can take over your UniFi session. Only use it on a network you
fully trust, and prefer setting up HTTPS for Home Assistant instead. The option
does not change the direct HTTPS console.

## Ports

Change or disable port mappings in the **Network** section of the app's
**Configuration** tab. "Off" means the mapping exists but is disabled by
default.

| Port        | Default | Purpose                                                             |
| ----------- | ------- | ------------------------------------------------------------------- |
| `11443/tcp` | 11443   | UniFi OS console (HTTPS web interface)                              |
| `8443/tcp`  | 8443    | Previous console port, relayed to `11443/tcp`                       |
| `8080/tcp`  | 8080    | Device inform: how UniFi devices talk to the controller             |
| `3478/udp`  | 3478    | STUN                                                                |
| `10003/udp` | 10003   | UniFi OS device discovery                                           |
| `10001/udp` | 10001   | Previous discovery port, relayed to `10003/udp`                     |
| `8444/tcp`  | 8444    | HTTPS guest (captive) portal                                        |
| `8843/tcp`  | 8843    | Previous HTTPS guest portal port, relayed to `8444/tcp`             |
| `8880/tcp`  | 8880    | HTTP guest (captive) portal                                         |
| `6789/tcp`  | 6789    | UniFi mobile speed test                                             |
| `5514/udp`  | 5514    | Remote syslog from UniFi devices                                    |
| `9543/tcp`  | Off     | UniFi Identity Hub                                                  |
| `5671/tcp`  | Off     | Other UniFi OS service                                              |
| `8881/tcp`  | Off     | Other UniFi OS service                                              |
| `8882/tcp`  | Off     | Other UniFi OS service                                              |
| `28082/tcp` | Off     | Other UniFi OS service                                              |
| `161/udp`   | Off     | Kept from the previous version (SNMP); the app adds no SNMP service |
| `1900/udp`  | Off     | Kept from the previous version (SSDP); the app adds no SSDP service |

`11443/tcp`, `8444/tcp` and `10003/udp` are UniFi OS Server's own ports and
are new in this version. The previous ports `8443/tcp`, `8843/tcp` and
`10001/udp` stay mapped and are relayed inside the app to those new ports, so
existing bookmarks, firewall rules and devices keep working. Your existing
port settings, including custom host ports, are kept.

**Port conflicts on the first start:** if a newly mapped host port (`11443`,
`8444` or `10003`) is already in use on your Home Assistant host, for example
by another app or because you moved a previous mapping to one of these ports,
the app cannot start. No data is touched in that case. Change or disable the
conflicting mapping in the app's network settings and start the app again.

## Configuration

**Note**: _Remember to restart the app when the configuration is changed._

Example app configuration, with all available options:

```yaml
log_level: info
memory_max: 2048
memory_init: 512
ingress_allow_insecure_http: false
```

**Note**: _This is just an example, don't copy and paste it! Create your own!_

The options `log_level`, `memory_max` and `memory_init` are carried over from
the previous version, so your existing settings remain valid. If an option has
an invalid value, the app does not start and shows a notification.

### Option: `log_level`

The `log_level` option controls the level of log output by the app and can
be changed to be more or less verbose, which might be useful when you are
dealing with an unknown issue. Possible values are:

- `trace`: Show every detail, like all called internal functions.
- `debug`: Shows detailed debug information.
- `info`: Normal (usually) interesting events.
- `warning`: Exceptional occurrences that are not errors.
- `error`: Runtime errors that do not require immediate action.
- `fatal`: Something went terribly wrong. App becomes unusable.

Please note that each level automatically includes log messages from a
more severe level, e.g., `debug` also shows `info` messages. By default,
the `log_level` is set to `info`, which is the recommended setting unless
you are troubleshooting.

### Option: `memory_max`

The maximum Java memory (heap), in MB, for the UniFi Network application inside
UniFi OS. When not set, Ubiquiti's default is used. This does not limit the
memory UniFi OS uses as a whole: its other services, such as its databases,
come on top.

### Option: `memory_init`

The initial Java memory (heap), in MB, for the UniFi Network application inside
UniFi OS. It cannot be larger than `memory_max`. When not set, Ubiquiti's
default is used.

### Option: `ingress_allow_insecure_http`

Default: `false`. When `true`, the UniFi console opens inside Home Assistant
even when you reach Home Assistant over plain HTTP. This sends your UniFi
login and session unencrypted; see
[Opening the console inside Home Assistant over HTTP](#opening-the-console-inside-home-assistant-over-http)
for the risk. The app writes a warning to its log while this is on.

## Backups and restore

Home Assistant stops this app while it makes a backup of it, and starts it
again afterwards. UniFi is unavailable during that time.

When the app stops, it saves a verified checkpoint archive of the UniFi OS
runtime in its data. The raw UniFi OS runtime directories are left out of
Home Assistant backups, because a plain file copy cannot preserve their
special ownership and file-system details. The checkpoint is included instead,
and the app unpacks it automatically on the first start after a restore.

- If a stop could not finish the checkpoint (for example the stop was
  interrupted, the disk was too full, or UniFi OS had not started
  successfully), the app shows the notification **Make a new backup of
  UniFi OS** on its next start. A backup made during that stop cannot restore
  UniFi OS. Once the app is running again, make a new backup.
- If a restored backup has no usable checkpoint, the app does not start and
  shows **UniFi OS could not be restored from the backup**. Restore a
  different backup of this app.

Backups of this app also contain your previous UniFi Network data and the
copy of the original database, so they are larger than before.

The previous version stored UniFi's automatic backups in `/backup/unifi`. This
version no longer writes there; files already in that folder are left alone.

To restore, go to **Settings** → **System** → **Backups**, select the backup
and restore only this app.

## Rolling back

To return to the previous UniFi Network Application:

1. Stop the app.
1. Go to **Settings** → **System** → **Backups** and select the backup you made
   **before** the update.
1. Restore only the UniFi app from that backup. Home Assistant reinstalls the
   previous version together with its data.
1. Start the app and check that your devices reconnect.

Everything that changed after the update is lost: the UniFi OS setup, accounts
created in UniFi OS, and any Network changes, adopted devices or statistics
recorded since then.

Without a backup from before the update there is no supported way back. The
app keeps a verified copy of your original database files
(`mongo-originals/originals.tar` in its data), but restoring it by hand is not
supported; ask for help before deleting anything.

## Remote access

Remote access through [unifi.ui.com](https://unifi.ui.com) is handled by
UniFi OS Server itself once the console is signed in to your Ubiquiti account.

Earlier versions of this app patched a WebRTC library in the UniFi Network
Application to work around a Cloudflare TURN error (420) that broke remote
access. UniFi OS Server uses a different WebRTC stack and does not include that
library, so the patch is no longer needed and the app does not change it.

If remote access does not work:

- Check that the Home Assistant host's clock is correct.
- Check that outgoing UDP traffic, including port 3478 (STUN), is not blocked
  by your firewall.
- Check in the console that it is signed in to your Ubiquiti account.

## Troubleshooting

Start with the app's **Log** tab. Set `log_level` to `debug` for more detail.
Detailed UniFi Network logs stay inside UniFi OS and are not copied to the app
log.

When a start fails, the app writes the failing step to its log, shows a
persistent Home Assistant notification titled "UniFi OS Server: …" and stops
itself, so Home Assistant shows it as stopped. It does not retry on its own.
Start-failure notifications are removed automatically after the next
successful start.

| Notification                                                                  | What to do                                                                                                                                                                                                                                 |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| UniFi OS Server could not start                                               | A requirement is missing (cgroup v2, AppArmor, `/dev/net/tun`) or an option is invalid. Fix the cause shown, then start the app. No data was changed.                                                                                      |
| Retained UniFi Network data could not be attached                             | The previous data could not be mounted. Check the log; nothing was imported or changed.                                                                                                                                                    |
| Retained UniFi Network database could not be prepared                         | The database upgrade failed, for example because there is not enough free disk space for the copy of the original data. Check the log, fix the cause and start the app again. Once created, a verified copy of the original files is kept. |
| UniFi OS installation did not finish                                          | The installer failed or was interrupted. Restore the backup of this app made before the update or installation.                                                                                                                            |
| An unfinished UniFi OS installation was found                                 | An earlier installation or update was interrupted. Restore the backup of this app made before it; do not delete app data.                                                                                                                  |
| UniFi OS version changed outside the app                                      | UniFi OS was updated from inside the console, or an update was interrupted. Restore the latest backup of this app made before that.                                                                                                        |
| This app version is older than the installed UniFi OS                         | Update the app, or restore the backup that matches this app version.                                                                                                                                                                       |
| UniFi OS files have unsafe ownership / UniFi OS program files are not trusted | Files of the nested UniFi OS have unexpected owners and may have been tampered with. Restore a backup of this app.                                                                                                                         |
| UniFi OS services did not start / UniFi OS failed to start                    | Check the log for the failing step, then start the app again. If it keeps failing, restore the latest backup of this app.                                                                                                                  |
| Make a new backup of UniFi OS                                                 | The last stop could not save a checkpoint. Make a new backup once the app is running.                                                                                                                                                      |
| UniFi OS could not be restored from the backup                                | The restored backup has no usable checkpoint. Restore a different backup of this app.                                                                                                                                                      |
| Unsupported UniFi OS version / Home Assistant Web UI may not work             | The app runs, but the console inside Home Assistant may not work. Use the [direct console](#direct-console).                                                                                                                               |

Other common problems:

- **The app does not start right after updating and Home Assistant reports a
  port that is already in use:** see the port conflicts note under
  [Ports](#ports).
- **Login problems in the console inside Home Assistant:** sign in through the
  [direct console](#direct-console) instead. If that works, the problem is in
  the in-frame view, not your account: do not repeat the import or reset
  passwords because of it.
- **Devices stay disconnected or "adopting":** check that port `8080/tcp` is
  mapped and that the Inform Host setting in the Network application points to
  your Home Assistant host's address.
- **Out of memory or very slow:** check the [requirements](#requirements).

## Known limitations

- The update to UniFi OS Server can only be undone by restoring a backup made
  before it.
- UniFi OS updates arrive only with new versions of this app. Do not update
  UniFi OS from inside the console: the app blocks this, and if the version
  changes anyway, the app refuses to start until you restore a backup.
- The app does not manage, block or test updates of the UniFi Network
  application offered inside UniFi OS. Make a backup of this app before
  installing one.
- Local-account users cannot use the setup wizard's built-in import; use a
  `.unf` backup instead.
- Ubiquiti's import transfers your configuration. Do not expect historical
  statistics to be carried over.
- The console inside Home Assistant relies on details of the UniFi OS 5.1.42
  web interface. The app checks these at every start and warns if they do not
  match; the direct console always works.
- To keep the migration progress visible, the app makes one small change to
  the UniFi OS 5.1.42 setup wizard file. The change is only applied when the
  file matches a known fingerprint, and a copy of the original is kept. No
  other UniFi OS files are changed.
- The broadcast feature of EDU-type access points does not work, because Home
  Assistant cannot map the required port range.

### Security

- The app needs the `SYS_ADMIN` privilege and its own AppArmor profile to run
  UniFi OS Server's containers. This lowers the app's security rating in Home
  Assistant. Do not disable the app's AppArmor profile; the app refuses to
  start without it.
- The app has Home Assistant API access. It uses it only to create and remove
  its own notifications.
- The app's ingress endpoint only accepts connections from the Home Assistant
  Supervisor.

## Changelog & Releases

This repository keeps a change log using [GitHub's releases][releases]
functionality. The format of the log is based on
[Keep a Changelog][keepchangelog].

Releases are based on [Semantic Versioning][semver], and use the format
of `MAJOR.MINOR.PATCH`. In a nutshell, the version will be incremented
based on the following:

- `MAJOR`: Incompatible or major changes.
- `MINOR`: Backwards-compatible new features and enhancements.
- `PATCH`: Backwards-compatible bugfixes and package updates.

## Support

Got questions?

You have several options to get them answered:

- The [Home Assistant Community Apps Discord chat server][discord] for app
  support and feature requests.
- The [Home Assistant Discord chat server][discord-ha] for general Home
  Assistant discussions and questions.
- The Home Assistant [Community Forum][forum].
- Join the [Reddit subreddit][reddit] in [/r/homeassistant][reddit]

You could also [open an issue here][issue] GitHub.

## Authors & contributors

The original setup of this repository is by [Franck Nijhof][frenck].

For a full list of all authors and contributors,
check [the contributor's page][contributors].

## License

MIT License

Copyright (c) 2018-2026 Franck Nijhof

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

[app-badge]: https://my.home-assistant.io/badges/supervisor_addon.svg
[app]: https://my.home-assistant.io/redirect/supervisor_addon/?addon=a0d7b954_unifi&repository_url=https%3A%2F%2Fgithub.com%2Fhassio-addons%2Frepository
[contributors]: https://github.com/hassio-addons/app-unifi/graphs/contributors
[discord-ha]: https://discord.gg/c5DvZ4e
[discord]: https://discord.me/hassioaddons
[forum]: https://community.home-assistant.io/t/home-assistant-community-add-on-unifi-controller/56297?u=frenck
[frenck]: https://github.com/frenck
[issue]: https://github.com/hassio-addons/app-unifi/issues
[keepchangelog]: http://keepachangelog.com/en/1.0.0/
[reddit]: https://reddit.com/r/homeassistant
[releases]: https://github.com/hassio-addons/app-unifi/releases
[semver]: http://semver.org/spec/v2.0.0.htm
[ubiquiti-migration]: https://help.ui.com/hc/en-us/articles/360008976393-Backups-and-Migration-in-UniFi
