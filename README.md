# Subsearch

Subsearch is a SubDL subtitle search and download add-on for Kodi 18 on Linux. It has been tested with Kodi 18.9 and LibreELEC 9.2 (Linux 4.19.127) on a Raspberry Pi 3. It may also work on older Raspberry Pi models, such as the Raspberry Pi 1 and 2.

## Motivation

With Kodi 18.9 and LibreELEC, older Raspberry Pi models such as the Raspberry Pi 2 and 3 remain compelling and versatile home media-player platforms, capable of smooth Full HD playback of H.264 and even HEVC/H.265 content.

However, newer Kodi releases dropped support for the legacy Raspberry Pi video acceleration stack, substantially reducing HEVC playback performance on Raspberry Pi 3 and earlier models. As a result, the smooth Full HD HEVC playback achieved with older Kodi and LibreELEC releases is no longer available on many older Pi devices.

Subtitle search and download are essential Kodi features for many users. Since I could not find a SubDL add-on compatible with Kodi 18.9, I decided to create one.

## Features

- Search subtitles from Kodi's on-screen subtitle dialog.
- Searches use the video's filename, preferred languages, and season/episode numbers when available.
- Browse up to 100 results from SubDL.
- Download `.srt` files directly or extract `.srt` files from `.zip` results.
- Save subtitles to a configured folder or, when no folder is set, alongside the video when possible.

## Installation

1. Download service.subsearch.zip from Releases on the right side of this page.
2. Copy `service.subsearch.zip` to a location accessible from Kodi, such as a USB drive or network share.
3. Ensure the ZIP file contains the `service.subsearch` add-on folder, including `addon.xml` and the other add-on files.
4. In Kodi, enable **Unknown sources** under **Settings → System → Add-ons** if required.
5. Open **Add-ons → Install from zip file**, browse to the ZIP file, and select it.
6. Configure the add-on's API key and preferred languages in the add-on settings.

## Usage

During video playback, open Kodi's subtitle dialog, select **Download**, and choose a subtitle from the results.

In some cases, the downloaded file may be an archive with multiple subtitle files. This behavior is intentional, as subtitle packages may contain different versions for the same title or subtitles for multiple episodes in a season. To see what files you got, select the **Browse** option in the subtitle dialog to choose the subtitle file you want to use.

---
JCA (2026)
