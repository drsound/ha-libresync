# LibreSync for Home Assistant

Control Libre Wireless / LibreSync audio devices from Home Assistant over the local network, with no cloud, and the vendor's app only for the hub's initial setup. The Platin Stereo Hub is the reference device; the same stack ships under at least a dozen brands.

Everything happens on your LAN, over the two TCP ports the hardware already speaks. Nothing leaves the house.

## What you get

| Entity | What it does |
| --- | --- |
| `media_player` | playback state, source selection, transport, volume, mute, track metadata and artwork |
| `switch` Power | the hub's power, which is a stop rather than a mains switch — see below |
| `switch` Room correction | on/off, once you have run the calibration in the vendor's app |
| `switch` Manual EQ | on/off, independent of room correction |
| `select` EQ preset | choose among the three presets you built in the app. While the app's EQ editor has a curve open, it reads *Being edited in the app*, and that option goes away as soon as a preset is chosen again |
| `binary_sensor` Audio | whether sound is actually coming out of the speakers |
| Diagnostics | a redacted dump for bug reports |

It is **push-driven**. The hub announces volume, source, transport and metadata as they happen, so the interface follows within a second or so. A slow poll covers the two properties nothing announces.

## Installing

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=drsound&repository=ha-libresync&category=integration)

Through HACS: the button above adds this repository to HACS for you; otherwise add it by hand as a custom repository of type *Integration*. Install it, and restart Home Assistant. Your hub should appear by itself under **Settings → Devices & Services**; if it does not, add it with **Add integration → LibreSync** and type its address.

Home Assistant installs the one library this needs, [`aiolibresync`](https://pypi.org/project/aiolibresync/), from PyPI by itself.

## Three things that will look like bugs and are not

**The media player has no on/off button.** The hub's "power" is not mains power. Switching it off is a *stop*: it terminates the session, and switching it back on restores nothing — it only permits playing again. On the way up it is the *last* thing to happen, arriving after the music has already started, as a consequence rather than a cause. A media player carrying that would show "off" while music played, so power is a separate switch.

**Mute is simulated.** The hub has no mute you can set: the command exists, is accepted, changes the value that reads back, and leaves the audio alone. So muting here saves your volume, sets it to zero, and puts it back afterwards. If something *else* mutes the hub — the remote, the vendor's app — the integration will show it as muted and will not be able to unmute it, and it says so in the log.

**A hub that will not say who it is cannot be added.** A hub is identified by the identity its UPnP service publishes on port 38400. Adding a hub by address reads it once, and discovery sees it on its own. If that service does not answer, check that nothing blocks the port, then unplug the hub from the mains and plug it back in: the service comes back with it. On the Platin Stereo Hub it has been seen to stop once, on an otherwise healthy hub.

## What it does not do

Seeking, media browsing, speaker grouping and multiroom. Defining EQ filters — build your presets in the vendor's app and select them here. Running the room calibration.

## Tested hardware

| Device | Firmware | Status |
| --- | --- | --- |
| Platin Stereo Hub | 1.52 | everything above, measured |

That is the whole list so far. The same hub is sold as the System Audio, Buchardt and Econik Stereo Hubs and the Triangle Compact Stereo Hub, Mesanovic bundles Platin's with its monitors, and Platin and others make HT and Surround versions. The platform probably ships under other names too. They should work, but nobody has checked yet.

**If you own one of them, please try it** and [open a device report](https://github.com/drsound/ha-libresync/issues/new?template=device_report.yml), whether it works or not. Attach the diagnostics: **Settings → Devices & Services → LibreSync → ⋮ → Download diagnostics**. The file hides your hub's address, its serial number and the track you are playing, and it holds what is needed to fix a hub nobody here owns.

## Where the rest of it is

The protocol documentation and the library live in the [`aiolibresync`](https://github.com/drsound/aiolibresync) project. Every claim in this file was measured against real hardware rather than inferred.

MIT licensed.
